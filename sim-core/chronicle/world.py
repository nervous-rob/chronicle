"""chronicle.world — the hex grid: terrain, rivers, regions, neighbors, travel, arrival-time passes.

ECOLOGY F1/F2.  Cells are pointy-top hexes in odd-r offset layout (row y shifted right by half a
cell when y is odd); index i = y * w + x.  Cells carry ecology, travel, claims; buildings (places)
carry co-presence and live elsewhere.  Every rate here is real units converted by cell_km.

Static per cell : terrain, elevation (0..1), slope (m/km), river, region, water_adjacent, river_adjacent
Infrastructure  : road_level (0..3), road_wear, bridge, ferry   (mutated by the build/transit systems)

dijkstra(sources, edge_cost) is the single minimum-travel-time primitive used by both journeys and
the daily fire pass (edge_cost is an (n, 6) array of hours, inf = impassable).
"""
from __future__ import annotations

import heapq
import math
import numpy as np

from .config import Config, TERRAINS
from .rng import Streams

T = {name: i for i, name in enumerate(TERRAINS)}      # water 0, marsh 1, plains 2, forest 3, hills 4, mountains 5, desert 6
SEASONS = ("spring", "summer", "autumn", "winter")

# odd-r offset neighbor deltas, order: W, E, NW, NE, SW, SE
_D_EVEN = ((-1, 0), (1, 0), (-1, -1), (0, -1), (-1, 1), (0, 1))
_D_ODD = ((-1, 0), (1, 0), (0, -1), (1, -1), (0, 1), (1, 1))


# ----------------------------------------------------------------------------- noise

def _value_noise(gen: np.random.Generator, w: int, h: int, period: int) -> np.ndarray:
    lw, lh = w // period + 2, h // period + 2
    lat = gen.random((lh, lw))
    ys, xs = np.mgrid[0:h, 0:w]
    fx, fy = xs / period, ys / period
    x0, y0 = fx.astype(int), fy.astype(int)
    tx, ty = fx - x0, fy - y0
    tx = tx * tx * (3 - 2 * tx)
    ty = ty * ty * (3 - 2 * ty)
    v00, v10 = lat[y0, x0], lat[y0, x0 + 1]
    v01, v11 = lat[y0 + 1, x0], lat[y0 + 1, x0 + 1]
    return (v00 * (1 - tx) + v10 * tx) * (1 - ty) + (v01 * (1 - tx) + v11 * tx) * ty


def _fractal(gen: np.random.Generator, w: int, h: int, period: int, octaves: int) -> np.ndarray:
    out = np.zeros((h, w))
    amp, total = 1.0, 0.0
    for _ in range(octaves):
        out += amp * _value_noise(gen, w, h, max(2, period))
        total += amp
        amp *= 0.5
        period //= 2
        if period < 2:
            break
    out /= total
    return (out - out.min()) / (out.max() - out.min() + 1e-12)


# ----------------------------------------------------------------------------- the grid

class World:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        d = cfg.derived
        self.w, self.h = int(d["grid_w"]), int(d["grid_h"])
        self.n = self.w * self.h
        self.cell_km = float(cfg["units"]["cell_km"])
        self.day_hours = float(cfg["units"]["day_hours"])
        idx = np.arange(self.n)
        self.ys, self.xs = np.divmod(idx, self.w)
        self.x_km = self.cell_km * (self.xs + 0.5 * (self.ys % 2))
        self.y_km = self.cell_km * (math.sqrt(3) / 2) * self.ys
        self.nbr = self._neighbor_table()
        # static
        self.terrain = np.full(self.n, T["plains"], dtype=np.int8)
        self.elevation = np.zeros(self.n, dtype=np.float32)
        self.slope = np.zeros(self.n, dtype=np.float32)       # m per km, max over neighbors
        self.river = np.zeros(self.n, dtype=bool)
        self.region = np.zeros(self.n, dtype=np.int16)
        self.water_adjacent = np.zeros(self.n, dtype=bool)
        self.river_adjacent = np.zeros(self.n, dtype=bool)
        # infrastructure (mutable)
        self.road_level = np.zeros(self.n, dtype=np.int8)
        self.road_wear = np.zeros(self.n, dtype=np.float32)
        self.bridge = np.zeros(self.n, dtype=bool)
        self.ferry = np.zeros(self.n, dtype=bool)
        self._hours_per_plains_cell = self.cell_km / (cfg["transit"]["km_per_day"] / self.day_hours)

    # ------------------------------------------------------------------ topology
    def _neighbor_table(self) -> np.ndarray:
        nbr = np.full((self.n, 6), -1, dtype=np.int32)
        odd = (self.ys % 2) == 1
        for k in range(6):
            dx = np.where(odd, _D_ODD[k][0], _D_EVEN[k][0])
            dy = np.where(odd, _D_ODD[k][1], _D_EVEN[k][1])
            nx, ny = self.xs + dx, self.ys + dy
            ok = (nx >= 0) & (nx < self.w) & (ny >= 0) & (ny < self.h)
            nbr[:, k] = np.where(ok, ny * self.w + nx, -1)
        return nbr

    def cube(self, idx) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        idx = np.asarray(idx)
        y, x = np.divmod(idx, self.w)
        q = x - (y - (y & 1)) // 2
        r = y
        return q, r, -q - r

    def hex_distance(self, a, b) -> np.ndarray:
        qa, ra, sa = self.cube(a)
        qb, rb, sb = self.cube(b)
        return np.maximum.reduce([np.abs(qa - qb), np.abs(ra - rb), np.abs(sa - sb)])

    def neighbors(self, i: int) -> np.ndarray:
        row = self.nbr[i]
        return row[row >= 0]

    def is_land(self) -> np.ndarray:
        return self.terrain != T["water"]

    # ------------------------------------------------------------------ generation
    @classmethod
    def generate(cls, cfg: Config) -> "World":
        self = cls(cfg)
        w = cfg["world"]
        tseed = w["terrain_seed"] or cfg.seed
        gen = Streams(tseed).one("terrain", 0, 0)
        period = max(2, int(round(w["terrain_scale_km"] / self.cell_km)))
        elev = _fractal(gen, self.w, self.h, period, w["terrain_octaves"]).ravel()
        wet = _fractal(gen, self.w, self.h, period, w["terrain_octaves"]).ravel()
        self.elevation[:] = elev

        terrain = np.full(self.n, T["plains"], dtype=np.int8)
        water = elev <= np.quantile(elev, w["water_fraction"]) if w["water_fraction"] > 0 else np.zeros(self.n, bool)
        mount = elev >= np.quantile(elev, 1 - w["mountain_fraction"]) if w["mountain_fraction"] > 0 else np.zeros(self.n, bool)
        mount &= ~water
        terrain[water] = T["water"]
        terrain[mount] = T["mountains"]
        land = ~water & ~mount
        mix = w["land_mix"]

        def _top(mask, field, frac):
            if frac <= 0 or not mask.any():
                return np.zeros(self.n, bool)
            thr = np.quantile(field[mask], 1 - frac)
            return mask & (field >= thr)

        def _bottom(mask, field, frac):
            if frac <= 0 or not mask.any():
                return np.zeros(self.n, bool)
            thr = np.quantile(field[mask], frac)
            return mask & (field <= thr)

        hills = _top(land, elev, mix["hills"])
        terrain[hills] = T["hills"]
        rest = land & ~hills
        marsh = _top(rest, wet, mix["marsh"] / max(1e-9, 1 - mix["hills"]))
        terrain[marsh] = T["marsh"]
        rest &= ~marsh
        desert = _bottom(rest, wet, mix["desert"] / max(1e-9, 1 - mix["hills"] - mix["marsh"]))
        terrain[desert] = T["desert"]
        rest &= ~desert
        forest = _top(rest, wet, mix["forest"] / max(1e-9, 1 - mix["hills"] - mix["marsh"] - mix["desert"]))
        terrain[forest] = T["forest"]
        self.terrain[:] = terrain

        self._carve_rivers(gen, w["river_count"])
        self._compute_slope(w["relief_m"])
        self._assign_regions(gen, w["region_count"])
        self._adjacency()
        self.road_level[:] = 0
        return self

    def _carve_rivers(self, gen, count: int) -> None:
        elev = self.elevation
        land = self.terrain != T["water"]
        if count <= 0 or not land.any():
            return
        high = land & (elev >= np.quantile(elev[land], 0.85)) & (self.terrain != T["mountains"])
        if not high.any():
            high = land & (elev >= np.quantile(elev[land], 0.85))
        cand = np.flatnonzero(high)
        if cand.size == 0:
            return
        srcs = gen.choice(cand, size=min(count, cand.size), replace=False)
        for s in srcs:
            cur = int(s)
            for _ in range(self.w + self.h):
                if self.terrain[cur] == T["water"]:
                    break
                self.river[cur] = True
                nb = self.neighbors(cur)
                # descend: lowest neighbor with a tiny tie-break; prefer existing river / water
                score = elev[nb] - 0.02 * (self.river[nb] | (self.terrain[nb] == T["water"])) + 1e-4 * gen.random(nb.size)
                nxt = int(nb[np.argmin(score)])
                if self.river[nxt] and self.terrain[nxt] != T["water"] and nxt != cur:
                    self.river[nxt] = True
                    break                                  # joined another river
                if elev[nxt] >= elev[cur] and self.terrain[nxt] != T["water"]:
                    self.terrain[cur] = T["water"]         # local minimum becomes a lake
                    self.river[cur] = False
                    break
                cur = nxt

    def _compute_slope(self, relief_m: float) -> None:
        e = self.elevation * relief_m
        diff = np.zeros(self.n, dtype=np.float32)
        for k in range(6):
            nb = self.nbr[:, k]
            ok = nb >= 0
            d = np.zeros(self.n, dtype=np.float32)
            d[ok] = np.abs(e[nb[ok]] - e[ok])
            diff = np.maximum(diff, d)
        self.slope[:] = diff / self.cell_km

    def mainland(self, season: str = "summer") -> np.ndarray:
        """Boolean mask of the largest walkable component (foot, no roads).  Islands and
        mountain pockets are land but not mainland; settlement and region seeds avoid them."""
        walk = np.flatnonzero((self.terrain != T["water"]) & (self.terrain != T["mountains"]))
        edge = self.edge_hours(season)
        seen = np.zeros(self.n, bool); best = np.zeros(self.n, bool)
        for i in walk:
            if seen[i]:
                continue
            arr, _ = self.dijkstra([int(i)], edge)
            comp = np.isfinite(arr); seen |= comp
            if comp.sum() > best.sum():
                best = comp
        return best

    def _assign_regions(self, gen, k: int) -> None:
        land = np.flatnonzero(self.mainland())
        if land.size == 0:
            land = np.flatnonzero(self.terrain != T["water"])
        if land.size == 0:
            return
        seeds = [int(gen.choice(land))]
        mind = self.hex_distance(np.arange(self.n), seeds[0]).astype(np.int32)
        for _ in range(1, min(k, land.size)):
            far = land[np.argmax(mind[land])]
            seeds.append(int(far))
            mind = np.minimum(mind, self.hex_distance(np.arange(self.n), far))
        dists = np.stack([self.hex_distance(np.arange(self.n), s) for s in seeds])
        self.region[:] = np.argmin(dists, axis=0)
        self.region_seeds = np.array(seeds, dtype=np.int32)

    def _adjacency(self) -> None:
        water = self.terrain == T["water"]
        wa = np.zeros(self.n, bool)
        ra = np.zeros(self.n, bool)
        for k in range(6):
            nb = self.nbr[:, k]
            ok = nb >= 0
            wa[ok] |= water[nb[ok]]
            ra[ok] |= self.river[nb[ok]]
        self.water_adjacent[:] = wa
        self.river_adjacent[:] = ra

    # ------------------------------------------------------------------ travel
    def edge_hours(self, season: str, mounted: bool = False, cart: bool = False, injured: bool = False) -> np.ndarray:
        """(n, 6) hours to step from cell i to its k-th neighbor; inf = impassable.
        River crossing is charged on ENTERING a river cell from a non-river cell (bridge waives it);
        water is impassable unless a ferry stands on either side; mountains need road_level >= 1."""
        tr = self.cfg["transit"]
        tc = np.array([self.cfg["world"]["terrain_cost"][t] for t in TERRAINS], dtype=np.float32)
        road_mult = np.array(tr["road_mult"], dtype=np.float32)
        out = np.full((self.n, 6), np.inf, dtype=np.float32)
        # every [transit] *_mult is a SPEED multiplier (winter 0.6 = slower, mounted 2.0 = faster)
        base = self._hours_per_plains_cell / tr["season_mult"][season]
        if mounted: base /= tr["mounted_mult"]
        if cart: base /= tr["cart_mult"]
        if injured: base /= tr["injured_mult"]
        for k in range(6):
            nb = self.nbr[:, k]
            ok = nb >= 0
            a = np.flatnonzero(ok); b = nb[ok]
            cost = tc[self.terrain[b]].copy()
            water_edge = (self.terrain[b] == T["water"]) | (self.terrain[a] == T["water"])
            ferry_ok = self.ferry[a] | self.ferry[b]
            cost[water_edge & ferry_ok] = 1.0
            cost[water_edge & ~ferry_ok] = 0.0
            cost[(self.terrain[b] == T["mountains"]) & (self.road_level[b] < 1)] = 0.0
            road = road_mult[np.minimum(self.road_level[a], self.road_level[b])]
            cross = np.where(self.river[b] & ~self.river[a] & ~self.bridge[b], tr["river_cross_mult"], 1.0)
            hours = np.where(cost > 0, base * cost * road * cross, np.inf)
            out[a, k] = hours
        return out

    def dijkstra(self, sources, edge_cost: np.ndarray, limit: float = np.inf):
        """Minimum arrival time from sources (list of cell idx, or list of (idx, t0)).
        Returns (arrival[n] float32 with inf, prev[n] int32 with -1).  Stops at `limit`."""
        arrival = np.full(self.n, np.inf, dtype=np.float32)
        prev = np.full(self.n, -1, dtype=np.int32)
        heap = []
        for s in sources:
            i, t0 = (s if isinstance(s, tuple) else (int(s), 0.0))
            if t0 < arrival[i]:
                arrival[i] = t0
                heapq.heappush(heap, (float(t0), int(i)))
        nbr, done = self.nbr, np.zeros(self.n, bool)
        while heap:
            t, i = heapq.heappop(heap)
            if done[i] or t > arrival[i]:
                continue
            done[i] = True
            if t > limit:
                break
            row, costs = nbr[i], edge_cost[i]
            for k in range(6):
                j = row[k]
                if j < 0:
                    continue
                c = costs[k]
                if c == np.inf:
                    continue
                nt = t + c
                if nt < arrival[j] and nt <= limit:
                    arrival[j] = nt
                    prev[j] = i
                    heapq.heappush(heap, (float(nt), int(j)))
        return arrival, prev

    @staticmethod
    def path(prev: np.ndarray, target: int) -> list[int]:
        out, cur = [], int(target)
        while cur >= 0:
            out.append(cur)
            cur = int(prev[cur])
        return out[::-1]

    def travel(self, a: int, b: int, season: str, **mode) -> tuple[float, list[int]]:
        """(days, path) from a to b on foot/mount/cart this season; days = inf if unreachable."""
        arrival, prev = self.dijkstra([a], self.edge_hours(season, **mode))
        if not np.isfinite(arrival[b]):
            return math.inf, []
        return float(arrival[b]) / self.day_hours, self.path(prev, b)

    # ------------------------------------------------------------------ summary / render
    def summary(self) -> dict:
        counts = {t: int((self.terrain == T[t]).sum()) for t in TERRAINS}
        regions = {int(r): int((self.region == r).sum()) for r in np.unique(self.region)}
        return {"cells": self.n, "w": self.w, "h": self.h, "terrain": counts,
                "river_cells": int(self.river.sum()), "regions": regions,
                "water_adjacent": int(self.water_adjacent.sum()), "river_adjacent": int(self.river_adjacent.sum())}

    def to_png(self, path: str, scale: int = 6, overlay: np.ndarray | None = None) -> None:
        from PIL import Image, ImageDraw
        colors = {T["water"]: (52, 101, 164), T["marsh"]: (96, 128, 80), T["plains"]: (170, 190, 110),
                  T["forest"]: (50, 110, 50), T["hills"]: (150, 130, 90), T["mountains"]: (210, 210, 215),
                  T["desert"]: (220, 200, 140)}
        W = int((self.w + 0.5) * scale); H = int(self.h * scale * math.sqrt(3) / 2) + scale
        img = Image.new("RGB", (W, H), (0, 0, 0))
        dr = ImageDraw.Draw(img)
        for i in range(self.n):
            x0 = int(self.x_km[i] / self.cell_km * scale); y0 = int(self.y_km[i] / self.cell_km * scale)
            c = colors[int(self.terrain[i])]
            if overlay is not None and overlay[i] > 0:
                o = float(min(1.0, overlay[i]))
                c = (int(c[0] * (1 - o) + 255 * o), int(c[1] * (1 - o) + 60 * o), int(c[2] * (1 - o)))
            dr.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill=c)
            if self.river[i]:
                dr.rectangle([x0 + scale // 3, y0 + scale // 3, x0 + 2 * scale // 3, y0 + 2 * scale // 3], fill=(80, 140, 220))
            nb = self.nbr[i]
            if any(nb[k] >= 0 and self.region[nb[k]] != self.region[i] for k in (1, 5)):
                dr.rectangle([x0, y0, x0 + scale - 1, y0], fill=(20, 20, 20))
        img.save(path)

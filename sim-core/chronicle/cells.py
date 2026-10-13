"""chronicle.cells — per-cell ecology: weather, moisture, vegetation/fuel, crops, wildlife, disease, fire.

ECOLOGY v1/v2.  Everything here is statistical and vectorized over cells; nothing is an individual
(people live in people.py).  Daily order, called by the engine:
    weather -> vegetation/fuel -> crops -> wildlife (growth, dispersal, predation, raids) -> disease -> fire
Fire is one arrival-time pass per day (world.dijkstra, limit = day_hours); arrival hours go on events.
Fronts persist across days: edge_progress[i, k] is the completed fraction of the crossing from burning
cell i toward neighbor k.  Remaining time is re-priced at TODAY's edge cost (weather/fuel/wind), so a
crossing longer than a day completes once enough burning hours accumulate while the source still burns.
Progress is cleared when the source burns out (an extinguished source does not keep heating its
neighbors) and when the target ignites by any route.  A cell ignited today keeps its ignition hour;
a cell carried over from a previous day burns from hour 0.  Losses scale with hours burned today.
Standing crops are NOT ledger resources (growth is a source bucket); harvest() emits integer food units
via src:growth.  Wildlife abundances are floats and are not balanced (ECOLOGY F3).
People never read these arrays directly: use the sample_* helpers (ECOLOGY F4).
"""
from __future__ import annotations

import numpy as np

from .config import Config
from .world import World, T, SEASONS
from .rng import Streams
from .ledger import Ledger

# vegetation carrying target by terrain index (water marsh plains forest hills mountains desert)
_VEG_TARGET = np.array([0.0, 0.8, 0.6, 1.0, 0.5, 0.15, 0.1], dtype=np.float32)
_VEG_RATE = 0.01          # per day relaxation toward target
_FUEL_RATE = 0.02         # per day: dead/dry biomass follows vegetation
_FIRE_VEG_LOSS = 0.5      # vegetation surviving a burn day
_FIRE_FUEL_LOSS = 0.3     # fuel surviving a burn day
_EPS = 1e-6


def season_of(day: int) -> str:
    return SEASONS[min(3, (day % 365) // 91)]


def _moisture_factor(m: np.ndarray, lo: float, opt: float, hi: float) -> np.ndarray:
    up = np.clip((m - lo) / max(opt - lo, _EPS), 0, 1)
    down = np.clip((hi - m) / max(hi - opt, _EPS), 0, 1)
    return np.minimum(up, down).astype(np.float32)


class Cells:
    def __init__(self, cfg: Config, world: World, streams: Streams):
        self.cfg, self.world, self.rng = cfg, world, streams
        n = world.n
        d = cfg.derived
        land = world.is_land()
        self.land = land
        self.wl_ids = list(cfg.catalog["wildlife"])
        self.wl_cfg = cfg.catalog["wildlife"]
        self.wl_k = np.array([d["wildlife_k_per_cell"][w] for w in self.wl_ids], dtype=np.float32)
        # physical
        self.moisture = np.where(land, 0.5, 1.0).astype(np.float32)
        self.vegetation = _VEG_TARGET[world.terrain].copy()
        self.fuel = (self.vegetation * 0.5).astype(np.float32)
        self.rain_mm = np.zeros(n, np.float32)
        self.protection = np.zeros(n, np.float32)         # 0..1, set by fence/guard systems
        self.cleared = np.zeros(n, np.float32)            # 0..1 fraction of the cell worked/built (fields, buildings); removes wild fuel
        # fire
        self.burning = np.zeros(n, bool)
        self.burn_hour = np.full(n, np.inf, np.float32)   # hour today the cell started burning; inf = not burning today
        self.burn_scar_day = np.full(n, -1, np.int32)
        self.burn_since_day = np.full(n, -1, np.int32)    # day this burn episode began (new vs continuing)
        self.edge_progress = np.zeros((n, 6), np.float32)  # completed fraction of crossing i -> nbr k
        # crops (one crop per theme in v1; fields are counts set by the farm/building system)
        self.crop_id = next(iter(cfg.catalog["crops"]))
        self.crop = cfg.catalog["crops"][self.crop_id]
        self.units_per_field = float(d["crop_units_per_field_max"][self.crop_id])
        self.fields = np.zeros(n, np.int32)
        self.planted = np.zeros(n, bool)
        self.crop_progress = np.zeros(n, np.float32)
        self.crop_health = np.ones(n, np.float32)
        # wildlife (n, C) starting at half K on land
        self.wildlife = (0.5 * self.wl_k[None, :] * land[:, None] * self.vegetation[:, None]).astype(np.float32)
        # disease: SEIR fractions per (disease, wildlife host)
        self.seir: dict[tuple[str, str], np.ndarray] = {}
        for did, dz in cfg.catalog["disease"].items():
            for h in dz["hosts"]:
                if h in self.wl_cfg:
                    a = np.zeros((n, 4), np.float32); a[:, 0] = 1.0
                    self.seir[(did, h)] = a
        # weather state per region
        self.regions = np.unique(world.region)
        self.wet = np.zeros(self.regions.size, bool)
        self.wind_dir = np.zeros(self.regions.size, np.int8)
        self.wind_kph = np.full(self.regions.size, cfg["weather"]["wind_mean_kph"], np.float32)
        # land-neighbor degree for dispersal
        nb = world.nbr
        self.land_nbr = (nb >= 0) & land[np.maximum(nb, 0)] & land[:, None]
        self.deg = np.maximum(self.land_nbr.sum(1), 1)
        self.last_fire_cells: np.ndarray = np.zeros(0, np.int32)
        # Diagnostics only: wildlife remains statistical, outside owned-stock accounting.
        # Measure REALIZED float32 changes using float64 reductions, not requested fluxes.
        self.reset_wildlife_budget()

    # ------------------------------------------------------------------ weather
    def step_weather(self, day: int) -> None:
        wcfg = self.cfg["weather"]; s = season_of(day)
        g = self.rng.one("weather", day, 0)
        stay = g.random(self.regions.size) < wcfg["persistence"]
        self.wet = np.where(stay, self.wet, ~self.wet)
        self.wind_dir = np.where(g.random(self.regions.size) < 0.3, g.integers(0, 6, self.regions.size), self.wind_dir).astype(np.int8)
        self.wind_kph = (wcfg["wind_mean_kph"] * g.gamma(2.0, 0.5, self.regions.size)).astype(np.float32)
        rain_r = np.where(self.wet, g.exponential(2.0 * wcfg["rain_mm_mean"][s], self.regions.size), 0.0)
        self.rain_mm[:] = rain_r[self.world.region]
        cap = wcfg["moisture_capacity_mm"]
        evap = wcfg["evaporation_mm"][s]
        sc = self.cfg["scenario"]; dr = sc["drought"]
        if sc["enabled"] and 0 <= dr["start_day"] <= day < dr["start_day"] + dr["days"]:
            evap = evap * dr["evaporation_mult"]; self.rain_mm *= np.float32(dr["rain_mult"])
        m = self.moisture + (self.rain_mm - evap * (0.5 + 0.5 * self.moisture)) / cap
        self.moisture[:] = np.where(self.land, np.clip(m, 0, 1), 1.0)

    def step_vegetation(self) -> None:
        target = _VEG_TARGET[self.world.terrain] * (0.3 + 0.7 * self.moisture)
        self.vegetation += _VEG_RATE * (target - self.vegetation)
        self.fuel += _FUEL_RATE * (self.vegetation * (1 - self.cleared) - self.fuel)
        np.clip(self.vegetation, 0, 1, out=self.vegetation); np.clip(self.fuel, 0, 1, out=self.fuel)

    def clear(self, cells, fraction) -> None:
        """Worked land: fields and buildings remove wild fuel from a cell.  Adds `fraction` (0..1) to the
        cleared share of each cell (capped at 1) and immediately drops standing fuel to the new ceiling,
        so a freshly generated village is not a fuel bed on day 0."""
        c = np.asarray(cells, dtype=np.int64)
        self.cleared[c] = np.minimum(1.0, self.cleared[c] + np.float32(fraction))
        self.fuel[c] = np.minimum(self.fuel[c], self.vegetation[c] * (1 - self.cleared[c]))

    # ------------------------------------------------------------------ crops
    def plant(self, day: int, cells: np.ndarray, ledger: Ledger | None = None, *, actors=(), place=-1) -> np.ndarray:
        c = np.asarray(cells); c = c[(self.fields[c] > 0) & ~self.planted[c]]
        self.planted[c] = True; self.crop_progress[c] = 0.0; self.crop_health[c] = 1.0
        if ledger is not None:
            for i in c: ledger.event(day, "planted", actors, cell=int(i), place=place, crop=self.crop_id, fields=int(self.fields[i]))
        return c

    def step_crops(self, day: int) -> None:
        cr = self.crop
        f = _moisture_factor(self.moisture, cr["moisture_min"], cr["moisture_optimal"], cr["moisture_max"])
        p = self.planted
        self.crop_progress[p] += (f[p] / cr["growth_days"])
        # Moisture stress (too dry OR too wet) costs crop.stress_loss_per_day of final yield per fully
        # stressed day, scaled by (1 - f).  Progress already slows under stress above, so this term is
        # the yield damage, not the delay.  Catalog data; no number lives here.
        sl = cr["stress_loss_per_day"]
        self.crop_health[p] *= (1.0 - sl * (1.0 - f[p]))
        if season_of(day) == "winter":
            still = p & (self.crop_progress < 1.0)
            self.crop_health[still] *= (1.0 - cr.get("frost_loss", 0.0) / 90.0)

    def harvest(self, day: int, cells: np.ndarray, ledger: Ledger, *, actors=(), place=-1) -> dict[int, int]:
        """Harvest standing crop in `cells` into the cell's ground holder (c:<i>) as integer food units."""
        out = {}
        for i in np.asarray(cells):
            i = int(i)
            if not self.planted[i]: continue
            units = int(round(self.units_per_field * self.fields[i] * min(1.0, self.crop_progress[i]) * self.crop_health[i]))
            self.planted[i] = False; self.crop_progress[i] = 0.0; self.crop_health[i] = 1.0
            if units > 0:
                ledger.flow(day, self.crop["item"], "src:growth", f"c:{i}", units, "harvest")
            ledger.event(day, "harvest", actors, cell=i, place=place, crop=self.crop_id, units=units)
            out[i] = units
        return out

    # ------------------------------------------------------------------ wildlife
    def reset_wildlife_budget(self) -> None:
        """Rebase diagnostics after explicit fixture/setup edits; never a balancing repair.
        Does not change abundance. Normal engine stepping must NOT call this method.
        """
        self.wl_initial = self.wildlife.sum(0, dtype=np.float64)
        self.budget = {k: np.zeros(len(self.wl_ids), np.float64) for k in
                       ("growth", "density_loss", "dispersal", "predation", "disease", "fire", "hunt")}

    def wildlife_budget(self) -> dict:
        """Component accounting for debugging, not an exact owned-resource ledger.
        Dispersal's global delta should be rounding-sized, not an ecological sink.
        Habitat modifies K and thereby growth/predation; it is NOT an additive sink.
        """
        current = self.wildlife.sum(0, dtype=np.float64)
        accounted = sum(self.budget.values(), np.zeros(len(self.wl_ids), np.float64))
        return {w: {"initial": float(self.wl_initial[i]), "final": float(current[i]),
                    "components": {k: float(v[i]) for k, v in self.budget.items()},
                    "residual": float(current[i] - self.wl_initial[i] - accounted[i])}
                for i, w in enumerate(self.wl_ids)}

    def carrying(self, cell: int | None = None) -> np.ndarray:
        """Habitat-coupled K or fixed terrain-target K (diagnostic control only).
        Optional single-cell lookup keeps local observations O(categories), not O(world).
        """
        veg = self.vegetation if cell is None else self.vegetation[cell]
        terrain = self.world.terrain if cell is None else self.world.terrain[cell]
        land = self.land if cell is None else self.land[cell]
        veg = np.maximum(veg, 0.02) if self.cfg["wildlife"]["habitat_coupling"] else _VEG_TARGET[terrain]
        if cell is not None: return self.wl_k * veg * land
        return self.wl_k[None, :] * veg[:, None] * land[:, None]

    def _intake(self, N: np.ndarray, K: np.ndarray, ci: int, prey: list) -> tuple:
        """Capped functional response for predator category ci (used only when wildlife.prey_intake is on).
        Uncapped intake per predator per day = sum over wildlife prey of predation_rate x prey_N / predator_K
        (x the refuge factor when prey_refuge is on) - the same kills the legacy rule makes, divided by the
        number of predators. Returns (scale, fed): scale shrinks the kill pressure so intake <= intake_cap;
        fed = capped intake / intake_cap in [0, 1]. Livestock in the diet is not counted (no cell stock).
        """
        wc = self.cfg["wildlife"]; Kc = np.maximum(K[:, ci], _EPS)
        raw = np.zeros(N.shape[0], np.float64)
        for pi in prey:
            x = wc["predation_rate"] * N[:, pi].astype(np.float64) / Kc
            if wc.get("prey_refuge", False):
                x = x * np.clip(N[:, pi] / np.maximum(K[:, pi], _EPS) / max(wc["refuge_fill"], _EPS), 0, 1)
            raw += x
        cap = wc["intake_cap"]
        scale = np.where(raw > cap, cap / np.maximum(raw, _EPS), 1.0)
        fed = np.clip(raw / cap, 0, 1)
        return scale, fed

    def step_wildlife(self, day: int) -> None:
        wc = self.cfg["wildlife"]
        K = self.carrying()
        N = self.wildlife
        r = np.array([self.wl_cfg[w]["r"] for w in self.wl_ids], np.float32)
        Kg = K
        if wc.get("prey_response", False):
            # Optional numerical response (default OFF): a category with wildlife prey grows toward
            # K x clip(prey_fill / response_fill, response_floor, 1) instead of K. Predators above that
            # target decline logistically (booked as density_loss) and recover when prey does; the floor
            # keeps them from reaching zero. Local per-cell prey fill, pre-growth.
            Kg = K.copy()
            for ci, w in enumerate(self.wl_ids):
                prey = [self.wl_ids.index(q) for q in self.wl_cfg[w]["diet"] if q in self.wl_ids]
                if not prey: continue
                fill = np.mean([N[:, pi] / np.maximum(K[:, pi], _EPS) for pi in prey], axis=0)
                Kg[:, ci] = K[:, ci] * np.clip(fill / max(wc["response_fill"], _EPS), wc["response_floor"], 1)
        growth = r[None, :] * N * (1 - N / np.maximum(Kg, _EPS))
        if wc.get("prey_intake", False):
            # Optional capped intake (default OFF): a category with wildlife prey grows only as far as it is
            # fed. fed = today's per-predator intake / intake_cap (0..1), from the same capped functional
            # response used for kills below. Fully fed -> logistic toward K; unfed -> loses intake_starve of
            # its animals per day; in between, a mix. Stateless (computed from current abundances), so
            # checkpoints are unchanged. Booked as growth / density_loss, so the budget still closes.
            for ci, w in enumerate(self.wl_ids):
                prey = [self.wl_ids.index(q) for q in self.wl_cfg[w]["diet"] if q in self.wl_ids]
                if not prey: continue
                _, fed = self._intake(N, K, ci, prey)
                Nc = N[:, ci]
                growth[:, ci] = r[ci] * Nc * fed * (1 - Nc / np.maximum(Kg[:, ci], _EPS)) - wc["intake_starve"] * Nc * (1 - fed)
        old_N = N.copy()
        N += np.where(K > 0, growth, -N)
        if wc.get("prey_coupling", False):
            # Optional predator-prey coupling (default OFF, a diagnostic/structural switch):
            # a category with wildlife prey loses a fraction of its animals per day in
            # proportion to how unfed it is. fed = mean prey fill (N/K) over its wildlife
            # prey, divided by prey_satiation and clipped to [0,1]. Non-wildlife prey
            # (livestock) are NOT counted here, so this is a conservative food signal.
            # The loss is booked under density_loss so the budget still closes.
            for ci, w in enumerate(self.wl_ids):
                prey = [self.wl_ids.index(q) for q in self.wl_cfg[w]["diet"] if q in self.wl_ids]
                if not prey: continue
                fill = np.mean([old_N[:, pi] / np.maximum(K[:, pi], _EPS) for pi in prey], axis=0)
                fed = np.clip(fill / max(wc["prey_satiation"], _EPS), 0, 1)
                N[:, ci] -= wc["starvation_rate"] * old_N[:, ci] * (1 - fed) * (K[:, ci] > 0)
        np.maximum(N, 0, out=N)
        delta = N.astype(np.float64) - old_N
        self.budget["growth"] += np.maximum(delta, 0).sum(0)
        self.budget["density_loss"] += np.minimum(delta, 0).sum(0)
        # Births enter susceptible; density deaths remove compartments in equal
        # proportion. Wildlife fractions must move WITH the animals, not remain
        # pinned to a cell after the hosts leave it.
        for (did, host), a in self.seir.items():
            hi = self.wl_ids.index(host)
            old = old_N[:, hi]; new = N[:, hi]
            counts = a * np.minimum(old, new)[:, None]
            counts[:, 0] += np.maximum(new - old, 0)
            a[:] = counts / np.maximum(new[:, None], _EPS)
            empty = new <= _EPS
            a[empty] = (1., 0., 0., 0.)
        # dispersal: a fraction leaves, split evenly among land neighbors
        flux = wc["dispersal"] * N / self.deg[:, None]
        inflow = np.zeros_like(N)
        for k in range(6):
            ok = self.land_nbr[:, k]; dst = self.world.nbr[ok, k]
            np.add.at(inflow, dst, flux[ok])
        after = N + inflow - flux * self.land_nbr.sum(1)[:, None]
        for (did, host), a in self.seir.items():
            hi = self.wl_ids.index(host)
            counts = a * N[:, hi, None]
            leaving = a * flux[:, hi, None]
            arriving = np.zeros_like(counts)
            for k in range(6):
                ok = self.land_nbr[:, k]; dst = self.world.nbr[ok, k]
                np.add.at(arriving, dst, leaving[ok])
            counts += arriving - leaving * self.land_nbr.sum(1)[:, None]
            np.maximum(counts, 0, out=counts)
            a[:] = counts / np.maximum(counts.sum(1, keepdims=True), _EPS)
            a[after[:, hi] <= _EPS] = (1., 0., 0., 0.)
        after = after.astype(N.dtype)  # account AFTER the actual storage rounding
        np.maximum(after, 0, out=after)
        self.budget["dispersal"] += (after.astype(np.float64) - N).sum(0)  # rounding-sized
        N[:] = after
        # predation and raids by diet
        before_pred = N.copy()
        intake_on = wc.get("prey_intake", False)
        for ci, w in enumerate(self.wl_ids):
            diet = self.wl_cfg[w]["diet"]
            if intake_on:
                # Capped intake: scale this predator's pressure on every wildlife prey so total intake per
                # predator is min(linear response, intake_cap). Computed once from pre-predation abundance.
                wprey = [self.wl_ids.index(q) for q in diet if q in self.wl_ids]
                cap_scale = self._intake(N, K, ci, wprey)[0] if wprey else None
            for prey in diet:
                if prey in self.wl_ids:
                    pi = self.wl_ids.index(prey)
                    # Saturate EACH loss, not just the final abundance: two excessive
                    # predator pressures must never multiply two negatives into new prey.
                    pressure = np.clip(wc["predation_rate"] * N[:, ci] / np.maximum(K[:, ci], _EPS), 0, 1)
                    if wc.get("prey_refuge", False):
                        # Optional prey refuge (default OFF): predators find prey harder as it gets scarce,
                        # so the kill fraction scales with the cell's own prey fill (N/K), saturating at
                        # refuge_fill. Uses pre-predation abundance of THIS cell; local, not map-wide.
                        fill = N[:, pi] / np.maximum(K[:, pi], _EPS)
                        pressure = pressure * np.clip(fill / max(wc["refuge_fill"], _EPS), 0, 1)
                    if intake_on:
                        pressure = pressure * cap_scale
                    N[:, pi] *= (1 - pressure)
            if self.wl_cfg[w]["raids"]:
                loss = wc["raid_rate"] * (N[:, ci] / np.maximum(K[:, ci], _EPS)) * (1 - self.protection)
                self.crop_health[self.planted] *= (1 - np.clip(loss[self.planted], 0, 0.5))
        np.maximum(N, 0, out=N); self.budget["predation"] += (N.astype(np.float64) - before_pred).sum(0); self.wildlife = N.astype(np.float32)

    # ------------------------------------------------------------------ disease (wildlife hosts)
    def infect(self, disease: str, host: str, cells, frac: float = 0.05) -> None:
        a = self.seir[(disease, host)]; c = np.unique(np.asarray(cells, dtype=int))
        if not np.isfinite(frac) or frac < 0 or np.any(c < 0) or np.any(c >= self.world.n):
            raise ValueError('invalid infection seed')
        if np.any(a[c, 0] < frac): raise ValueError('infection seed exceeds susceptible fraction')
        a[c, 0] -= frac; a[c, 2] += frac

    def step_disease(self, day: int) -> None:
        for (did, host), a in self.seir.items():
            dz = self.cfg.catalog["disease"][did]; hi = self.wl_ids.index(host)
            S, E, I, R = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
            force = dz["beta"][host] * I
            for pair, x in dz["cross"].items():
                src, _, dst = pair.partition(">")
                if dst == host and (did, src) in self.seir:
                    force = force + x * self.seir[(did, src)][:, 2]
            newE = np.clip(force, 0, 1) * S
            e2i = E / dz["incubation_days"]; i2out = I / dz["infectious_days"]
            dead = i2out * dz["mortality"][host]
            a[:, 0] = S - newE + (0 if dz["immunity"] else i2out - dead)
            a[:, 1] = E + newE - e2i
            a[:, 2] = I + e2i - i2out
            a[:, 3] = R + (i2out - dead if dz["immunity"] else 0)
            tot = a.sum(1, keepdims=True); a /= np.maximum(tot, _EPS)   # renormalize after deaths
            before = self.wildlife[:, hi].copy()
            self.wildlife[:, hi] *= (1 - dead)
            self.budget["disease"][hi] += (self.wildlife[:, hi].astype(np.float64) - before).sum()

    # ------------------------------------------------------------------ fire
    def ignite(self, day: int, cells, source: str, ledger: Ledger | None = None, hour: float = 0.0) -> np.ndarray:
        c = np.asarray(cells, dtype=np.int64)
        fcfg = self.cfg["fire"]
        ok = self.land[c] & (self.fuel[c] >= fcfg["burnout"]) & ~self.burning[c] & (self._eff_fuel()[c] >= fcfg["min_effective_fuel"])
        c = c[ok]
        if not (0.0 <= hour < self.world.day_hours): raise ValueError("ignition hour must lie within the day")
        self.burning[c] = True; self.burn_hour[c] = hour; self.burn_since_day[c] = day
        self.edge_progress[np.isin(self.world.nbr, c)] = 0.0   # nobody is still 'crossing into' a burning cell
        if ledger is not None:
            for i in c: ledger.event(day, "fire_ignited", [], cell=int(i), source=source, hour=float(hour))
        return c

    def _eff_fuel(self) -> np.ndarray:
        """Effective (burnable) fuel: fuel x dryness.  Below fire.min_effective_fuel a cell neither
        ignites nor receives spread (moisture of extinction); this is what bounds a fire season.
        A cell already alight keeps burning down to fire.burnout regardless."""
        return self.fuel * (1 - self.moisture)

    def _fire_edges(self) -> np.ndarray:
        fcfg = self.cfg["fire"]; base = self.cfg.derived["cell_cross_hours_base"]
        w = self.world; out = np.full((w.n, 6), np.inf, np.float32)
        wind_dir = self.wind_dir[w.region]; wind = self.wind_kph[w.region]
        eff = self._eff_fuel()
        passable = self.land & (self.fuel >= fcfg["burnout"]) & (eff >= fcfg["min_effective_fuel"])
        for k in range(6):
            nb = w.nbr[:, k]; ok = nb >= 0; a = np.flatnonzero(ok); b = nb[ok]
            # downwind step is faster, upwind slower; (k - wind_dir) mod 6 == 0 is straight downwind
            rel = (k - wind_dir[a]) % 6
            wf = np.where(rel == 0, 1.0 / (1 + wind[a] / 20), np.where(rel == 3, 1 + wind[a] / 20, 1.0))
            hours = base / np.maximum(eff[b], _EPS) * wf
            out[a, k] = np.where(passable[b], hours, np.inf)
        return out

    def step_fire(self, day: int, ledger: Ledger | None = None) -> np.ndarray:
        """Lightning ignitions, then one arrival-time pass from every burning cell, carrying unfinished
        edge progress across days.  Returns cells that burned today (new + continuing) so the building
        system can apply damage by hours burned."""
        fcfg = self.cfg["fire"]; s = season_of(day); H = self.world.day_hours
        # continuing fires burn from hour 0; cells ignited today (ignite(day, hour=..)) keep their hour
        cont = self.burning & (self.burn_since_day < day)
        self.burn_hour[cont] = 0.0
        self.burn_hour[~self.burning] = np.inf
        p = self.cfg["weather"]["lightning_p"][s] * self._eff_fuel() * self.land   # ignite() applies min_effective_fuel
        strikes = np.flatnonzero(self.rng.uniform("lightning", day, self.world.n) < p)
        if strikes.size: self.ignite(day, strikes, "lightning", ledger, hour=float(self.rng.one("lightning", day, 1).random() * 24))
        src = np.flatnonzero(self.burning)
        if src.size == 0:
            self.edge_progress[:] = 0.0
            return src
        edges = self._fire_edges()
        # re-price the unfinished part of every crossing at today's cost
        eff = edges.copy(); eff[src] = edges[src] * (1.0 - self.edge_progress[src])
        arrival, _ = self.world.dijkstra([(int(i), float(self.burn_hour[i])) for i in src], eff, limit=H)
        reached = np.flatnonzero(np.isfinite(arrival) & ~self.burning)
        self.burning[reached] = True; self.burn_hour[reached] = arrival[reached]; self.burn_since_day[reached] = day
        if ledger is not None:
            for i in reached: ledger.event(day, "fire_spread", [], cell=int(i), hour=float(arrival[i]))
        burned = np.flatnonzero(self.burning)
        # accumulate progress on crossings that did not complete today
        hours_left = (H - self.burn_hour[burned])[:, None]
        cost = edges[burned]
        nb = self.world.nbr[burned]
        target_burning = (nb >= 0) & self.burning[np.maximum(nb, 0)]
        passable = np.isfinite(cost) & (nb >= 0)
        prog = self.edge_progress[burned]
        prog = np.where(passable, np.minimum(prog + hours_left / np.where(passable, cost, 1.0), 1.0), prog)
        prog[target_burning] = 0.0
        self.edge_progress[burned] = prog
        # duration-scaled losses: a cell lit at hour 20 loses 4/24 of a full burn day
        frac = ((H - self.burn_hour[burned]) / H).astype(np.float32)
        self.fuel[burned] *= _FIRE_FUEL_LOSS ** frac; self.vegetation[burned] *= _FIRE_VEG_LOSS ** frac
        self.planted[burned] = False; self.crop_progress[burned] = 0; self.crop_health[burned] = 1
        self.burn_scar_day[burned] = day
        wl_before = self.wildlife[burned].copy(); self.wildlife[burned] *= (0.5 ** frac)[:, None]
        self.budget["fire"] += (self.wildlife[burned].astype(np.float64) - wl_before).sum(0)
        self.burning[burned] = self.fuel[burned] >= fcfg["burnout"]   # a lit cell smolders down to burnout; min_effective_fuel gates ignition/spread only
        out = burned[~self.burning[burned]]
        self.edge_progress[out] = 0.0; self.burn_since_day[out] = -1   # extinguished sources lose their progress
        self.last_fire_cells = burned
        return burned

    # ------------------------------------------------------------------ the day
    def step(self, day: int, ledger: Ledger | None = None) -> np.ndarray:
        self.step_weather(day); self.step_vegetation(); self.step_crops(day)
        self.step_wildlife(day); self.step_disease(day)
        return self.step_fire(day, ledger)

    # ------------------------------------------------------------------ observation (ECOLOGY F4)
    def forage_band(self, wl: str, cell: int) -> str:
        lo, hi = self.cfg["observation"]["forage_bands"]
        ci = self.wl_ids.index(wl); k = float(self.carrying(cell)[ci])
        x = self.wildlife[cell, ci] / max(k, _EPS)
        return "scarce" if x < lo else "few" if x < hi else "plenty"

    def sample_hunt(self, day: int, person: int, wl: str, cell: int, skill: float = 1.0) -> int:
        """A hunt yields an integer kg of meat drawn from abundance; success lowers abundance."""
        ci = self.wl_ids.index(wl); g = self.rng.one("hunt", day, person)
        k = float(self.carrying(cell)[ci])
        p = min(0.9, skill * self.wildlife[cell, ci] / max(k, _EPS))
        if g.random() < p:
            before = float(self.wildlife[cell, ci])
            self.wildlife[cell, ci] = max(0.0, before - 1.0)
            self.budget["hunt"][ci] += float(self.wildlife[cell, ci]) - before
            return int(self.wl_cfg[wl]["hunt_yield_kg"])
        return 0

    def visible(self, cell: int) -> dict:
        """What simply being in a cell shows: coarse bands, never numbers."""
        return {"smoke": bool(self.burning[self.world.neighbors(cell)].any() or self.burning[cell]),
                "fire": bool(self.burning[cell]),
                "crop": ("none" if not self.planted[cell] else "poor" if self.crop_health[cell] < 0.6 else "fair" if self.crop_health[cell] < 0.9 else "good"),
                "dry": bool(self.moisture[cell] < 0.2),
                "forage": {w: self.forage_band(w, cell) for w in self.wl_ids}}

    def summary(self) -> dict:
        L = self.land
        return {"moisture_mean": float(self.moisture[L].mean()), "fuel_mean": float(self.fuel[L].mean()),
                "burning": int(self.burning.sum()), "planted": int(self.planted.sum()),
                "wildlife_total": {w: float(self.wildlife[:, i].sum()) for i, w in enumerate(self.wl_ids)},
                "infected": {f"{d}/{h}": float((a[:, 2] * self.wildlife[:, self.wl_ids.index(h)]).sum()) for (d, h), a in self.seir.items()}}

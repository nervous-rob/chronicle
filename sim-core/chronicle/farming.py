"""Bounded physical food-loop resolver, not a market or an omniscient agent menu.

Explicit setup binds workers to an owned farm, pantry and store. One crop cohort
and one registered farm per cell (reject, rather than mix owners/progress).
Cheap routines use assigned household stock counts; belief-based planning awaits
observation work. Journeys, handling and labor share a daily activity budget.
Seed stocks, soil nutrients, tools, guarding and animal products are not modeled.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import numpy as np
from .cells import season_of
from .inventory import Leg, InventoryError


@dataclass
class Farm:
    place: int
    home: int
    store: int
    fields: int
    owner: str
    plant_hours: float = 0.0
    harvest_hours: float = 0.0
    last_plant_year: int = -1
    rebuild_hours: float = 0.0     # labor already sunk into rebuilding this farm after a fire (rebuild-v1)


@dataclass
class Household:
    """Pantry logistics for a household without (or beyond) a farm: a keeper carries food from the store to the home."""
    home: int
    store: int
    owner: str


@dataclass
class Journey:
    target: int
    path: list
    edge: int = 0
    remaining: float = 0.0
    elapsed: float = 0.0
    local: bool = False


class Farming:
    def __init__(self, engine):
        self.e = engine
        self.farms: dict[int, Farm] = {}
        self.assignments: dict[int, int] = {}
        self.households: dict[int, Household] = {}   # home place id -> Household (worldgen-v1)
        self.keepers: dict[int, int] = {}            # pid -> home place id
        self.journeys: dict[int, Journey] = {}
        self.day = -1
        self.budgets = {}
        self.actions = {}
        self.labor = []  # daily trace, included in report fixtures
        self._routes = {}

    @property
    def cfg(self): return self.e.cfg['agriculture']

    def register(self, place, home, store, fields=1):
        e = self.e
        if e.day != 0: raise ValueError('farm registration is setup-only')
        if type(fields) is not int or fields <= 0: raise ValueError('positive integer fields required')
        b, h, s = (e.places[i] for i in (place, home, store))
        if 'field' not in e.cfg.building(b.kind)['affordances']: raise ValueError('not a field workplace')
        if not b.owner or not (b.owner == h.owner == s.owner): raise ValueError('farm/home/store need same owner')
        if e.cfg.building(h.kind)['shelter'] <= 0 or not e.inventory.holders[f'b:{store}'].store:
            raise ValueError('need a sheltered home and physical store')
        if b.cell in [e.places[f.place].cell for f in self.farms.values()] or e.cells.fields[b.cell] != 0:
            raise ValueError('one farm per cell until independent crop cohorts exist')
        if fields * e.cells.crop['hectares_per_field'] > e.cfg.derived['cell_area_km2'] * 100 + 1e-9:
            raise ValueError('fields exceed cell land area')
        self.farms[place] = Farm(place, home, store, fields, b.owner)
        e.cells.fields[b.cell] = fields
        e.cells.clear([b.cell], fields * e.cells.crop['hectares_per_field'] / (e.cfg.derived['cell_area_km2'] * 100))
        return self.farms[place]

    def assign(self, pid, place):
        e = self.e; p = e.people._actor(pid); f = self.farms[place]
        if f.owner not in e.people.owners(p): raise ValueError('worker lacks household/owner access')
        if not p.role or not e.people.can_use_role(p, p.role, e.day): raise ValueError('ineligible worker')
        role = e.cfg.role(p.role)
        if role['workplace'] != e.places[place].kind or e.cells.crop['item'] not in role['produces']:
            raise ValueError('role fields do not support this crop workplace')
        if p.home_place != f.home: raise ValueError('assigned pantry must be worker home')
        self.assignments[pid] = place

    def register_household(self, home, store):
        """Setup-only: a home/store pair whose pantry a keeper refills. Same physical checks as a farm's home/store."""
        e = self.e
        if e.day != 0: raise ValueError('household registration is setup-only')
        h, st = e.places[home], e.places[store]
        if not h.owner or h.owner != st.owner: raise ValueError('home/store need the same owner')
        if e.cfg.building(h.kind)['shelter'] <= 0 or not e.inventory.holders[f'b:{store}'].store:
            raise ValueError('need a sheltered home and physical store')
        if home in self.households: raise ValueError('household already registered')
        self.households[home] = Household(home, store, h.owner)
        return self.households[home]

    def assign_keeper(self, pid, home):
        e = self.e; p = e.people._actor(pid); h = self.households[home]
        if h.owner not in e.people.owners(p) or p.home_place != home: raise ValueError('keeper must live in the household')
        if not e.people.at_least(p, e.day, 'youth'): raise ValueError('keeper too young')
        self.keepers[pid] = home

    def begin(self, day):
        if day <= self.day: raise ValueError('farming day repeated or reversed')
        self.day = day; self._routes.clear(); self.actions = {}; self.labor = []
        # sleep-v1a: today's activity budget is scaled by the consequence rows that
        # matched this morning (last night's quality + current rest band). Rows are config data.
        season = season_of(day); self.budgets = {}
        for pid, p in self.e.people.rows.items():
            if not p.alive: continue
            mult, _, _, _ = self.e.people.sleep_effects(p, season)
            self.budgets[pid] = self.cfg['activity_hours_per_day'] * mult
        for f in self.farms.values():
            cell = self.e.places[f.place].cell
            if not self.e.cells.planted[cell]: f.harvest_hours = 0.0
            if season_of(day) != self.e.cells.crop['plant_season']: f.plant_hours = 0.0

    def _person(self, pid):
        p = self.e.people._actor(pid)
        if self.day != self.e.day or pid not in self.budgets: raise ValueError('begin current day first')
        if p.busy: raise ValueError('actor already committed to another activity')
        return p

    def _spend(self, pid, hours, action, label=True):
        if not math.isfinite(hours) or hours < 0 or hours > self.budgets[pid] + 1e-7:
            raise ValueError('activity budget exceeded')
        self.budgets[pid] = max(0., self.budgets[pid] - hours)
        if hours > 0 and label: self.actions[pid] = action

    def _route(self, pid, target):
        e = self.e; p = e.people.rows[pid]; b = e.places[target]
        cart = any(l.holder == f'p:{pid}' and e.cfg.item(l.item)['kind'] == 'vehicle' for l in e.inventory.lots.values())
        injured = p.needs['health'] < e.cfg['needs']['thresholds']['low']
        key = (p.cell, p.place, target, cart, injured)
        if key not in self._routes:
            if p.cell == b.cell:
                hours = 0. if p.place == target else self.cfg['same_cell_place_hours']
                result = (hours, [p.cell], None)
            else:
                edges = e.world.edge_hours(season_of(self.day), cart=cart, injured=injured)
                arr, prev = e.world.dijkstra([p.cell], edges)
                result = (float(arr[b.cell]), e.world.path(prev, b.cell) if math.isfinite(float(arr[b.cell])) else [], edges)
            self._routes[key] = result
        return self._routes[key]

    def move(self, pid, target):
        e = self.e; p = self._person(pid)
        ruin_ok = self.assignments.get(pid) == target     # a worker may walk to the ruin of their own farm, to rebuild it
        if target not in e.places or (e.places[target].destroyed_day is not None and not ruin_ok): return False
        if pid not in self.journeys:
            hours, path, edges = self._route(pid, target)
            if not math.isfinite(hours): return False
            if hours == 0: return True
            if self.budgets[pid] <= 1e-8: return False
            local = len(path) == 1
            # sleep-v1a ration check: a cross-cell route that cannot finish today means a night
            # on the road; refuse it unless the traveler carries their own food.
            scfg = e.cfg['sleep']
            if not local and scfg['ration_refuse'] and hours > self.budgets[pid] + scfg['ration_route_margin_hours']:
                if not any(self._food(f'p:{pid}', o) for o in e.people.owners(p)):
                    e.ledger.event(self.day, 'journey_refused', [pid], cell=p.cell, place=p.place, target=target,
                                   reason='no_rations', route_hours=hours, hours_left=self.budgets[pid])
                    return False
            j = Journey(target, path, local=local)
            if local: j.remaining = hours
            else:
                k = int(np.flatnonzero(e.world.nbr[path[0]] == path[1])[0])
                j.remaining = float(edges[path[0], k])
            self.journeys[pid] = j
            e.ledger.event(self.day, 'departed', [pid], cell=p.cell, place=p.place, target=target)
            p.place = -1
        j = self.journeys[pid]
        if j.target != target: raise ValueError('finish current journey before retargeting')
        while self.budgets[pid] > 1e-8:
            spent = min(self.budgets[pid], j.remaining)
            self._spend(pid, spent, 'travel'); j.remaining -= spent; j.elapsed += spent
            if j.remaining > 1e-7: return False
            if not j.local:
                j.edge += 1; p.cell = j.path[j.edge]
                e.ledger.event(self.day, 'passed_through', [pid], cell=p.cell, target=target)
            if j.local or j.edge == len(j.path) - 1:
                p.cell = e.places[target].cell; p.place = target
                e.ledger.event(self.day, 'arrived', [pid], cell=p.cell, place=target, travel_hours=j.elapsed)
                del self.journeys[pid]
                return True
            # Entered edges retain their remaining travel time overnight; future
            # edges read today's season/mode and can become blocked.
            _, _, edges = self._route(pid, target)
            a, b = j.path[j.edge:j.edge+2]
            k = int(np.flatnonzero(e.world.nbr[a] == b)[0])
            j.remaining = float(edges[a, k])
            if not math.isfinite(j.remaining):
                del self.journeys[pid]
                e.ledger.event(self.day, 'journey_abandoned', [pid], cell=p.cell, target=target, reason='blocked')
                return False
        return False

    def _free_kg(self, holder):
        inv = self.e.inventory; h = inv.holders[holder]
        if h.capacity_kg is None: return math.inf
        extra = sum(l.qty * self.e.cfg.item(l.item).get('carry_cap_add', 0.) for l in inv.lots.values() if l.holder == holder) if holder.startswith('p:') else 0.
        return max(0., h.capacity_kg + extra - inv.weight(holder))

    def _food(self, holder, owner):
        return [l for l in self.e.inventory.lots.values() if l.holder == holder and l.owner == owner and self.e.cfg.item(l.item)['kind'] == 'food']

    def _goods(self, holder, owner):
        return [l for l in self.e.inventory.lots.values() if l.holder == holder and l.owner == owner and self.e.cfg.item(l.item)['kind'] in ('food','material','coin')]

    def _at_holder(self, p, holder, lot=None):
        if holder == f'p:{p.id}': return True
        if holder == f'b:{p.place}' and p.place >= 0:
            b = self.e.places[p.place]
            return b.destroyed_day is None and b.owner in self.e.people.owners(p)
        return bool(lot and holder == f'c:{p.cell}' and p.place >= 0 and lot.ground_place == p.place)

    def move_food(self, pid, src, dst, owner, limit=None):
        """Custody-only pickup/deposit at the exact place, with capacity/time preflight."""
        p = self._person(pid); e = self.e
        if pid in self.journeys or owner not in e.people.owners(p): raise ValueError('in transit or unauthorized owner')
        if src == dst or (src != f'p:{pid}' and dst != f'p:{pid}'):
            raise ValueError('goods must physically pass through their carrier')
        if dst not in e.inventory.holders or not self._at_holder(p, dst): raise ValueError('destination not accessible')
        lots = sorted(self._goods(src, owner), key=lambda l:l.id)
        if any(not self._at_holder(p, src, l) for l in lots): raise ValueError('source not at exact place')
        kg = min(self._free_kg(dst), self.budgets[pid] * self.cfg['handling_kg_per_hour'])
        left = math.inf if limit is None else limit
        if left < 0: raise ValueError('negative transfer limit')
        legs = []; total = 0; mass = 0.
        for lot in lots:
            w = e.cfg.item(lot.item)['weight']
            q = int(min(lot.qty, math.floor((kg + 1e-10)/w), left))
            if q > 0:
                legs.append(Leg(lot.id, q, dst, lot.owner)); kg -= q*w; mass += q*w; left -= q; total += q
        if not legs: return 0
        e.inventory.transfer(self.day, legs, actors=[pid], cell=p.cell, place=p.place, reason='haul')
        self._spend(pid, mass / self.cfg['handling_kg_per_hour'], 'haul')
        return total

    def field_work(self, pid, activity, hours=None):
        p = self._person(pid); e = self.e
        if pid not in self.assignments: raise ValueError('unassigned worker')
        f = self.farms[self.assignments[pid]]; b = e.places[f.place]; cell = b.cell
        if pid in self.journeys or (p.cell, p.place) != (cell, f.place) or b.destroyed_day is not None:
            raise ValueError('work requires presence at intact field')
        if not p.role or not e.people.can_use_role(p, p.role, self.day): raise ValueError('worker role no longer eligible')
        role = e.cfg.role(p.role)
        if role['workplace'] != b.kind or e.cells.crop['item'] not in role['produces']: raise ValueError('role no longer supports field work')
        if b.owner != f.owner or f.owner not in e.people.owners(p): raise ValueError('farm ownership changed')
        cr = e.cells.crop; year = self.day // 365
        if activity == 'plant':
            if season_of(self.day) != cr['plant_season'] or e.cells.planted[cell] or f.last_plant_year == year: return 0.
            key = 'plant_hours'
        elif activity == 'harvest':
            if season_of(self.day) != cr['harvest_season'] or not e.cells.planted[cell]: return 0.
            key = 'harvest_hours'
        else: raise ValueError('unknown field activity')
        need = f.fields * cr['hectares_per_field'] * self.cfg[f'{activity}_labor_days_per_ha'] * self.cfg['activity_hours_per_day']
        hours = self.budgets[pid] if hours is None else float(hours)
        if not math.isfinite(hours) or hours < 0: raise ValueError('invalid labor hours')
        used = min(hours, self.budgets[pid], max(0., need - getattr(f, key)))
        if used <= 1e-8: return 0.
        self._spend(pid, used, activity); setattr(f, key, getattr(f, key) + used)
        self.labor.append({'day':self.day, 'person':pid, 'farm':f.place, 'activity':activity, 'hours':used})
        if getattr(f, key) >= need - 1e-7:
            if activity == 'plant':
                e.cells.plant(self.day, np.array([cell]), e.ledger, actors=[pid], place=f.place)
                f.last_plant_year = year
            else:
                e.inventory.receive_harvest(self.day, e.cells, [cell], owner=f.owner, place=f.place, actors=[pid])
            setattr(f, key, 0.)
        return used

    def pantry_need(self, f):
        e = self.e
        residents = sum(p.alive and p.home_place == f.home and f.owner in e.people.owners(p) for p in e.people.rows.values())
        target = residents * e.cfg.derived['consumption_units_per_day'] * self.cfg['pantry_days']
        stock = sum(l.qty for l in self._food(f'b:{f.home}', f.owner))
        return max(0, target - stock)

    def keep(self, pid):
        """Keeper routine (worldgen-v1): the pantry-refill half of `automatic` for a household with no field work.
        Carried food goes home; if the pantry is short and the store has food, fetch it. Nothing else."""
        e = self.e; p = self._person(pid); h = self.households[self.keepers[pid]]
        self.actions[pid] = 'rest'; unit = e.cfg['units']['food_unit_g'] / 1000
        if pid in self.journeys and not self.move(pid, self.journeys[pid].target): return
        for _ in range(6):
            if self.budgets[pid] <= 1e-7: return
            before = self.budgets[pid]; carried = self._food(f'p:{pid}', h.owner); need = self.pantry_need(h)
            if carried and self._free_kg(f'b:{h.home}') >= unit:
                if e.places[h.home].destroyed_day is not None: break
                if not self.move(pid, h.home): return
                if not self.move_food(pid, f'p:{pid}', f'b:{h.home}', h.owner): break
            elif not carried and need > 0 and h.home != h.store and e.places[h.store].destroyed_day is None \
                    and self._food(f'b:{h.store}', h.owner) and self._free_kg(f'b:{h.home}') >= unit:
                if not self.move(pid, h.store): return
                if not self.move_food(pid, f'b:{h.store}', f'p:{pid}', h.owner, need): break
            else: break
            if self.budgets[pid] >= before - 1e-8: break
        if p.place != h.home and pid not in self.journeys and e.places[h.home].destroyed_day is None: self.move(pid, h.home)

    def rebuildable(self, place):
        """A burned building can be rebuilt by its household from labor alone only if its catalog row needs no
        materials (a farm). Timber/stone buildings need a material economy that does not exist yet (open finding)."""
        spec = self.e.cfg.building(self.e.places[place].kind)
        return not spec['materials']

    def rebuild(self, pid, f):
        """Spend today's remaining hours (less the walk home) rebuilding the worker's burned farm.
        Cost is the catalog `labor_days` x activity_hours_per_day; nothing is created from nothing."""
        e = self.e; b = e.places[f.place]; spec = e.cfg.building(b.kind)
        if not self.move(pid, f.place): return 0.
        need = spec['labor_days'] * self.cfg['activity_hours_per_day']
        home_hours, _, _ = self._route(pid, f.home)
        used = min(max(0., self.budgets[pid] - home_hours), max(0., need - f.rebuild_hours))
        if used <= 1e-8: return 0.
        self._spend(pid, used, 'build'); f.rebuild_hours += used
        self.labor.append({'day': self.day, 'person': pid, 'farm': f.place, 'activity': 'rebuild', 'hours': used})
        if f.rebuild_hours >= need - 1e-7:
            b.destroyed_day = None; b.condition = 1.0; f.rebuild_hours = 0.
            e.inventory.holders[f'b:{b.id}'].store = 'store' in spec['affordances']
            e.ledger.event(self.day, 'repaired', [pid], cell=b.cell, place=b.id, building=b.id, labor_hours=need)
        return used

    def automatic(self, pid):
        e = self.e; p = self._person(pid); f = self.farms[self.assignments[pid]]
        self.actions[pid] = 'rest'
        if pid in self.journeys:
            target = self.journeys[pid].target
            if not self.move(pid, target): return
        # A household's fixed logistical routine, not a general choice engine.
        work_day = e.people.work_due(self.day, p)
        for _ in range(12):
            if self.budgets[pid] <= 1e-7: return
            before = self.budgets[pid]
            carried = self._food(f'p:{pid}', f.owner)
            need = self.pantry_need(f)
            has_room = (need > 0 and self._free_kg(f'b:{f.home}') >= e.cfg['units']['food_unit_g']/1000) or self._free_kg(f'b:{f.store}') >= e.cfg['units']['food_unit_g']/1000
            if carried and has_room:
                dest = f.home if need > 0 and self._free_kg(f'b:{f.home}') >= e.cfg['units']['food_unit_g']/1000 else f.store
                if e.places[dest].destroyed_day is not None: break
                if not self.move(pid, dest): return
                moved = self.move_food(pid, f'p:{pid}', f'b:{dest}', f.owner, need if dest == f.home else None)
                if not moved: break
            elif not carried and need > 0 and f.home != f.store and self._food(f'b:{f.store}', f.owner) and self._free_kg(f'b:{f.home}') >= e.cfg['units']['food_unit_g']/1000:
                if not self.move(pid, f.store): return
                if not self.move_food(pid, f'b:{f.store}', f'p:{pid}', f.owner, need): break
            elif work_day and e.places[f.place].destroyed_day is not None and self.rebuildable(f.place) and e.places[f.home].destroyed_day is None:
                if self.rebuild(pid, f) == 0: break
            elif work_day and e.places[f.place].destroyed_day is None and e.places[f.store].destroyed_day is None:
                cell = e.places[f.place].cell
                ground = self._food(f'c:{cell}', f.owner)
                action = 'harvest' if e.cells.planted[cell] and season_of(self.day) == e.cells.crop['harvest_season'] else 'plant' if not e.cells.planted[cell] and f.last_plant_year != self.day // 365 and season_of(self.day) == e.cells.crop['plant_season'] else None
                if not action and not carried and ground and self._free_kg(f'b:{f.store}') >= e.cfg['units']['food_unit_g']/1000:
                    if not self.move(pid, f.place): return
                    if not self.move_food(pid, f'c:{cell}', f'p:{pid}', f.owner): break
                else:
                    action = 'harvest' if e.cells.planted[cell] and season_of(self.day) == e.cells.crop['harvest_season'] else 'plant' if not e.cells.planted[cell] and f.last_plant_year != self.day // 365 and season_of(self.day) == e.cells.crop['plant_season'] else None
                    if not action: break
                    if not self.move(pid, f.place): return
                    home_hours, _, _ = self._route(pid, f.home)
                    available = max(0., self.budgets[pid] - home_hours)
                    if self.field_work(pid, action, available) == 0: break
            else: break
            if self.budgets[pid] >= before - 1e-8: break
        if p.place != f.home and pid not in self.journeys and e.places[f.home].destroyed_day is None:
            self.move(pid, f.home)

    def step(self, day):
        self.begin(day)
        for pid in sorted(self.assignments):
            p = self.e.people.rows[pid]
            if p.alive and not p.busy and pid not in self.e.agency.agents: self.automatic(pid)
        for pid in sorted(self.keepers):
            p = self.e.people.rows[pid]
            if p.alive and not p.busy and pid not in self.e.agency.agents and pid not in self.assignments: self.keep(pid)

    def state(self):
        return {'farms':{k:asdict(v) for k,v in self.farms.items()}, 'assignments':self.assignments,
                'households':{k:asdict(v) for k,v in self.households.items()}, 'keepers':self.keepers,
                'journeys':{k:asdict(v) for k,v in self.journeys.items()}, 'day':self.day,
                'budgets':self.budgets, 'actions':self.actions}

"""World setup generator (worldgen-v1): turns [population] and [setup] into households, people,
buildings, stores, coin, media and roads on day 0. Everything is drawn from rng.one('setup', 0, k)
with a fixed k per stage, so a world is a pure function of (config, seed): building it twice gives
the same state fingerprint. Setup emits no events (it is not history; run.origin "established").

Rules the generator keeps to:
  * No building kind, role id or capability id is named in this file. Which buildings a household
    gets comes from [setup.household_buildings]; the workplace comes from the role's `workplace`
    field; which capabilities a role needs comes from the role's `requires` field; which medium a
    recording role carries comes from [setup.media_by_role].
  * Every generated person is named (unnamed people neither witness nor remember).
  * Nothing here is a resolver: all state goes through engine.seed_place / add_person /
    inventory.create(source='setup') / farming.register / farming.assign.
  * Whatever could not be sited or filled is counted in the returned report, never silently dropped.
"""
from __future__ import annotations
import math
import numpy as np
from .world import T

# Age mixtures per population.age_dist (closed vocabulary validated by config). Years.
# Each row: (p_child, p_youth, p_adult, p_elder) for household members after the head/spouse.
_AGE_MIX = {'premodern': (0.55, 0.20, 0.15, 0.10), 'young': (0.70, 0.20, 0.10, 0.00), 'flat': (0.25, 0.25, 0.25, 0.25)}
_SYL = ('ka', 'ri', 'to', 'ma', 'el', 'on', 'sa', 've', 'du', 'ni', 'ro', 'la', 'be', 'tu', 'is', 'or', 'wa', 'ye', 'mo', 'ha')


def _feeds(cfg, role):
    """Does this role produce any item of kind food? Catalog-driven; no role or item id named here."""
    return any(cfg.item(i)['kind'] == 'food' for i in role['produces'])


def _name(gen, used):
    for _ in range(50):
        n = ''.join(_SYL[int(i)] for i in gen.integers(0, len(_SYL), size=int(gen.integers(2, 4)))).capitalize()
        if n not in used: used.add(n); return n
    n = f'P{len(used)}'; used.add(n); return n


def _lognormal_sigma(gini):
    """Lognormal sigma whose Gini equals `gini`: G = 2 Phi(sigma/sqrt2) - 1."""
    from statistics import NormalDist
    g = min(max(gini, 0.0), 0.95)
    return math.sqrt(2) * NormalDist().inv_cdf((g + 1) / 2)


class SetupError(ValueError):
    pass


def generate(e):
    """Populate an empty day-0 engine. Returns a plain-JSON report."""
    if e.day != 0 or e.places or e.people.rows: raise SetupError('generate() needs an empty day-0 engine')
    cfg = e.cfg; pop = cfg['population']; st = cfg['setup']; w = e.world
    stage_days = cfg.derived['stage_days']; order = cfg.derived['stage_order']
    report = {'clusters': [], 'households': 0, 'people': 0, 'skipped_buildings': {}, 'clipped_food': 0, 'subsistence': {},
              'roles': {}, 'dropped_roles': {}, 'capabilities': {}, 'coin_classes': {}, 'media': 0, 'road_cells': 0,
              'notable': [], 'warnings': []}
    spread = max(1, int(round(pop['cluster_spread_km'] / cfg['units']['cell_km'])))

    # ---- 1. cluster seeds: mainland, buildable, weighted by terrain preference (+ water adjacency)
    gen = e.rng.one('setup', 0, 1)
    buildable = np.array([cfg['world']['buildable'][name] for name in sorted(T, key=T.get)])[w.terrain]
    land = np.flatnonzero(w.mainland() & buildable)
    if len(land) == 0: raise SetupError('no buildable mainland')
    prefs = pop['cluster_terrain_prefs']; inv = {v: k for k, v in T.items()}
    weight = np.array([prefs.get(inv[int(w.terrain[c])], 0.0) * (prefs.get('water_adjacent', 1.0) if w.water_adjacent[c] else 1.0) for c in land])
    if weight.sum() <= 0: raise SetupError('cluster_terrain_prefs give every buildable cell weight 0')
    seeds = []
    for k in range(pop['clusters']):
        wt = weight.copy()
        if seeds:
            d = np.min(np.stack([w.hex_distance(np.full(len(land), s), land) for s in seeds]), axis=0)
            wt = np.where(d >= 2 * spread + 1, wt, 0.0)
            if wt.sum() <= 0: wt = np.where(d >= spread + 1, weight, 0.0)
            if wt.sum() <= 0: wt = weight.copy(); report['warnings'].append(f'cluster {k}: no separated site, overlapping')
        seeds.append(int(land[int(gen.choice(len(land), p=wt / wt.sum()))]))

    # ---- 2. household sizes and cluster membership
    gen = e.rng.one('setup', 0, 2)
    sizes = []; remaining = pop['initial']; dist = np.array(pop['household_size_dist'])
    while remaining > 0:
        s = int(gen.choice(len(dist), p=dist)) + 1; s = min(s, remaining); sizes.append(s); remaining -= s
    cluster_of = [int(gen.choice(len(seeds), p=np.array(pop['cluster_weights']))) for _ in sizes]
    for k, s in enumerate(seeds):
        report['clusters'].append({'seed_cell': s, 'households': sum(1 for c in cluster_of if c == k)})

    # ---- 3. per-household: cell, buildings, people, roles, capabilities, food, coin, media
    hb = st['household_buildings']; media_by_role = st['media_by_role']; lit = st['literacy_by_role']
    roles = cfg.catalog['roles']; mix = pop['roles_mix']; role_ids = sorted(mix); role_p = np.array([mix[r] for r in role_ids])
    sigma = _lognormal_sigma(pop['wealth_gini']); cls_names = sorted(st['coin_by_wealth'], key=lambda k: st['coin_by_wealth'][k])
    # class quantiles by wealth rank: lowest class holds the bottom 40%, the top class the top 2%
    cuts = np.array([0.4, 0.85, 0.98][:max(0, len(cls_names) - 1)])
    demand = cfg.derived['consumption_units_per_day']; unit_kg = cfg['units']['food_unit_g'] / 1000
    food_item = next(i for i, it in sorted(cfg.catalog['items'].items()) if it['kind'] == 'food' and it['spoil_per_day'] == min(x['spoil_per_day'] for x in cfg.catalog['items'].values() if x['kind'] == 'food'))
    coin_item = next(i for i, it in sorted(cfg.catalog['items'].items()) if it['kind'] == 'coin')
    used_names = set(); farm_cells = set(); first_adult_by_cluster = {}
    for hid, size in enumerate(sizes):
        gen = e.rng.one('setup', 0, 100 + hid); k = cluster_of[hid]; owner = f'h:{hid}'
        # cell near the seed
        d = w.hex_distance(np.full(len(land), seeds[k]), land); cand = land[d <= spread]
        wt = weight[d <= spread]; cell = int(cand[int(gen.choice(len(cand), p=wt / wt.sum()))])
        # people: head, optional spouse, then the rest by age mix
        members = []
        head_age = int(gen.integers(stage_days['adult'] // 365, stage_days['elder'] // 365 - 5))
        members.append(('adult', head_age))
        if size >= 2 and pop['family_seed']: members.append(('spouse', max(stage_days['adult'] // 365, head_age + int(gen.integers(-6, 7)))))
        mixp = np.array(_AGE_MIX[pop['age_dist']])
        bands = ['child', 'youth', 'adult', 'elder']
        while len(members) < size:
            band = bands[int(gen.choice(4, p=mixp / mixp.sum()))]
            lo = stage_days[band] // 365; hi = (stage_days[order[order.index(band) + 1]] // 365 if band != order[-1] else 80)
            if band == 'child' and 'infant' in stage_days: lo = 0
            members.append((band, int(gen.integers(lo, max(lo + 1, hi)))))
        # roles for adults, capabilities, literacy
        people = []; head_role = None
        for i, (band, age) in enumerate(members):
            born = -age * 365 - int(gen.integers(0, 365)); born = max(born, -200 * 365)
            adult = -born >= stage_days['youth']
            role = None; caps = set()
            if adult:
                role = role_ids[int(gen.choice(len(role_ids), p=role_p))]
                if role == 'other' or role not in roles: role = None
                if role is not None and 'min_stage' in roles[role]: pass
                # literacy / prerequisite roll: role-specific rate, else anyone
                p_cap = lit.get(role, lit['anyone']) * st['literacy_scale'] if role else lit['anyone'] * st['literacy_scale']
                if gen.random() < p_cap:
                    caps |= set(roles[role]['requires']) if role and roles[role]['requires'] else set(c for c in cfg['capabilities'] if c != 'teach_weight' and cfg['capabilities'][c]['needs_medium'])
                if role is not None and not set(roles[role]['requires']) <= caps:
                    report['dropped_roles'][role] = report['dropped_roles'].get(role, 0) + 1; role = None
            caps = {c for c in caps if c in cfg['capabilities'] and c != 'teach_weight'}
            name = _name(gen, used_names)
            # Stable family seed: head/spouse are complementary; all other sexes are keyed draws.
            sex = 'male' if i == 0 else 'female' if i == 1 and band == 'spouse' else ('female' if gen.random() < .5 else 'male')
            people.append(dict(born=born, role=role, caps=sorted(caps), name=name, band=band, sex=sex))
            if i == 0: head_role = role
        # subsistence: with no market, a household nobody in which produces food lives on its buffer
        # and starves (worldgen-v1 finding). [setup] subsistence_role names the fallback producer role;
        # it goes to the first non-head adult, else the head. Counted, never silent.
        sub = st['subsistence_role']
        if sub and not any(q['role'] and _feeds(cfg, roles[q['role']]) for q in people):
            adults = [q for q in people if -q['born'] >= stage_days['youth']]
            pick = next((q for q in adults[1:] if not q['role']), None) or next((q for q in adults[1:]), None) or (adults[0] if adults else None)
            if pick is not None and set(roles[sub]['requires']) <= set(pick['caps']):
                key = 'replaced' if pick['role'] else 'filled'
                report['subsistence'][key] = report['subsistence'].get(key, 0) + 1
                pick['role'] = sub
                if pick is people[0]: head_role = sub
            else:
                report['subsistence']['unfilled'] = report['subsistence'].get('unfilled', 0) + 1
        # buildings: table rows for anyone + head role, plus the workplace of every food-producing member
        kinds = list(hb.get('anyone', [])) + (list(hb.get(head_role, [])) if head_role else [])
        # Sorted: a set of str iterates in per-process hash order, which once gave two processes different
        # place ids for the same seed (worldgen-v1 finding). Determinism must not depend on PYTHONHASHSEED.
        for r in sorted({head_role} | {q['role'] for q in people if q['role'] and _feeds(cfg, roles[q['role']])}, key=lambda x: (x is None, x or '')):
            wp = roles[r]['workplace'] if r else 'none'
            if wp != 'none' and wp in cfg.catalog['buildings'] and wp not in kinds: kinds.append(wp)
        built = {}
        for kind in kinds:
            spec = cfg.building(kind); target = cell
            if 'field' in spec['affordances']:
                # one farm per cell: prefer home cell, else the nearest free allowed cell inside the cluster
                allowed = [T[t] for t in spec['terrain_allow']]
                ok = lambda c: c not in farm_cells and int(w.terrain[c]) in allowed and e.cells.fields[c] == 0
                if not ok(cell):
                    near = land[np.argsort(w.hex_distance(np.full(len(land), cell), land), kind='stable')]
                    near = [int(c) for c in near if w.hex_distance(np.array([cell]), np.array([c]))[0] <= spread + 2 and ok(int(c))]
                    target = near[0] if near else None
            try:
                if target is None: raise ValueError('no free cell')
                built[kind] = e.seed_place(kind, target, owner)
                if 'field' in spec['affordances']: farm_cells.add(target)
            except (ValueError, NotImplementedError) as exc:
                report['skipped_buildings'][kind] = report['skipped_buildings'].get(kind, 0) + 1
        homes = [b for k_, b in built.items() if cfg.building(k_)['shelter'] > 0]
        if not homes: raise SetupError(f'household {hid}: [setup.household_buildings] gives no sheltered building')
        home = homes[0]
        stores = sorted((b for k_, b in built.items() if 'store' in cfg.building(k_)['affordances']), key=lambda b: -cfg.building(e.places[b].kind)['store'])
        store = stores[0]
        farm = next((b for k_, b in built.items() if 'field' in cfg.building(k_)['affordances']), None)
        if farm is not None: e.farming.register(farm, home, store)
        if home != store: e.farming.register_household(home, store)
        # add people
        pids = []
        for q in people:
            p = e.add_person(q['born'], cell, home, household_id=hid, name=q['name'], role=q['role'], capabilities=q['caps'], sex=q['sex'], estate_owner=(len(pids) == 0))
            pids.append(p.id)
            if q['role']: report['roles'][q['role']] = report['roles'].get(q['role'], 0) + 1
            for c in q['caps']: report['capabilities'][c] = report['capabilities'].get(c, 0) + 1
            if farm is not None and q['role'] and e.places[farm].kind == roles[q['role']]['workplace']:
                try: e.farming.assign(p.id, farm)
                except ValueError: pass
            if q['band'] == 'adult' and k not in first_adult_by_cluster: first_adult_by_cluster[k] = p.id
        # the first youth+ resident keeps the pantry when the household has a separate store and no farm worker to do it
        if home != store and not any(pid in e.farming.assignments for pid in pids):
            keeper = next((pid for pid in pids if e.people.at_least(e.people.rows[pid], 0, 'youth')), None)
            if keeper is not None: e.farming.assign_keeper(keeper, home)
        if pop['family_seed'] and len(pids) >= 2 and people[1]['band'] == 'spouse':
            a, b = e.people.rows[pids[0]], e.people.rows[pids[1]]; a.spouse, b.spouse = b.id, a.id
            for c in pids[2:]:
                if people[pids.index(c)]['band'] in ('child', 'youth'):
                    e.people.rows[c].parents = [a.id, b.id]; a.children.append(c); b.children.append(c)
        # food buffer in the store (clipped to capacity), coin by wealth class in the home
        want = int(size * demand * 30 * pop['food_buffer_months'])
        cap = e.inventory.holders[f'b:{store}'].capacity_kg
        room = int(max(0.0, cap - e.inventory.weight(f'b:{store}')) / unit_kg) if cap is not None else want
        qty = min(want, room)
        if qty < want: report['clipped_food'] += 1
        if qty > 0: e.inventory.create(0, food_item, qty, f'b:{store}', owner, source='setup', process='initial-buffer')
        wealth = float(np.exp(sigma * gen.standard_normal())); rank = 0.5 * (1 + math.erf(math.log(wealth) / (sigma * math.sqrt(2)))) if sigma > 0 else 0.5
        cls = cls_names[int(np.searchsorted(cuts, rank, side='right'))] if len(cls_names) > 1 else cls_names[0]
        coins = int(st['coin_by_wealth'][cls]); report['coin_classes'][cls] = report['coin_classes'].get(cls, 0) + 1
        # pocket money on the head (bounded by carry capacity), the rest in the home; both clipped to physical capacity
        coin_kg = cfg.item(coin_item)['weight']
        pocket = min(coins, st['pocket_coin'], int(max(0.0, e.inventory.holders[f'p:{pids[0]}'].capacity_kg - e.inventory.weight(f'p:{pids[0]}')) / coin_kg))
        if pocket > 0: e.inventory.create(0, coin_item, pocket, f'p:{pids[0]}', owner, source='setup', process='initial-coin')
        hcap = e.inventory.holders[f'b:{home}'].capacity_kg
        hroom = coins - pocket if hcap is None else int(max(0.0, hcap - e.inventory.weight(f'b:{home}')) / coin_kg)
        rest = min(coins - pocket, hroom)
        if rest > 0: e.inventory.create(0, coin_item, rest, f'b:{home}', owner, source='setup', process='initial-coin')
        if pocket + rest < coins: report['clipped_coin'] = report.get('clipped_coin', 0) + 1
        # media for recording roles
        if st['seed_media']:
            for pid in pids:
                r = e.people.rows[pid].role
                if r and r in cfg['records']['auto_record'] and r in media_by_role:
                    e.inventory.create(0, media_by_role[r], 1, f'p:{pid}', owner, source='setup', process='initial-media'); report['media'] += 1
        report['households'] += 1; report['people'] += len(pids)

    # ---- 4. one chronicler per cluster with probability historian_rate x literacy_scale
    gen = e.rng.one('setup', 0, 3); cr = st['chronicler_role']
    if cr and cr in roles:
        for k in range(len(seeds)):
            if k in first_adult_by_cluster and gen.random() < st['historian_rate'] * st['literacy_scale']:
                p = e.people.rows[first_adult_by_cluster[k]]
                p.capabilities |= set(roles[cr]['requires']); p.role = cr; p.role_since_day = 0
                for c in roles[cr]['requires']: report['capabilities'][c] = report['capabilities'].get(c, 0) + 1
                report['roles'][cr] = report['roles'].get(cr, 0) + 1
                if st['seed_media'] and cr in media_by_role and cr in cfg['records']['auto_record']:
                    e.inventory.create(0, media_by_role[cr], 1, f'p:{p.id}', f'h:{p.household_id}', source='setup', process='initial-media'); report['media'] += 1
                e.farming.assignments.pop(p.id, None)

    # ---- 5. roads: a chain joining each cluster seed to the nearest already-connected one
    lvl = pop['initial_road_level']
    if lvl > 0 and len(seeds) > 1:
        edges = w.edge_hours('summer'); joined = [seeds[0]]
        for s in seeds[1:]:
            arr, prev = w.dijkstra(joined, edges)
            if math.isfinite(float(arr[s])):
                for c in w.path(prev, s): w.road_level[c] = max(int(w.road_level[c]), lvl)
            joined.append(s)
        report['road_cells'] = int((w.road_level > 0).sum())

    # ---- 6. notable starters: the first `notable_starters` household heads by household id (for run.py to bind)
    heads = [pid for pid, p in sorted(e.people.rows.items()) if p.household_id is not None and p.id == min(q.id for q in e.people.rows.values() if q.household_id == p.household_id)]
    report['notable'] = heads[:st['notable_starters']]
    report['farms'] = len(e.farming.farms); report['workers'] = len(e.farming.assignments); report['places'] = len(e.places)
    return report

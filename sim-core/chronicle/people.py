"""People foundation: identity, stages, needs, capabilities and local records.

No LLM writes facts. Direct witnesses/readers can acquire ledger pointers;
observation expectations are not world-state access. Physics is daily, not
attention-tick based. The optional demography pass supplies coarse bonds, births,
background mortality, and role inheritance. Travel, markets, social edges and resolver
menus are NOT implemented here yet.
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
import math
from .inventory import InventoryError


class PeopleError(ValueError):
    pass


@dataclass
class Person:
    id: int
    born_day: int
    cell: int
    place: int = -1
    sex: str = 'unspecified'
    died_day: int | None = None
    household_id: int | None = None
    home_place: int = -1
    name: str | None = None
    role: str | None = None
    role_since_day: int = 0
    parents: list = field(default_factory=list)
    spouse: int | None = None
    children: list = field(default_factory=list)
    # None, or a durable conception record: father id and the day birth becomes due.
    pregnancy: dict | None = None
    designated_heir: int | None = None
    estate_owner: bool = False  # seeded household head; its household estate follows personal succession
    capabilities: set = field(default_factory=set)
    learning: dict = field(default_factory=dict)
    traits: tuple = ()
    needs: dict = field(default_factory=lambda: {k: 1.0 for k in ('food', 'health', 'rest', 'shelter', 'social', 'safety')})
    expectations: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)  # event id -> source/fidelity/day
    known_records: dict = field(default_factory=dict)
    known_cells: dict = field(default_factory=dict)  # cell -> first-hand discovery snapshot; personal map knowledge
    disease_state: dict = field(default_factory=dict)
    goals: list = field(default_factory=list)
    busy: str | None = None
    attention: float = 0.0
    starvation: float = 0.0
    migrate: bool = False
    last_day: int = -1
    stage_seen: str | None = None
    last_action: str = 'idle'
    last_sleep_quality: float = 1.0   # quality of last night's sleep (0..1), set by step_person
    sleep_streaks: dict = field(default_factory=dict)  # consequence row id -> consecutive nights its sleep condition held

    @property
    def alive(self):
        return self.died_day is None


class People:
    def __init__(self, cfg, inventory, ledger, streams):
        self.cfg, self.inventory, self.ledger, self.rng = cfg, inventory, ledger, streams
        self.rows: dict[int, Person] = {}
        self._taught = set()
        self._teaching_day = -1

    def add(self, born_day, cell, place=-1, *, day=0, household_id=None, name=None,
            role=None, capabilities=(), traits=(), sex='unspecified', parents=(), emit_birth=False,
            designated_heir=None, estate_owner=False):
        if type(born_day) is not int or born_day > day or type(cell) is not int or cell < 0:
            raise PeopleError('invalid birth day or cell')
        if role is not None and role not in self.cfg.catalog['roles']: raise PeopleError('unknown role')
        if sex not in ('unspecified', 'female', 'male'): raise PeopleError('invalid sex')
        if any(type(pid) is not int or pid not in self.rows for pid in parents): raise PeopleError('unknown parent')
        if emit_birth and born_day != day: raise PeopleError('born event must be on actual birth day')
        if designated_heir is not None and (type(designated_heir) is not int or designated_heir not in self.rows):
            raise PeopleError('unknown designated heir')
        known = set(self.cfg['capabilities']) - {'teach_weight'}
        if not set(capabilities) <= known: raise PeopleError('unknown capability')
        pid = len(self.rows)
        p = Person(pid, born_day, cell, place, sex=sex, household_id=household_id, home_place=place,
                   name=name, role=role, role_since_day=day, parents=list(parents), designated_heir=designated_heir,
                   estate_owner=bool(estate_owner), capabilities=set(capabilities), traits=tuple(traits))
        p.stage_seen = self.stage(p, day)
        if role is not None and not self.at_least(p, day, 'youth'):
            raise PeopleError('too young for a role')
        self.inventory.register(f'p:{pid}', self.cfg['people']['carry_cap'])
        self.rows[pid] = p
        # Being born or introduced somewhere provides durable first-hand local knowledge.
        p.known_cells[cell] = {"day": int(day), "source": "origin"}
        for parent_id in p.parents: self.rows[parent_id].children.append(pid)
        if emit_birth:
            self.ledger.event(day, 'born', [pid] + list(p.parents), cell=cell, place=place, sex=sex)
        return p

    def stage(self, p, day):
        age = day - p.born_day
        return next(s for s in reversed(self.cfg.derived['stage_order']) if age >= self.cfg.derived['stage_days'][s])

    def at_least(self, p, day, stage):
        return day - p.born_day >= self.cfg.derived['stage_days'][stage]

    def where(self, pid):
        p = self.rows[pid]
        return p.cell, p.place

    def co_present(self, a, b):
        # -1 means unspecified open air, NOT every outdoor person in the cell.
        return a.alive and b.alive and a.place >= 0 and (a.cell, a.place) == (b.cell, b.place)

    def owners(self, p):
        return {f'p:{p.id}'} | ({f'h:{p.household_id}'} if p.household_id is not None else set())

    def can_use_role(self, p, role, day):
        return self.at_least(p, day, 'youth') and set(self.cfg.role(role)['requires']) <= p.capabilities

    def set_role(self, day, pid, role, appointed=False):
        p = self._actor(pid)
        if role not in self.cfg.catalog['roles'] or not self.at_least(p, day, 'youth'):
            raise PeopleError('invalid role assignment')
        if not appointed and not self.can_use_role(p, role, day): raise PeopleError('role prerequisites missing')
        # Explicit appointment may violate prerequisites; functionality stays gated.
        p.role = role; p.role_since_day = day
        return self.ledger.event(day, 'role_changed', [pid], cell=p.cell, place=p.place, role=role, appointed=appointed)

    def _actor(self, pid, requires=None):
        p = self.rows[pid]
        if not p.alive: raise PeopleError('dead actor')
        if requires and not self.can(p, requires): raise PeopleError(f'{requires} requires an enabling capability')
        return p

    def can(self, p, primitive):
        """Does any capability this person holds enable the primitive? Capabilities are config tables; no id is named here."""
        caps = self.cfg['capabilities']
        return any(primitive in caps[c].get('enables', ()) for c in p.capabilities if c in caps)

    def witness_scope(self, e):
        """[events.witness] lookup: 'type:flag' rows match a payload flag that is True, then the type row, then default."""
        tbl = self.cfg['events']['witness']
        for k, v in e.payload.items():
            if v is True and f'{e.type}:{k}' in tbl: return tbl[f'{e.type}:{k}']
        return tbl.get(e.type, tbl['default'])

    def is_local(self, p, e):
        """Could p perceive public event e from where they are? Same place always; whole cell when the scope says so."""
        if e.cell != p.cell: return False
        return (p.place >= 0 and e.place == p.place) or self.witness_scope(e) == 'cell'

    def _accessible(self, p, lot):
        if lot.holder == f'p:{p.id}': return True
        if lot.holder == f'b:{p.place}' and p.place >= 0: return True
        if lot.holder.startswith('p:'):
            other = self.rows.get(int(lot.holder[2:]))
            return other is not None and self.co_present(p, other)
        # A ground lot has no sub-cell place identity yet: never grant access
        # just because its cell matches. Caller must resolve a pickup first.
        return False

    def _medium(self, p, lid, write=False, consent=False):
        lot = self.inventory.medium(lid)
        if not self._accessible(p, lot): raise PeopleError('medium is not co-present')
        if write and lot.owner not in self.owners(p) and not consent:
            raise PeopleError('owner consent required')
        return lot

    def teach(self, day, teacher_id, student_ids, capability, medium_id=None):
        teacher = self._actor(teacher_id)
        if capability == 'teach_weight' or capability not in self.cfg['capabilities']:
            raise PeopleError('unknown capability')
        cap = self.cfg['capabilities'][capability]
        if capability not in teacher.capabilities: raise PeopleError('teacher lacks capability')
        if cap['teachers'] != 'any_holder' and teacher.role not in cap['teachers']:
            raise PeopleError('teacher role not eligible')
        if cap['needs_medium']:
            if medium_id is None: raise PeopleError('teaching needs a medium')
            self._medium(teacher, medium_id)
            self.inventory.contents(medium_id)
        if day < self._teaching_day: raise PeopleError('teaching day went backward')
        if day > self._teaching_day:
            self._taught.clear(); self._teaching_day = day
        students = []
        for sid in sorted(set(student_ids)):
            p = self._actor(sid)
            if sid == teacher_id or not self.co_present(teacher, p): raise PeopleError('students must be co-present with teacher')
            if not self.at_least(p, day, cap['min_stage']): raise PeopleError('student below minimum stage')
            if capability not in p.capabilities and (sid, capability) not in self._taught:
                students.append(p)
        if not students: return []
        eid = self.ledger.event(day, 'teach', [teacher_id] + [p.id for p in students], cell=teacher.cell,
                                place=teacher.place, capability=capability)
        acquired = []
        for p in students:
            # Neutral trait factor until a configured trait-learning table exists.
            p.learning[capability] = p.learning.get(capability, 0.) + self.cfg['development']['stage_rate'][self.stage(p, day)]
            self._taught.add((p.id, capability)); self.remember(p, eid, day, 1., 'witnessed')
            if p.learning[capability] + 1e-9 >= cap['learn_days']:
                p.capabilities.add(capability); acquired.append(p.id)
                e = self.ledger.event(day, 'learned', [p.id], cell=p.cell, place=p.place, capability=capability)
                self.remember(p, e, day, 1., 'witnessed')
        return acquired

    def can_remember(self, p, day):
        """Memory facts exist at named and above, from childhood. One rule, used by remember and by the contact phase."""
        return p.alive and p.name is not None and self.at_least(p, day, 'child')

    def remember(self, p, eid, day, fidelity, source, *, hops=0, from_pid=None):
        if not self.can_remember(p, day): return False
        if type(eid) is not int or not 0 <= eid < len(self.ledger.events) or self.ledger.events[eid].day > day:
            raise PeopleError('memory must reference an existing non-future event')
        if not math.isfinite(fidelity) or not 0 <= fidelity <= 1: raise PeopleError('invalid fidelity')
        old = p.memory.get(eid)
        if old and old['fidelity'] >= fidelity: return False
        p.memory[eid] = {'day': day, 'fidelity': fidelity, 'source': source, 'hops': int(hops), 'from': from_pid}
        rw = self.cfg.role(p.role)['role_weight'] if p.role else 0
        limit = max(1, int(self.cfg['people']['memory_base'] + self.cfg['people']['memory_per_role_weight'] * rw))
        while len(p.memory) > limit: del p.memory[next(iter(p.memory))]
        return True

    def witness(self, day, pid, eid):
        p = self._actor(pid); e = self.ledger.events[eid]
        if e.day != day: raise PeopleError('direct witnessing must happen that day')
        participant = pid in e.actors
        if not participant and not (e.visibility == 'public' and self.is_local(p, e)): raise PeopleError('not a witness')
        return self.remember(p, eid, day, 1., 'witnessed')

    # ---- hearsay (contact-v1): memory moves between people only through tell(); words alone move nothing
    def salience(self, e):
        """[gossip.salience] lookup, same shape as [events.witness]: 'type:flag' (payload flag True), then type, then default."""
        tbl = self.cfg['gossip']['salience']
        for k, v in e.payload.items():
            if v is True and f'{e.type}:{k}' in tbl: return float(tbl[f'{e.type}:{k}'])
        return float(tbl.get(e.type, tbl['default']))

    def tellable(self, teller, listener, gen):
        """Pick ONE remembered event the listener lacks (or holds at lower fidelity than a telling would give),
        weighted by salience x hop_decay^hops. gen is the caller's keyed generator. None when nothing is worth telling."""
        g = self.cfg['gossip']; cands = []; weights = []
        for eid, m in teller.memory.items():
            offered = m['fidelity'] * g['tell_fidelity_mult']
            old = listener.memory.get(eid)
            if old is not None and old['fidelity'] >= offered: continue
            cands.append(eid); weights.append(self.salience(self.ledger.events[eid]) * g['hop_decay'] ** m['hops'])
        if not cands: return None
        w = np.asarray(weights, dtype=float)
        if w.sum() <= 0: return None
        return cands[int(gen.choice(len(cands), p=w / w.sum()))]

    def tell(self, day, teller_id, listener_id, eid, *, overheard=False):
        """Hearsay transfer primitive: a ledgered `tell` event plus a memory record in the listener at reduced
        fidelity (x tell_fidelity_mult, x crowd_fidelity when overheard), source 'hearsay', hops+1, from = teller.
        Returns whether the listener's memory actually changed."""
        t = self._actor(teller_id); l = self._actor(listener_id)
        if teller_id == listener_id: raise PeopleError('cannot tell oneself')
        m = t.memory.get(eid)
        if m is None: raise PeopleError('teller does not remember that event')
        g = self.cfg['gossip']
        fid = m['fidelity'] * g['tell_fidelity_mult'] * (g['crowd_fidelity'] if overheard else 1.0)
        self.ledger.event(day, 'tell', [teller_id, listener_id], cell=t.cell, place=t.place, visibility='private',
                          event=eid, fidelity=fid, hops=m['hops'] + 1, overheard=bool(overheard))
        return self.remember(l, eid, day, fid, 'hearsay', hops=m['hops'] + 1, from_pid=teller_id)

    def observe(self, day, pid, observable, sample, event_id=None):
        p = self._actor(pid)
        if not math.isfinite(sample): raise PeopleError('nonfinite observation')
        state = p.expectations.get(observable)
        surprise = False
        if state is None:
            p.expectations[observable] = [float(sample), 0., 1]
        else:
            mean, var, count = state; delta = sample - mean
            surprise = abs(delta) > self.cfg['observation']['surprise_k'] * max(math.sqrt(var), 1e-6)
            alpha = self.cfg['observation']['obs_alpha']
            state[:] = [mean + alpha * delta, (1-alpha) * (var + alpha*delta*delta), count+1]
        if surprise and event_id is not None: self.witness(day, pid, event_id)
        return surprise

    def record(self, day, pid, lid, entries, overwrite=False, consent=False):
        p = self._actor(pid, requires='record'); self._medium(p, lid, write=True, consent=consent)
        out = []
        for entry in entries:
            entry = dict(entry)
            if entry.get('type') == 'cite':
                memory = p.memory.get(entry.get('event_id'))
                if not memory: raise PeopleError('cannot cite an unknown event')
                entry['fidelity'] = memory['fidelity']  # never trust supplied fidelity
            elif entry.get('type') != 'note':
                raise PeopleError('snapshots are reserved for engine serialization')
            out.append(entry)
        self.inventory.append_entries(lid, out, overwrite)
        eid = self.ledger.event(day, 'record', [pid], cell=p.cell, place=p.place, medium=lid, count=len(out))
        p.known_records[lid] = p.cell
        return eid

    def read_record(self, day, pid, lid):
        p = self._actor(pid, requires='read'); self._medium(p, lid)
        entries = self.inventory.contents(lid); p.known_records[lid] = p.cell
        for entry in entries:
            if entry['type'] == 'cite': self.remember(p, entry['event_id'], day, entry['fidelity'], f'read:{lid}')
        self.ledger.event(day, 'read', [pid], cell=p.cell, place=p.place, medium=lid)
        return entries

    def seal(self, day, pid, lid, consent=False):
        p = self._actor(pid); lot = self._medium(p, lid, write=True, consent=consent)
        wax = self.cfg['records']['seal_requires_item']
        if wax:
            stock = next((l for l in self.inventory.lots.values() if l.item == wax and l.owner in self.owners(p) and self._accessible(p, l)), None)
            if stock is None: raise PeopleError('seal material required')
            self.inventory.destroy(day, stock.id, 1, 'consume', 'seal')
        lot.sealed_by, lot.opened_by = pid, None
        return self.ledger.event(day, 'seal', [pid], cell=p.cell, place=p.place, medium=lid)

    def open_record(self, day, pid, lid, consent=False):
        p = self._actor(pid); lot = self._medium(p, lid, write=True, consent=consent)
        lot.opened_by = pid
        return self.ledger.event(day, 'open', [pid], cell=p.cell, place=p.place, medium=lid)

    def copy_record(self, day, pid, source, target, consent=False):
        p = self._actor(pid, requires='copy'); self._medium(p, source)
        dest = self._medium(p, target, write=True, consent=consent)
        if source == target: raise PeopleError('cannot copy a medium onto itself')
        spec = self.cfg.item(dest.item)
        if dest.entries and not spec['reusable']: raise PeopleError('target is not blank/reusable')
        entries = self.inventory.contents(source)[:spec['capacity']]
        g = self.rng.one(f'copy:{source}:{target}', day, pid)
        copied = [entry for entry in entries if g.random() >= self.cfg['records']['copy_loss']]
        self.inventory.append_entries(target, copied, overwrite=bool(dest.entries))
        dest.copied_from = source
        self.ledger.event(day, 'copy', [pid], cell=p.cell, place=p.place, source=source, target=target, count=len(copied))
        return len(copied)

    # ---- optional lifecycle: deliberately coarse and entirely gated by [demography].
    def _daily_p(self, annual):
        return 1.0 - (1.0 - float(annual)) ** (1.0 / 365.0)

    def end_pregnancy(self, day, mother, cause):
        """Resolve a pregnancy that cannot continue, without creating an unparented child."""
        if mother.pregnancy is None:
            return False
        father = mother.pregnancy['father']
        mother.pregnancy = None
        self.ledger.event(day, 'pregnancy_lost', [mother.id, father], cell=mother.cell,
                          place=mother.place, cause=cause)
        return True

    def lifecycle(self, day):
        """Apply mortality, public bonds, conception, and due births.
        Pregnancies are explicit state rather than same-day births. Every random draw is keyed
        by (process, day, person), so unrelated population changes do not shift outcomes."""
        dm = self.cfg['demography']
        if not dm['enabled']: return []
        dead = []
        # Mortality first: dead people cannot bond or reproduce today.
        for pid, p in sorted(self.rows.items()):
            if not p.alive: continue
            stage = self.stage(p, day)
            annual = self.cfg['population']['death_base_per_year'] * dm['mortality_mult'][stage]
            if self.rng.one('background-mortality', day, pid).random() < self._daily_p(annual):
                self.end_pregnancy(day, p, 'mother_died')
                p.died_day = day; p.needs['health'] = 0.; p.last_day = day
                self.ledger.event(day, 'died', [pid], cell=p.cell, place=p.place, cause='background')
                dead.append(pid)
        adults = [p for p in self.rows.values() if p.alive and self.at_least(p, day, 'adult')]
        # One deterministic pairing pass per household. Existing bonds require no daily roll.
        for hid in sorted({p.household_id for p in adults if p.household_id is not None}):
            group = [p for p in adults if p.household_id == hid and p.spouse is None and p.sex in ('female','male')]
            females = sorted((p for p in group if p.sex == 'female'), key=lambda p:p.id)
            males = sorted((p for p in group if p.sex == 'male'), key=lambda p:p.id)
            # A bond is a witnessed public act, not an invisible household-wide match.
            # Pair only people at the same exact place; consume the selected partner so
            # the result is deterministic without permitting multiple bonds per pass.
            available = list(males)
            for a in females:
                b = next((q for q in available if self.co_present(a, q)), None)
                if b is None: continue
                if self.rng.one('bond', day, a.id).random() < self._daily_p(dm['bond_chance_per_year']):
                    a.spouse=b.id; b.spouse=a.id
                    available.remove(b)
                    self.ledger.event(day, 'bond', [a.id,b.id], cell=a.cell, place=a.place)
        # Resolve due births before attempting new conceptions. Father identity is retained
        # from conception even if the father subsequently dies; the child remains linked to both.
        for mother in sorted(self.rows.values(), key=lambda p:p.id):
            pregnancy = mother.pregnancy
            if not mother.alive or pregnancy is None or day < pregnancy['due_day']:
                continue
            father = self.rows.get(pregnancy['father'])
            if father is None:
                self.end_pregnancy(day, mother, 'missing_father')
                continue
            sex = 'female' if self.rng.one('birth-sex', day, mother.id).random() < .5 else 'male'
            child = self.add(day, mother.cell, mother.place, day=day, household_id=mother.household_id,
                             name=f'Child{len(self.rows)}', sex=sex, parents=(mother.id, father.id), emit_birth=True)
            child.home_place = mother.home_place
            mother.pregnancy = None
            child.needs['food'] = 1.0
        lo, hi = dm['fertile_min_years'] * 365, dm['fertile_max_years'] * 365
        mothers = [p for p in self.rows.values() if p.alive and p.sex == 'female' and p.spouse is not None
                   and p.pregnancy is None and lo <= day-p.born_day <= hi]
        for mother in sorted(mothers, key=lambda p:p.id):
            father = self.rows.get(mother.spouse)
            # Conception, like a bond, is local rather than an invisible household-wide effect.
            if father is None or not father.alive or father.household_id != mother.household_id or not self.co_present(mother, father):
                continue
            if self.rng.one('conception', day, mother.id).random() >= self._daily_p(self.cfg['population']['birth_rate_per_year']):
                continue
            mother.pregnancy = {'father': father.id, 'due_day': day + dm['gestation_days']}
            self.ledger.event(day, 'conceived', [mother.id, father.id], cell=mother.cell, place=mother.place,
                              due_day=mother.pregnancy['due_day'])
        return dead

    def inherit_role(self, day, p):
        """At adulthood, inherit a viable parent role when its capability gate is met.

        Childhood and youth may retain legacy/setup roles, but people born during the
        simulation receive no automatic economic role until their adult transition.
        A failed inheritance roll or missing prerequisite deliberately leaves the
        adult roleless rather than granting fictional competence.
        """
        if not self.at_least(p, day, 'adult') or p.role is not None or not p.parents: return False
        parent = next((self.rows.get(pid) for pid in p.parents if self.rows.get(pid) and self.rows[pid].role), None)
        if parent is None: return False
        if self.rng.one('role-inherit', day, p.id).random() >= self.cfg['development']['role_inherit_p']: return False
        role = parent.role
        if role and self.can_use_role(p, role, day):
            self.set_role(day, p.id, role)
            return True
        return False

    def work_due(self, day, p):
        from .cells import season_of
        if not p.alive or p.role is None or not self.can_use_role(p, p.role, day): return False
        work = self.cfg['time']['work_days'].get(p.role, self.cfg['time']['work_days']['default'])
        if isinstance(work, dict): work = work[season_of(day)]
        work *= self.cfg['development']['stage_work_mult'][self.stage(p, day)]
        return self.rng.one('work-allocation', day, p.id).random() < work

    # ---- sleep consequences (rows from [sleep.consequences]; the engine never names a row)
    def _sleep_row_holds(self, p, row, season):
        """Quality + season conditions of a row against last night (not rest or streak)."""
        q = p.last_sleep_quality
        if 'quality_below' in row and not q < row['quality_below']: return False
        if 'quality_at_least' in row and not q >= row['quality_at_least']: return False
        if 'seasons' in row and season not in row['seasons']: return False
        return True

    def _update_sleep_streaks(self, p, season):
        for cid, row in self.cfg['sleep']['consequences'].items():
            if 'consecutive_nights' not in row: continue
            p.sleep_streaks[cid] = p.sleep_streaks.get(cid, 0) + 1 if self._sleep_row_holds(p, row, season) else 0

    def sleep_effects(self, p, season):
        """Matched consequence rows for this person given last night's sleep and current rest.
        Returns (hours_mult, health_drain, cause, matched_ids). Conditions within a row AND together;
        hours multipliers multiply across rows, drains add, cause is the last draining row's label."""
        hours_mult, drain, cause, matched = 1.0, 0.0, None, []
        rest = p.needs['rest']
        for cid, row in self.cfg['sleep']['consequences'].items():
            if not self._sleep_row_holds(p, row, season): continue
            if 'rest_below' in row and not rest < row['rest_below']: continue
            if 'rest_at_least' in row and not rest >= row['rest_at_least']: continue
            if 'consecutive_nights' in row and p.sleep_streaks.get(cid, 0) < row['consecutive_nights']: continue
            matched.append(cid)
            hours_mult *= row.get('hours_mult', 1.0)
            if 'health_drain' in row:
                drain += row['health_drain']; cause = row['cause']
        return hours_mult, drain, cause, matched

    def step_person(self, day, pid, food_holders=(), sheltered=False, resolved_action=None, *,
                    sleep_quality=None, season=None):
        """End-of-day physiology. `sleep_quality` (0..1) is the engine's resolved quality for tonight;
        the legacy positional `sheltered` bool maps to 1.0/0.0 when it is omitted."""
        from .cells import season_of
        p = self._actor(pid)
        if day <= p.last_day: raise PeopleError('person already processed or day reversed')
        # External engine resolves accessible stores; this method only adds self.
        demand = self.cfg.derived['consumption_units_per_day']
        ate = self.inventory.eat(day, [f'p:{pid}'] + list(food_holders), self.owners(p), demand)
        p.needs['food'] = ate / demand
        p.starvation = max(0., p.starvation + (1. - ate/demand) if ate < demand else p.starvation - 1.)
        stage = self.stage(p, day)
        mult = self.cfg['needs']['age_mult'].get(stage, 1.)
        working = self.work_due(day, p)
        p.last_action = resolved_action or p.busy or ('work' if working else 'rest')
        for need, drift in self.cfg['needs']['drift_per_day'].items():
            # A day spent resting carries no rest drift; recovery itself comes from the night's sleep below.
            if need == 'rest' and p.last_action == 'rest': continue
            p.needs[need] = min(1., max(0., p.needs[need] - drift * mult))
        # ---- night: sleep quality drives shelter need and rest recovery (terms from [sleep])
        sl = self.cfg['sleep']
        q = float(sheltered) if sleep_quality is None else float(sleep_quality)
        if not math.isfinite(q) or not 0. <= q <= 1.: raise PeopleError('sleep quality must be in [0,1]')
        season = season or season_of(day)
        p.last_sleep_quality = q
        p.needs['shelter'] = min(1., q * sl['shelter_need_per_quality'])
        p.needs['rest'] = min(1., p.needs['rest'] + sl['rest_recovery_per_quality'] * q * mult)
        self._update_sleep_streaks(p, season)
        _, drain, drain_cause, _ = self.sleep_effects(p, season)
        # Explicit provisional nutrition rule: deficit-equivalent days / configured
        # starvation horizon. Not a calibrated demographic or medical model.
        cap = max(0., 1 - p.starvation / self.cfg['needs']['starve_days'])
        p.needs['health'] = min(cap, p.needs['health'] + (1/self.cfg['needs']['starve_days'] if ate == demand else 0))
        if drain > 0: p.needs['health'] = max(0., p.needs['health'] - drain)
        p.migrate = p.needs['food'] < self.cfg['needs']['thresholds']['critical']
        if stage != p.stage_seen:
            self.ledger.event(day, 'stage', [pid], cell=p.cell, place=p.place, stage=stage)
            p.stage_seen = stage
            if stage == 'adult' and self.cfg['demography']['enabled']: self.inherit_role(day, p)
        if p.needs['health'] <= 1e-12:
            p.needs['health'] = 0.; self.end_pregnancy(day, p, 'mother_died'); p.died_day = day
            # Cause: starvation when the nutrition cap closed; otherwise the draining consequence row's label.
            cause = 'starvation' if cap <= 1e-12 or drain <= 0 else drain_cause
            self.ledger.event(day, 'died', [pid], cell=p.cell, place=p.place, cause=cause)
            # Estate stays physically present; no disappearance before succession.
        p.last_day = day
        return ate

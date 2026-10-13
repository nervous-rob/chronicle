"""chronicle.frames - read-only export for the replay viewer (viewer.html).

Nothing here writes engine state, draws random numbers or touches the ledger, so a run with frames on has the same
digest as one with them off (tests/test_frames.py checks that). Three files, all plain JSON:

  world.json     once per run: grid size, terrain, rivers, roads, regions, buildings, the role list, people at export time.
  frames.jsonl   one line per day (state at the END of that day):
                   {"day", "season", "people": [[id, cell, place, role, food, health, rest, action, alive], ...],
                    "born": [[id, name, household, sex, born_day, home_place], ...]  (people not yet exported),
                    "burning": [cell...], "planted": [cell...], "destroyed": [place...],
                    "events": [[type, [actors], cell, place], ...]}
                 people rows: role is an index into world.json "roles" (-1 none); needs are 0..1; alive is 0/1. Dead people
                 appear once, on the day they died, with alive 0. Event types in QUIET are left out (they are in the ledger).
  context.jsonl  one line per policy call: the menu kinds, body bands and surroundings the person was shown. decisions.jsonl
                 keeps only a hash of the observation, so this is the only record of what the menu was.

Roads and bridges are exported once (nothing builds them yet); if a build system lands, add them to the frame.
"""
from __future__ import annotations
import numpy as np
from .config import TERRAINS
from .cells import season_of

# High-volume bookkeeping events that would swamp the viewer (movement, goods transfers, fire front steps).
QUIET = frozenset({'transfer', 'arrived', 'departed', 'passed_through', 'fire_spread'})


def _person_static(p):
    return [p.id, p.name, p.household_id, p.sex, p.born_day, p.home_place]


class FrameWriter:
    def __init__(self, engine):
        self.e = engine
        self.roles = list(engine.cfg.catalog['roles'])
        self.role_ix = {r: i for i, r in enumerate(self.roles)}
        self.seen = set()

    def world_doc(self):
        e = self.e; w = e.world
        return {'format': 'chronicle-world-1', 'run_id': e.cfg.run_id, 'seed': e.cfg.seed, 'day': e.day,
                'w': int(w.w), 'h': int(w.h), 'cell_km': float(w.cell_km), 'terrains': list(TERRAINS),
                'terrain': ''.join(str(int(t)) for t in w.terrain),
                'elevation': [int(round(float(v) * 99)) for v in w.elevation],
                'region': [int(v) for v in w.region],
                'rivers': [int(i) for i in np.flatnonzero(w.river)],
                'roads': [[int(i), int(w.road_level[i])] for i in np.flatnonzero(w.road_level > 0)],
                'bridges': [int(i) for i in np.flatnonzero(w.bridge)], 'ferries': [int(i) for i in np.flatnonzero(w.ferry)],
                'roles': self.roles,
                'places': [{'id': b.id, 'kind': b.kind, 'cell': b.cell, 'owner': b.owner, 'destroyed_day': b.destroyed_day}
                           for b in e.places.values()]}

    def frame(self, day):
        """State at the end of `day`. Call right after e.step() for that day."""
        e = self.e; P = e.people; rows = []; born = []
        for pid, p in sorted(P.rows.items()):
            if pid not in self.seen: born.append(_person_static(p)); self.seen.add(pid)
            if p.alive or p.died_day == day:
                rows.append([pid, p.cell, p.place, self.role_ix.get(p.role, -1), round(float(p.needs['food']), 2),
                             round(float(p.needs['health']), 2), round(float(p.needs['rest']), 2), p.last_action, 1 if p.alive else 0])
        c = e.cells
        events = [[ev.type, list(ev.actors), ev.cell, ev.place] for ev in e.ledger.events_on(day) if ev.type not in QUIET]
        return {'day': day, 'season': season_of(day), 'people': rows, 'born': born,
                'burning': [int(i) for i in np.flatnonzero(c.burning)], 'planted': [int(i) for i in np.flatnonzero(c.planted)],
                'destroyed': [b.id for b in e.places.values() if b.destroyed_day is not None], 'events': events}


class ContextTap:
    """Wraps a policy and remembers what each person was shown. Pure observation: decide() returns the inner policy's
    answer untouched, and every other attribute (name, last_source, ...) is the inner policy's."""
    def __init__(self, inner):
        self._inner = inner; self.records = []
    def __getattr__(self, name):
        return getattr(self._inner, name)
    def decide(self, packet):
        s = packet['self']; h = packet['here']
        self.records.append({'day': packet['day'], 'person': s['id'], 'name': s['name'], 'role': s['role'], 'menu': [m['kind'] for m in packet['menu']],
                             'body': packet['body'], 'place_kind': h['place_kind'], 'cell': h['cell'], 'company': [c['id'] for c in h['company']],
                             'surroundings': h['surroundings'], 'hours_left': s['hours_left'], 'last_action': s['last_action'],
                             'plan': packet['plan'], 'memories': len(packet['memory'])})
        return self._inner.decide(packet)
    def drain(self):
        out, self.records = self.records, []
        return out

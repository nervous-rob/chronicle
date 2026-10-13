"""Attention (increment F, part 2): who the model is asked about today, and the day driver that binds them.

Only K people a day are consulted; everyone else keeps the engine's routine (farm work, pantry keeping). K and the
scoring weights come from [attention]. The score uses only cheap engine state: deviation (needs below the low
threshold), social (company in the same cell), role (holds a role) and debt (days since last consulted, derived from
the decision log so nothing new is checkpointed). Ties break on person id, so selection is deterministic.
A person with a plan in flight stays bound: plan days cost no model call.
"""
from __future__ import annotations
from collections import Counter


class Attention:
    def __init__(self, engine):
        self.e = engine; self.last = {}
        self.note(engine.agency.log)

    def note(self, recs):
        for r in recs: self.last[r['person']] = max(self.last.get(r['person'], -1), r['day'])

    def k(self, alive):
        a = self.e.cfg['attention']
        return min(a['k_cap'], max(a['k_per_tick'], round(a['k_per_pop'] * alive)))

    def scores(self):
        e = self.e; cfg = e.cfg; a = cfg['attention']; w = a['weights']; day = e.day
        low = cfg['needs']['thresholds']['low']
        people = [p for p in e.people.rows.values() if p.alive]
        here = Counter(p.cell for p in people)
        out = {}
        for p in people:
            if not e.people.at_least(p, day, a['min_stage']): continue
            dev = sum(1 for v in p.needs.values() if v < low) / max(1, len(p.needs))
            soc = min(1.0, (here[p.cell] - 1) / max(1, a['scene_max_people'] - 1))
            debt = min(1.0, (day - self.last.get(p.id, -1)) / a['debt_norm_days'])
            alarm = 1.0 if e.cells.visible(p.cell)['smoke'] else 0.0    # smoke or fire in or beside the cell: whoever stands there is worth asking
            out[p.id] = (w['deviation'] * dev + w['social'] * soc + w['role'] * (1.0 if p.role else 0.0) + w['debt'] * debt
                         + w['alarm'] * alarm)
        return out, len(people)

    def select(self):
        sc, alive = self.scores()
        return sorted(sorted(sc, key=lambda pid: (-sc[pid], pid))[:self.k(alive)])


def run_day(e, att, policy_for, chosen=None):
    """Advance the engine one day. chosen=None lets attention pick; replay passes the logged persons explicitly.
    policy_for(pid) builds the policy to bind. Returns (day, decision records made today)."""
    g = e.agency
    if chosen is None: chosen = att.select()
    chosen = set(chosen); keep = {pid for pid in g.agents if pid in g.plans}
    for pid in sorted(g.agents - keep - chosen): g.unbind(pid)
    for pid in sorted(chosen | keep):
        if pid not in g.policies: g.bind(pid, policy_for(pid))
    d, n0 = e.day, len(g.log)
    e.step()
    recs = g.log[n0:]; att.note(recs)
    return d, recs


def replay_days(log):
    """{day: [persons consulted that day]} from a decision log. Plan-step days (source 'plan:...') are not consultations:
    the engine runs those from the stored plan, so replay binds nobody for them."""
    out = {}
    for r in log:
        if not str(r['source']).startswith('plan:'): out.setdefault(r['day'], []).append(r['person'])
    return {d: sorted(v) for d, v in out.items()}

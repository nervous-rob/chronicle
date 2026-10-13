"""chronicle.ledger — the event ledger and the conservation flow ledger (ECOLOGY F3, ERRATA 2.1f).

Events are the only facts in the world (memory, hearsay, records, chroniclers all point at them).
Flows are the only way a conserved stock changes.  The ledger is the authority: it keeps the
expected holding of every (holder, resource) from flows since day 0; close_day() compares that
against the actual inventories and names the first process that broke conservation.

Holder keys: "p:<person>", "b:<building>", "c:<cell>" (ground / field), "src:<bucket>", "sink:<bucket>".
Allowed source/sink buckets depend on the item kind (ALLOWED).  Quantities are integers; food is in
food_unit_g quanta.  Wildlife is not a resource here (it is a float on cells, logged elsewhere).
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import Config, EVENT_TYPES, VISIBILITY

ALLOWED = {
    # kind:  (sources, sinks)
    "coin":      ({"mint", "setup"}, {"melt"}),
    "food":      ({"growth", "production", "setup"}, {"consume", "spoil", "burn", "decay", "fodder"}),
    "material":  ({"production", "setup"}, {"consume", "spoil", "burn", "decay", "build", "fodder"}),
    "tool":      ({"production", "setup"}, {"burn", "decay", "wear"}),
    "medium":    ({"production", "setup"}, {"burn", "decay"}),
    "livestock": ({"birth", "setup"}, {"death", "slaughter", "burn"}),
    "vehicle":   ({"production", "setup"}, {"burn", "decay"}),
}


class LedgerError(Exception):
    pass


@dataclass
class Event:
    id: int
    day: int
    type: str
    actors: tuple            # person ids; actors[0] is the subject
    cell: int
    place: int               # building id, or -1 for open air / none
    visibility: str
    payload: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.id, "day": self.day, "type": self.type, "actors": list(self.actors),
                "cell": self.cell, "place": self.place, "vis": self.visibility, **({"p": self.payload} if self.payload else {})}


class Ledger:
    def __init__(self, cfg: Config, events_path: str | None = None, flows_path: str | None = None):
        self.cfg = cfg
        self.kind_of = {iid: it["kind"] for iid, it in cfg.catalog["items"].items()}
        self.events: list[Event] = []
        self.by_day: dict[int, tuple[int, int]] = {}
        self._day_start = 0
        self._cur_day = -1
        # flows: columnar, today only; totals carried as expected holdings
        self._f_res: list[str] = []; self._f_from: list[str] = []; self._f_to: list[str] = []
        self._f_qty: list[int] = []; self._f_proc: list[str] = []; self._f_day: list[int] = []
        self.expected: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))   # holder -> resource -> qty
        self.bucket_totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))  # "src:x"/"sink:x" -> resource -> qty
        self._ev_out = open(events_path, "a") if events_path else None
        self._fl_out = open(flows_path, "a") if flows_path else None
        self.on_event = None  # optional synchronous event-time witness hook
        self.n_flows = 0
        self.checks = 0
        self.residuals: list[dict] = []

    # ------------------------------------------------------------------ events
    def event(self, day: int, type: str, actors: Iterable[int] = (), cell: int = -1, place: int = -1,
              visibility: str = "public", **payload: Any) -> int:
        if type not in EVENT_TYPES:
            raise LedgerError(f"unknown event type {type!r}")
        if visibility not in VISIBILITY:
            raise LedgerError(f"bad visibility {visibility!r}")
        if day != self._cur_day:
            self._roll_day(day)
        ev = Event(len(self.events), day, type, tuple(int(a) for a in actors), int(cell), int(place), visibility, payload)
        self.events.append(ev)
        if self._ev_out:
            self._ev_out.write(json.dumps(ev.to_json(), separators=(",", ":")) + "\n")
        if self.on_event is not None: self.on_event(ev)
        return ev.id

    def _roll_day(self, day: int) -> None:
        if self._cur_day >= 0:
            self.by_day[self._cur_day] = (self._day_start, len(self.events))
        self._cur_day = day
        self._day_start = len(self.events)

    def events_on(self, day: int) -> list[Event]:
        if day == self._cur_day:
            return self.events[self._day_start:]
        a, b = self.by_day.get(day, (0, 0))
        return self.events[a:b]

    # ------------------------------------------------------------------ flows
    def flow(self, day: int, resource: str, src: str, dst: str, qty: int, process: str) -> None:
        if not isinstance(qty, int) or isinstance(qty, bool):
            raise LedgerError(f"flow qty must be int, got {type(qty).__name__} ({process})")
        if qty <= 0:
            raise LedgerError(f"flow qty must be > 0 ({process}: {resource} {src}->{dst} {qty})")
        kind = self.kind_of.get(resource)
        if kind is None:
            raise LedgerError(f"unknown resource {resource!r} ({process})")
        sources, sinks = ALLOWED[kind]
        if src.startswith("src:") and src[4:] not in sources:
            raise LedgerError(f"{process}: {src!r} is not an allowed source for kind {kind}")
        if dst.startswith("sink:") and dst[5:] not in sinks:
            raise LedgerError(f"{process}: {dst!r} is not an allowed sink for kind {kind}")
        if src.startswith("sink:") or dst.startswith("src:"):
            raise LedgerError(f"{process}: flows go src -> holder -> sink, never the reverse")
        if not src.startswith("src:"):
            self.expected[src][resource] -= qty
        else:
            self.bucket_totals[src][resource] += qty
        if not dst.startswith("sink:"):
            self.expected[dst][resource] += qty
        else:
            self.bucket_totals[dst][resource] += qty
        self._f_res.append(resource); self._f_from.append(src); self._f_to.append(dst)
        self._f_qty.append(qty); self._f_proc.append(process); self._f_day.append(day)
        self.n_flows += 1

    def day_flows(self) -> list[tuple]:
        return list(zip(self._f_day, self._f_res, self._f_from, self._f_to, self._f_qty, self._f_proc))

    def close_day(self, day: int, holdings: dict[str, dict[str, int]], check: bool = True) -> list[dict]:
        """holdings: actual {holder -> {resource -> qty}} from the inventory system.
        Returns the residual list (empty when the books balance).  Raises in strict mode."""
        residuals = []
        if check:
            self.checks += 1
            holders = set(holdings) | {h for h in self.expected if self.expected[h]}
            for h in sorted(holders):
                exp = self.expected.get(h, {})
                act = holdings.get(h, {})
                for r in set(exp) | set(act):
                    e, a = int(exp.get(r, 0)), int(act.get(r, 0))
                    if e != a:
                        procs = sorted({p for (d, rr, f, t, q, p) in self.day_flows() if rr == r and h in (f, t)})
                        residuals.append({"day": day, "holder": h, "resource": r, "expected": e, "actual": a,
                                          "today_processes": procs})
            if any(a < 0 for hh in holdings.values() for a in hh.values()):
                residuals.append({"day": day, "holder": "*", "resource": "*", "expected": 0, "actual": -1,
                                  "today_processes": ["negative holding"]})
            if residuals:
                self.residuals.extend(residuals)
                if self.cfg["validator"]["strict"]:
                    r0 = residuals[0]
                    raise LedgerError(f"conservation broken on day {day}: {r0['holder']} {r0['resource']} "
                                      f"expected {r0['expected']} actual {r0['actual']} (today: {r0['today_processes']}); "
                                      f"{len(residuals)} residual(s)")
        if self._fl_out:
            for row in self.day_flows():
                self._fl_out.write(json.dumps(row, separators=(",", ":")) + "\n")
        self._f_res.clear(); self._f_from.clear(); self._f_to.clear()
        self._f_qty.clear(); self._f_proc.clear(); self._f_day.clear()
        return residuals

    def totals(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for h, rr in self.expected.items():
            for r, q in rr.items():
                out[r] += q
        return dict(out)

    def flush(self) -> None:
        for f in (self._ev_out, self._fl_out):
            if f: f.flush()

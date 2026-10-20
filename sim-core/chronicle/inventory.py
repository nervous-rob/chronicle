"""Physical inventory, not a market or an action resolver.

Holder = physical custody (p:, b:, c:); owner = person/household/title key.
Location and ownership are deliberately independent. Items on c: are actual
lots lying on the ground, NEVER an abstract cell food supply.
Transfer preflight is atomic across all legs. Caller resolves consent and
co-presence; transfer is a trusted engine primitive, not an LLM-facing API.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict
import copy
import math
from .ledger import ALLOWED, LedgerError


class InventoryError(ValueError):
    pass


def quantity(value):
    if type(value) is not int or value <= 0:
        raise InventoryError('quantity must be a positive Python integer')
    return value


@dataclass
class Holder:
    key: str
    capacity_kg: float | None = None
    store: bool = False
    spoil_mult: float = 1.0


@dataclass
class Lot:
    id: int
    item: str
    qty: int
    holder: str
    owner: str | None
    # Medium content and provenance survive movement; never merge media.
    entries: list = field(default_factory=list)
    sealed_by: int | None = None
    opened_by: int | None = None
    copied_from: int | None = None
    # Livestock are counts, not animal agents. Health is per disease.
    health: dict = field(default_factory=dict)  # disease -> S/E/I/R integer counts
    ground_place: int = -1  # exact field/place for ground lots; -1 is unresolved
    origin_event: int | None = None  # harvest ledger pointer; retained through splits
    # A finished work order may sit at an establishment awaiting its customer's pickup.
    # It remains customer-owned but cannot be consumed or traded out from under them.
    order_id: int | None = None



@dataclass(frozen=True)
class Leg:
    lot: int
    qty: int
    to_holder: str
    to_owner: str | None   # pass the current owner for custody-only delivery


class Inventory:
    def __init__(self, cfg, ledger):
        self.cfg, self.ledger = cfg, ledger
        self.holders: dict[str, Holder] = {}
        self.lots: dict[int, Lot] = {}
        self.next_id = 0
        self._decay_day = -1

    def register(self, key, capacity_kg=None, store=False, spoil_mult=1.0):
        if key in self.holders:
            raise InventoryError(f'duplicate holder {key}')
        if not isinstance(key, str) or key.partition(':')[0] not in ('p', 'b', 'c'):
            raise InventoryError('holder must start p:, b:, or c:')
        if capacity_kg is not None and (not math.isfinite(capacity_kg) or capacity_kg < 0):
            raise InventoryError('invalid capacity')
        if not math.isfinite(spoil_mult) or spoil_mult < 0:
            raise InventoryError('invalid spoilage multiplier')
        self.holders[key] = Holder(key, capacity_kg, bool(store), spoil_mult)

    def _holder(self, key):
        if key not in self.holders:
            raise InventoryError(f'unregistered holder {key}')
        return self.holders[key]

    def holdings(self):
        out = defaultdict(lambda: defaultdict(int))
        for lot in self.lots.values():
            out[lot.holder][lot.item] += lot.qty
        return {h: dict(v) for h, v in out.items()}

    def weight(self, holder, lots=None):
        lots = self.lots if lots is None else lots
        # Herds and vehicles are led/driven, not carried on a person's back.
        return sum(l.qty * self.cfg.item(l.item)['weight'] for l in lots.values()
                   if l.holder == holder and self.cfg.item(l.item)['kind'] not in ('livestock', 'vehicle'))

    def _check_capacity(self, lots, holders):
        for key in holders:
            spec = self._holder(key)
            if spec.capacity_kg is None:
                continue
            extra = sum(l.qty * self.cfg.item(l.item).get('carry_cap_add', 0)
                        for l in lots.values() if l.holder == key) if key.startswith('p:') else 0
            if self.weight(key, lots) > spec.capacity_kg + extra + 1e-8:
                raise InventoryError(f'capacity exceeded at {key}')

    def _make_lot(self, lid, item, qty, holder, owner):
        it = self.cfg.item(item)
        health = {}
        if it['kind'] == 'livestock':
            health = {d: {'S': qty, 'E': 0, 'I': 0, 'R': 0}
                      for d, v in self.cfg.catalog['disease'].items() if item in v['hosts']}
        return Lot(lid, item, qty, holder, owner, health=health)

    def create(self, day, item, qty, holder, owner, source='production', process='production'):
        quantity(qty); self._holder(holder)
        it = self.cfg.item(item)
        if source not in ALLOWED[it['kind']][0]:
            raise InventoryError(f'{source} cannot create {item}')
        candidate = self.lots.copy()
        ids = []
        # Every physical writing surface has its own identity, blank or written.
        for amount in ([1] * qty if it['kind'] == 'medium' else [qty]):
            lid = self.next_id + len(ids)
            candidate[lid] = self._make_lot(lid, item, amount, holder, owner)
            ids.append(lid)
        self._check_capacity(candidate, [holder])
        self.ledger.flow(day, item, f'src:{source}', holder, qty, process)
        self.lots = candidate; self.next_id += len(ids)
        return ids

    def receive_harvest(self, day, cells, cell_ids, *, owner=None, place=-1, actors=()):
        """Bridge Cells.harvest's existing growth flow exactly once, without a
        second create() flow. Entire harvest is preflighted before crop mutation."""
        import numpy as np
        ids = sorted({int(i) for i in cell_ids})
        item = cells.crop['item']; candidate = self.lots.copy(); next_id = self.next_id
        for i in ids:
            if not 0 <= i < cells.world.n:
                raise InventoryError('invalid harvest cell')
            h = f'c:{i}'
            if h not in self.holders: self.register(h)
            if not cells.planted[i]: continue
            q = int(round(cells.units_per_field * cells.fields[i] * min(1., cells.crop_progress[i]) * cells.crop_health[i]))
            if q > 0:
                candidate[next_id] = self._make_lot(next_id, item, q, h, owner)
                candidate[next_id].ground_place = place
                next_id += 1
        self._check_capacity(candidate, [f'c:{i}' for i in ids])
        result = cells.harvest(day, np.asarray(ids, dtype=int), self.ledger, actors=actors, place=place)
        for event in self.ledger.events_on(day):
            if event.type == 'harvest' and event.cell in result:
                for lid in range(self.next_id, next_id):
                    if candidate[lid].holder == f'c:{event.cell}':
                        candidate[lid].origin_event = event.id
        self.lots, self.next_id = candidate, next_id
        return result

    @staticmethod
    def _split_health(lot, qty):
        """Deterministic proportional split; marginal counts remain exact.
        This is not a joint multi-disease co-infection representation."""
        out = {}
        for disease, counts in lot.health.items():
            keys = ('S', 'E', 'I', 'R')
            exact = {k: counts[k] * qty / lot.qty for k in keys}
            taken = {k: math.floor(exact[k]) for k in keys}
            left = qty - sum(taken.values())
            for k in sorted(keys, key=lambda k: (-(exact[k] - taken[k]), keys.index(k)))[:left]:
                taken[k] += 1
            out[disease] = taken
        return out

    def transfer(self, day, legs, actors=(), cell=-1, place=-1,
                 visibility='public', reason='give'):
        from .config import VISIBILITY
        legs = list(legs); actors = tuple(int(p) for p in actors)
        cell, place = int(cell), int(place)  # preflight coordinates
        if not legs or visibility not in VISIBILITY:
            raise InventoryError('empty transfer or invalid visibility')
        # Preflight every outgoing obligation against INITIAL stock; no credit
        # from goods that would be received later in this same transaction.
        used = defaultdict(int)
        for leg in legs:
            quantity(leg.qty); self._holder(leg.to_holder)
            if leg.lot not in self.lots: raise InventoryError('unknown lot')
            used[leg.lot] += leg.qty
            if used[leg.lot] > self.lots[leg.lot].qty:
                raise InventoryError('insufficient stock')
        candidate = self.lots.copy()
        for lid in used: candidate[lid] = copy.deepcopy(candidate[lid])
        next_id = self.next_id; touched = set(); moves = []; received = []
        for leg in legs:
            lot = candidate[leg.lot]; src = lot.holder
            touched.update((src, leg.to_holder))
            if leg.qty == lot.qty:
                moved = lot
            else:
                moved = copy.deepcopy(lot); moved.id = next_id; next_id += 1
                moved.qty = leg.qty; moved.health = self._split_health(lot, leg.qty)
                for d, counts in moved.health.items():
                    for k, q in counts.items(): lot.health[d][k] -= q
                lot.qty -= leg.qty; candidate[moved.id] = moved
            moved.holder, moved.owner = leg.to_holder, leg.to_owner
            moves.append((lot.item, src, leg.to_holder, leg.qty))
            received.append(moved.id)
        self._check_capacity(candidate, touched)
        # No fallible resolver work below this line. The ledger types and flow
        # shape were preflighted; physical transfers never touch source/sink buckets.
        for item, src, dst, qty in moves:
            if src != dst: self.ledger.flow(day, item, src, dst, qty, 'transfer')
        self.lots, self.next_id = candidate, next_id
        eid = self.ledger.event(day, 'transfer', actors, cell=cell, place=place,
                                visibility=visibility, reason=reason,
                                legs=[{'lot': l.lot, 'qty': l.qty, 'to': l.to_holder, 'owner': l.to_owner} for l in legs])
        return eid, received

    def destroy(self, day, lid, qty, sink, process):
        quantity(qty)
        lot = self.lots[lid]
        if qty > lot.qty or sink not in ALLOWED[self.cfg.item(lot.item)['kind']][1]:
            raise InventoryError('invalid destruction')
        removed = self._split_health(lot, qty)
        self.ledger.flow(day, lot.item, lot.holder, f'sink:{sink}', qty, process)
        if qty == lot.qty:
            del self.lots[lid]
        else:
            for d, counts in removed.items():
                for k, q in counts.items(): lot.health[d][k] -= q
            lot.qty -= qty

    def eat(self, day, holders, owners, demand):
        quantity(demand)
        owners = set(owners); remaining = demand
        # Only personally/household-owned food in explicitly reachable holders.
        # No remote household teleportation, market freebies, or ground scavenging.
        for holder in dict.fromkeys(holders):
            food = [l for l in self.lots.values() if l.holder == holder and l.owner in owners
                    and l.order_id is None and self.cfg.item(l.item)['kind'] == 'food']
            for lot in sorted(food, key=lambda l: (-self.cfg.item(l.item)['spoil_per_day'], l.id)):
                q = min(remaining, lot.qty)
                self.destroy(day, lot.id, q, 'consume', 'consume')
                remaining -= q
                if not remaining: return demand
        return demand - remaining

    def decay(self, day, streams):
        if day <= self._decay_day: raise InventoryError('decay already processed or out of order')
        for lid in sorted(list(self.lots)):
            lot = self.lots[lid]; it = self.cfg.item(lot.item); h = self.holders[lot.holder]
            p = it.get('decay_p_per_day', 0) if it['kind'] == 'medium' else it.get('spoil_per_day', 0) * h.spoil_mult
            if it['kind'] == 'medium' and h.store: p = 0
            if p <= 0: continue
            q = int(streams.one('item-decay', day, lid).binomial(lot.qty, min(1., p)))
            if q: self.destroy(day, lid, q, 'decay' if it['kind'] == 'medium' else 'spoil', 'decay')
        self._decay_day = day

    def burn(self, day, holder):
        destroyed = []
        for lid in sorted(list(self.lots)):
            lot = self.lots[lid]; it = self.cfg.item(lot.item)
            if lot.holder != holder or it.get('fireproof', False) or 'burn' not in ALLOWED[it['kind']][1]:
                continue
            self.destroy(day, lid, lot.qty, 'burn', 'fire'); destroyed.append(lid)
        return destroyed

    def value_owned(self, owner, prices=None):
        prices = prices or {}
        return sum(l.qty * prices.get(l.item, self.cfg.item(l.item)['price'])
                   for l in self.lots.values() if l.owner == owner)

    def medium(self, lid):
        lot = self.lots[lid]
        if self.cfg.item(lot.item)['kind'] != 'medium': raise InventoryError('not a medium')
        return lot

    def contents(self, lid):
        lot = self.medium(lid)
        if lot.sealed_by is not None and lot.opened_by is None:
            raise InventoryError('sealed record')
        return copy.deepcopy(lot.entries)

    def append_entries(self, lid, entries, overwrite=False):
        lot = self.medium(lid); self.contents(lid)
        spec = self.cfg.item(lot.item); entries = copy.deepcopy(list(entries))
        if overwrite and not spec['reusable']: raise InventoryError('medium is not reusable')
        for entry in entries:
            if entry.get('type') not in ('cite', 'note', 'snapshot'): raise InventoryError('unknown entry type')
            if entry['type'] == 'note' and (not isinstance(entry.get('text'), str) or len(entry['text']) > self.cfg['records']['max_chars']):
                raise InventoryError('invalid note')
            if entry['type'] == 'cite':
                eid = entry.get('event_id'); fidelity = entry.get('fidelity')
                if type(eid) is not int or not 0 <= eid < len(self.ledger.events): raise InventoryError('invalid citation')
                if not isinstance(fidelity, (int, float)) or not math.isfinite(fidelity) or not 0 <= fidelity <= 1:
                    raise InventoryError('invalid citation fidelity')
        new = ([] if overwrite else lot.entries) + entries
        if len(new) > spec['capacity']: raise InventoryError('medium full')
        lot.entries = new

    def compact_food(self):
        """Coalesce fungible food with identical provenance; media retain identity.
        Engine-only day boundary: current contracts do not reference food lot ids.
        Future reservations must pin lots or refer to provenance/quantity, not ids.
        """
        groups = {}
        for lid in sorted(list(self.lots)):
            lot = self.lots[lid]
            if self.cfg.item(lot.item)['kind'] != 'food' or lot.entries or lot.health: continue
            key = (lot.item, lot.holder, lot.owner, lot.ground_place, lot.origin_event)
            if key in groups:
                self.lots[groups[key]].qty += lot.qty
                del self.lots[lid]
            else: groups[key] = lid

    def validate(self):
        for lid, lot in self.lots.items():
            quantity(lot.qty); self._holder(lot.holder)
            if lid != lot.id: raise InventoryError('lot id mismatch')
            if self.cfg.item(lot.item)['kind'] == 'medium' and lot.qty != 1:
                raise InventoryError('media must retain unique identity')
            for counts in lot.health.values():
                if set(counts) != {'S', 'E', 'I', 'R'} or any(type(q) is not int or q < 0 for q in counts.values()) or sum(counts.values()) != lot.qty:
                    raise InventoryError('herd health counts do not balance')
        self._check_capacity(self.lots, self.holders)
        return True

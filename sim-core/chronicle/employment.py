"""Local paid-work offers: employer-funded shifts at physical workplaces."""
from dataclasses import dataclass
from .inventory import Leg

@dataclass
class Job:
    id: int
    place: int
    employer: int
    wage: int
    slots: int
    filled_day: int = -1
    filled: int = 0

class Employment:
    def __init__(self, engine):
        self.e = engine
        self.jobs = {}
        self.next_id = 0

    def post(self, place, employer, wage, slots=1):
        """Setup/trusted business primitive. Wages must already be held at its workplace."""
        e = self.e
        if type(place) is not int or place not in e.places: raise ValueError('unknown workplace')
        if type(employer) is not int or employer not in e.people.rows: raise ValueError('unknown employer')
        if type(wage) is not int or wage < 1 or type(slots) is not int or slots < 1: raise ValueError('positive wage and slots required')
        p, b = e.people.rows[employer], e.places[place]
        if b.owner not in e.people.owners(p): raise ValueError('employer lacks workplace ownership')
        if 'workplace' not in e.cfg.building(b.kind)['affordances']: raise ValueError('place is not a workplace')
        jid = self.next_id; self.next_id += 1
        self.jobs[jid] = Job(jid, place, employer, wage, slots)
        return self.jobs[jid]

    def offers(self, person):
        # A posting is public at a personally known location. It exposes wage and destination,
        # never the employer's remote cash, inventory, or another worker's condition.
        return [{'id': j.id, 'place': j.place, 'cell': self.e.places[j.place].cell, 'wage': j.wage,
                 'slots': j.slots, 'employer': j.employer}
                for j in sorted(self.jobs.values())
                if self.e.places[j.place].cell in person.known_cells and self.e.places[j.place].destroyed_day is None]

    def available_here(self, person):
        return [j for j in self.jobs.values() if j.place == person.place and self.e.places[j.place].destroyed_day is None]

    def work(self, worker, jid):
        e = self.e
        if type(jid) is not int or jid not in self.jobs: raise ValueError('unknown_job')
        j = self.jobs[jid]
        if worker.place != j.place: raise ValueError('not_at_workplace')
        if j.filled_day != e.day: j.filled_day, j.filled = e.day, 0
        if j.filled >= j.slots: raise ValueError('job_full')
        employer = e.people.rows[j.employer]
        # The till is physically at the business. No coin creation and no remote payroll.
        coins = [lot for lot in e.inventory.lots.values() if lot.holder == f'b:{j.place}' and lot.owner in e.people.owners(employer) and lot.item == 'coin']
        if sum(lot.qty for lot in coins) < j.wage: raise ValueError('employer_insolvent')
        left, legs = j.wage, []
        for lot in sorted(coins, key=lambda x:x.id):
            take = min(left, lot.qty)
            if take: legs.append(Leg(lot.id, take, f'p:{worker.id}', f'h:{worker.household_id}' if worker.household_id is not None else f'p:{worker.id}')); left -= take
            if not left: break
        e.inventory.transfer(e.day, legs, actors=(j.employer, worker.id), cell=worker.cell, place=j.place, reason='wage')
        e.ledger.event(e.day, 'paid', [j.employer, worker.id], cell=worker.cell, place=j.place, job=j.id, wage=j.wage)
        j.filled += 1

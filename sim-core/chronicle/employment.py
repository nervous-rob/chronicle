"""Local, employer-funded paid shifts at physical workplaces."""
from dataclasses import dataclass
from .inventory import Leg

@dataclass
class Staffing:
    """A durable primary operating assignment at a physical establishment.

    It is not a permanent caste or a promise of a wage: it names the person currently
    responsible for operating a particular workplace.  The relationship can be vacated
    by death/ownership change and reconciled into a visible vacancy.
    """
    worker: int
    place: int
    role: str
    employer: int | None
    since_day: int = 0

@dataclass
class Job:
    id: int
    place: int
    employer: int
    wage: int
    slots: int
    activity: str | None = None
    project: int | None = None
    # A casual shift has no order.  An order-linked shift closes with its order.
    order: int | None = None
    filled_day: int = -1
    filled: int = 0

@dataclass
class WorkOrder:
    """A paid customer commission routed through one physical establishment.

    v1 intentionally uses a building project as the deliverable.  The customer prepays
    from coin they physically carry at the establishment; the establishment then uses
    its till to fund ordinary project shifts.  Completion transfers the created place
    to the named customer household.  Nothing here creates coin, materials or labor.
    """
    id: int
    place: int
    customer: int
    provider: int
    project: int | None
    price: int
    due_day: int
    status: str = 'open'       # open | complete
    created_day: int = 0
    completed_day: int | None = None
    delivered_place: int | None = None
    # Generic recipe order fields.  Project orders retain the narrow building path.
    recipe: str | None = None
    output: str | None = None
    output_qty: int = 0
    labor_hours: float = 0.0
    required_hours: float = 0.0
    output_lots: tuple[int, ...] = ()

class Employment:
    def __init__(self, engine):
        self.e = engine
        self.jobs = {}
        self.next_id = 0
        self.work_orders = {}
        self.next_order_id = 0
        # worker id -> Staffing.  A person has one primary establishment assignment;
        # casual paid shifts remain ordinary Job records.
        self.staffing = {}
        # (place id, role id) -> True for establishment roles that lost their operator.
        # A workshop may legitimately host several distinct roles, so vacancies are
        # role slots rather than a lossy one-per-building flag.
        self.vacancies = {}

    def _employer_for(self, place):
        """Return the current legal owner who can employ for this establishment."""
        owner = self.e.places[place].owner
        if owner is None: return None
        candidates = [p for p in self.e.people.rows.values() if p.alive and owner in self.e.people.owners(p)]
        return min(candidates, key=lambda p: (not p.estate_owner, p.id)).id if candidates else None

    def assign_staff(self, worker, place, role=None, *, setup=False):
        """Assign an eligible resident as the establishment's primary operator.

        Setup uses this to prevent the day-zero all-unemployed deadlock.  It transfers
        neither property nor money; operational work and paid contracts remain separate.
        """
        e=self.e
        if type(worker) is not int or worker not in e.people.rows: raise ValueError('unknown worker')
        if type(place) is not int or place not in e.places: raise ValueError('unknown workplace')
        p=e.people.rows[worker]; b=e.places[place]
        role = p.role if role is None else role
        if role not in e.cfg.catalog['roles']: raise ValueError('unknown role')
        if not p.alive or not e.people.can_use_role(p, role, e.day): raise ValueError('ineligible worker')
        if p.role != role or e.cfg.role(role)['workplace'] != b.kind: raise ValueError('role does not operate workplace')
        if 'workplace' not in e.cfg.building(b.kind)['affordances']: raise ValueError('place is not a workplace')
        # A starting operator must have a lawful tie to the establishment.  Later
        # cross-household hiring will be expressed by a contract, not setup magic.
        if b.owner is not None and b.owner not in e.people.owners(p): raise ValueError('worker lacks establishment access')
        self.staffing[worker] = Staffing(worker, place, role, self._employer_for(place), e.day)
        self.vacancies.pop((place, role), None)
        return self.staffing[worker]

    def reconcile_staffing(self, day):
        """Remove invalid operators and expose local replacement needs after lifecycle changes."""
        e=self.e
        for pid, row in list(self.staffing.items()):
            p=e.people.rows.get(pid); b=e.places.get(row.place)
            valid = (p is not None and p.alive and b is not None and b.destroyed_day is None
                     and p.role == row.role and e.people.can_use_role(p, row.role, day)
                     and e.cfg.role(row.role)['workplace'] == b.kind
                     and (b.owner is None or b.owner in e.people.owners(p)))
            if not valid:
                self.staffing.pop(pid, None)
                if b is not None and b.destroyed_day is None and b.owner is not None:
                    self.vacancies[(row.place, row.role)] = True
        # A successor already qualified within the establishment-owning household may
        # take over deterministically; otherwise the vacancy remains visible for a
        # later hiring/contract resolver.
        occupied = {(row.place, row.role) for row in self.staffing.values()}
        for (place, role), _ in sorted(list(self.vacancies.items())):
            if (place, role) in occupied: self.vacancies.pop((place, role), None); continue
            b=e.places.get(place)
            if b is None or b.destroyed_day is not None or b.owner is None:
                self.vacancies.pop((place, role), None); continue
            candidates=[p for p in e.people.rows.values() if p.alive and p.role == role
                        and e.people.can_use_role(p, role, day) and b.owner in e.people.owners(p)]
            if candidates: self.assign_staff(min(candidates,key=lambda p:p.id).id, place, role)
        # Ownership can change without a vacancy (inheritance); refresh the employer
        # pointer without moving either people or resources.
        for row in self.staffing.values(): row.employer=self._employer_for(row.place)

    def _owner_key(self, person):
        return f'h:{person.household_id}' if person.household_id is not None else f'p:{person.id}'

    def commission_project(self, customer, provider, place, building, price, due_day):
        """Create a prepaid building commission at a provider's establishment.

        This is the queue's first vertical slice.  It is deliberately strict: both
        parties must be physically present at the establishment; the client pays with
        carried coin; the provider supplies/reserves real materials; and the finished
        building is owned by the customer, not silently retained by the workshop.
        """
        e=self.e
        if any(type(x) is not int for x in (customer, provider, place, price, due_day)):
            raise ValueError('invalid_work_order')
        if customer not in e.people.rows or provider not in e.people.rows or place not in e.places:
            raise ValueError('unknown_work_order_party_or_place')
        if customer == provider or price < 1 or due_day < e.day:
            raise ValueError('invalid_work_order_terms')
        if building not in e.cfg.catalog['buildings']:
            raise ValueError('unknown_order_building')
        client=e.people.rows[customer]; proprietor=e.people.rows[provider]; b=e.places[place]
        if not client.alive or not proprietor.alive or client.place != place or proprietor.place != place:
            raise ValueError('parties_must_be_at_establishment')
        if b.owner not in e.people.owners(proprietor) or 'workplace' not in e.cfg.building(b.kind)['affordances']:
            raise ValueError('provider_lacks_establishment')
        # A project can only reserve material held at its exact work place.  This keeps
        # the commission physical and makes a workshop's stock meaningful.
        coin=[lot for lot in e.inventory.lots.values() if lot.holder==f'p:{customer}' and lot.item=='coin' and lot.owner in e.people.owners(client)]
        if sum(lot.qty for lot in coin) < price: raise ValueError('customer_insolvent')
        legs=[]; left=price; provider_owner=self._owner_key(proprietor)
        for lot in sorted(coin,key=lambda x:x.id):
            take=min(left,lot.qty)
            if take: legs.append(Leg(lot.id,take,f'b:{place}',provider_owner)); left-=take
            if not left: break
        e.inventory.transfer(e.day,legs,actors=(customer,provider),cell=client.cell,place=place,reason='work-order-payment')
        # _new is the one project-authority path: it reserves provider material and
        # keeps the project bound to the actual work site.
        q=e.projects._new(proprietor, {'project':'build','building':building})
        q.recipient_owner=self._owner_key(client)
        oid=self.next_order_id; self.next_order_id+=1
        order=WorkOrder(oid,place,customer,provider,q.id,price,due_day,created_day=e.day)
        self.work_orders[oid]=order
        e.ledger.event(e.day,'contract',[customer,provider],cell=client.cell,place=place,order=oid,project=q.id,
                       price=price,due_day=due_day,building=building)
        return order

    def commission_recipe(self, customer, provider, place, recipe, price, due_day):
        """Queue a prepaid, material-conserving recipe order for establishment pickup."""
        e=self.e
        if any(type(x) is not int for x in (customer, provider, place, price, due_day)) or not isinstance(recipe,str): raise ValueError('invalid_work_order')
        if recipe not in e.cfg.catalog.get('recipes',{}): raise ValueError('unknown_recipe')
        if customer not in e.people.rows or provider not in e.people.rows or place not in e.places: raise ValueError('unknown_work_order_party_or_place')
        if customer == provider or price < 1 or due_day < e.day: raise ValueError('invalid_work_order_terms')
        client=e.people.rows[customer]; proprietor=e.people.rows[provider]; b=e.places[place]; spec=e.cfg.catalog['recipes'][recipe]
        if not client.alive or not proprietor.alive or client.place != place or proprietor.place != place: raise ValueError('parties_must_be_at_establishment')
        if b.owner not in e.people.owners(proprietor) or b.kind != spec['workplace'] or 'workplace' not in e.cfg.building(b.kind)['affordances']: raise ValueError('provider_lacks_recipe_establishment')
        coin=[lot for lot in e.inventory.lots.values() if lot.holder==f'p:{customer}' and lot.item=='coin' and lot.owner in e.people.owners(client)]
        if sum(lot.qty for lot in coin) < price: raise ValueError('customer_insolvent')
        provider_owner=self._owner_key(proprietor)
        material=[lot for lot in e.inventory.lots.values() if lot.holder==f'b:{place}' and lot.owner==provider_owner and lot.item in spec['inputs']]
        available={item:sum(l.qty for l in material if l.item==item) for item in spec['inputs']}
        if any(available[item] < qty for item,qty in spec['inputs'].items()): raise ValueError('provider_missing_recipe_inputs')
        legs=[]; left=price
        for lot in sorted(coin,key=lambda x:x.id):
            take=min(left,lot.qty)
            if take: legs.append(Leg(lot.id,take,f'b:{place}',provider_owner)); left-=take
            if not left: break
        e.inventory.transfer(e.day,legs,actors=(customer,provider),cell=client.cell,place=place,reason='work-order-payment')
        for item,need in sorted(spec['inputs'].items()):
            left=need
            for lot in sorted([x for x in list(e.inventory.lots.values()) if x.holder==f'b:{place}' and x.owner==provider_owner and x.item==item],key=lambda x:x.id):
                take=min(left,lot.qty)
                if take: e.inventory.destroy(e.day,lot.id,take,'consume','work-order-reserve'); left-=take
                if not left: break
        oid=self.next_order_id; self.next_order_id+=1
        order=WorkOrder(oid,place,customer,provider,None,price,due_day,created_day=e.day,recipe=recipe,output=spec['output'],output_qty=spec['output_qty'],required_hours=float(spec['labor_hours']))
        self.work_orders[oid]=order
        e.ledger.event(e.day,'contract',[customer,provider],cell=client.cell,place=place,order=oid,recipe=recipe,price=price,due_day=due_day,output=spec['output'],output_qty=spec['output_qty'])
        return order

    def _recipe_complete(self, q):
        e=self.e
        ids=tuple(e.inventory.create(e.day,q.output,q.output_qty,f'b:{q.place}',self._owner_key(e.people.rows[q.customer]),source='production',process='work-order-recipe'))
        # Customer-owned output is reserved for pickup.  It is physical stock at the
        # shop, not a free meal that the customer (or shop) can consume before the
        # handoff action resolves.
        for lid in ids: e.inventory.lots[lid].order_id=q.id
        q.status='ready'; q.completed_day=e.day; q.output_lots=ids
        for jid, job in list(self.jobs.items()):
            if job.order == q.id: del self.jobs[jid]
        e.ledger.event(e.day,'contract',[q.customer,q.provider],cell=e.places[q.place].cell,place=q.place,order=q.id,recipe=q.recipe,status='ready',output=q.output,output_qty=q.output_qty)

    def collect_order(self, customer, order):
        if type(customer) is not int or type(order) is not int or order not in self.work_orders: raise ValueError('unknown_work_order')
        q=self.work_orders[order]; e=self.e
        if q.customer != customer or q.recipe is None or q.status != 'ready': raise ValueError('order_not_ready_for_customer')
        client=e.people.rows[customer]
        if not client.alive or client.place != q.place: raise ValueError('customer_not_at_establishment')
        lots=[e.inventory.lots[lid] for lid in q.output_lots if lid in e.inventory.lots]
        if len(lots)!=len(q.output_lots) or sum(l.qty for l in lots)!=q.output_qty or any(l.holder!=f'b:{q.place}' or l.owner!=self._owner_key(client) or l.item!=q.output for l in lots): raise ValueError('order_output_missing')
        e.inventory.transfer(e.day,[Leg(l.id,l.qty,f'p:{customer}',l.owner) for l in lots],actors=(customer,q.provider),cell=client.cell,place=q.place,reason='work-order-pickup')
        # transfer may split/retain lot identities; every full-output lot remains
        # present here, so clear its pickup reservation only after custody changes.
        for lid in q.output_lots:
            if lid in e.inventory.lots: e.inventory.lots[lid].order_id=None
        q.status='complete'; q.delivered_place=q.place
        e.ledger.event(e.day,'contract_done',[customer,q.provider],cell=client.cell,place=q.place,order=q.id,output=q.output,output_qty=q.output_qty)
        return q

    def post_order_shift(self, order, wage, slots=1):
        if type(order) is not int or order not in self.work_orders: raise ValueError('unknown_work_order')
        q=self.work_orders[order]
        if q.status != 'open' or self.e.day > q.due_day: raise ValueError('work_order_closed')
        # `post` is deliberately guarded against arbitrary order shifts; bind the
        # internal order identity before exposing this trusted queue-generated job.
        j=self.post(q.place,q.provider,wage,slots,activity=('project' if q.project is not None else None),project=q.project)
        j.order=order
        if q.project is None: j.activity='order'
        return j

    def order_for_project(self, project):
        return next((q for q in self.work_orders.values() if q.project==project and q.status=='open'),None)

    def complete_project_order(self, project, delivered_place):
        q=self.order_for_project(project)
        if q is None: return
        q.status='complete';q.completed_day=self.e.day;q.delivered_place=delivered_place
        # Closed project shifts cannot remain attached to a vanished active project:
        # retaining them would advertise a stale contract and make a completed order
        # impossible to checkpoint.
        for jid, job in list(self.jobs.items()):
            if job.order == q.id: del self.jobs[jid]
        self.e.ledger.event(self.e.day,'contract_done',[q.customer,q.provider],cell=self.e.places[q.place].cell,place=q.place,
                            order=q.id,project=project,delivered_place=delivered_place)

    def post(self, place, employer, wage, slots=1, activity=None, project=None):
        """Setup/trusted business primitive. Wages must already be held at the workplace.

        A field posting is an owned farm's seasonal planting or harvest shift. It adds
        real crop labor; it neither makes grain nor bypasses normal crop state.
        """
        e = self.e
        if type(place) is not int or place not in e.places: raise ValueError('unknown workplace')
        if type(employer) is not int or employer not in e.people.rows: raise ValueError('unknown employer')
        if type(wage) is not int or wage < 1 or type(slots) is not int or slots < 1: raise ValueError('positive wage and slots required')
        p, b = e.people.rows[employer], e.places[place]
        if b.owner not in e.people.owners(p): raise ValueError('employer lacks workplace ownership')
        if 'workplace' not in e.cfg.building(b.kind)['affordances']: raise ValueError('place is not a workplace')
        if activity is not None:
            if activity not in ('plant', 'harvest', 'project', 'order'): raise ValueError('unknown job activity')
            if activity in ('plant', 'harvest') and (place not in e.farming.farms or b.kind != 'farm'):
                raise ValueError('field shift requires a registered farm and plant or harvest activity')
            if activity == 'order':
                raise ValueError('order shifts must be created through post_order_shift')
            if activity == 'project':
                if type(project) is not int or project not in e.projects.projects: raise ValueError('project shift requires an active project')
                q=e.projects.projects[project]
                if q.place != place or q.owner not in e.people.owners(p): raise ValueError('employer does not control project workplace')
        elif project is not None: raise ValueError('project requires project activity')
        jid = self.next_id; self.next_id += 1
        self.jobs[jid] = Job(jid, place, employer, wage, slots, activity, project)
        return self.jobs[jid]

    def _open(self, j):
        if self.e.places[j.place].destroyed_day is not None: return False
        if j.order is not None:
            q=self.work_orders.get(j.order)
            if q is None or q.status != 'open' or q.project != j.project or self.e.day > q.due_day: return False
        if j.activity in ('plant','harvest') and not self.e.farming.hired_field_open(j.place, j.activity): return False
        if j.activity == 'project' and (j.project not in self.e.projects.projects or self.e.projects.projects[j.project].place != j.place): return False
        if j.activity == 'order' and (j.order is None or self.work_orders[j.order].recipe is None): return False
        # A public offer must be fundable from the physical workplace till now.
        employer = self.e.people.rows[j.employer]
        till = sum(lot.qty for lot in self.e.inventory.lots.values()
                   if lot.holder == f'b:{j.place}' and lot.item == 'coin'
                   and lot.owner in self.e.people.owners(employer))
        return till >= j.wage

    def offers(self, person):
        return [{'id': j.id, 'place': j.place, 'cell': self.e.places[j.place].cell, 'wage': j.wage,
                 'slots': j.slots, 'activity': j.activity, 'project': j.project, 'order': j.order, 'employer': j.employer}
                for j in sorted(self.jobs.values(), key=lambda j:j.id)
                if self.e.places[j.place].cell in person.known_cells and self._open(j)]

    def available_here(self, person):
        return [j for j in self.jobs.values() if j.place == person.place and self._open(j)]

    def work(self, worker, jid):
        e = self.e
        if type(jid) is not int or jid not in self.jobs: raise ValueError('unknown_job')
        j = self.jobs[jid]
        if worker.place != j.place: raise ValueError('not_at_workplace')
        if not self._open(j): raise ValueError('job_closed')
        if j.filled_day != e.day: j.filled_day, j.filled = e.day, 0
        if j.filled >= j.slots: raise ValueError('job_full')
        employer = e.people.rows[j.employer]
        coins = [lot for lot in e.inventory.lots.values() if lot.holder == f'b:{j.place}' and lot.owner in e.people.owners(employer) and lot.item == 'coin']
        if sum(lot.qty for lot in coins) < j.wage: raise ValueError('employer_insolvent')
        worked = 0.0
        if j.activity in ('plant','harvest'):
            worked = e.farming.hired_field_work(worker.id, j.place, j.activity)
            if worked <= 1e-8: raise ValueError('no_field_work')
        elif j.activity == 'project':
            worked, effective = e.projects.contract_work(worker, j.project, j.employer, e.cfg['agriculture']['activity_hours_per_day'])
        elif j.activity == 'order':
            q=self.work_orders[j.order]; craft=float(worker.aptitudes.get('craft',1.0))
            worked=min(e.cfg['agriculture']['activity_hours_per_day'], max(0.,(q.required_hours-q.labor_hours)/craft))
            if worked <= 1e-8: raise ValueError('order_has_no_work')
            effective=worked*craft; q.labor_hours=min(q.required_hours,q.labor_hours+effective)
            if q.labor_hours+1e-7 >= q.required_hours: self._recipe_complete(q)
        left, legs = j.wage, []
        for lot in sorted(coins, key=lambda x:x.id):
            take = min(left, lot.qty)
            if take: legs.append(Leg(lot.id, take, f'p:{worker.id}', f'h:{worker.household_id}' if worker.household_id is not None else f'p:{worker.id}')); left -= take
            if not left: break
        e.inventory.transfer(e.day, legs, actors=(j.employer, worker.id), cell=worker.cell, place=j.place, reason='wage')
        e.ledger.event(e.day, 'paid', [j.employer, worker.id], cell=worker.cell, place=j.place, job=j.id, wage=j.wage, activity=j.activity, project=j.project, labor_hours=worked, effective_hours=(effective if j.activity in ('project','order') else worked))
        j.filled += 1

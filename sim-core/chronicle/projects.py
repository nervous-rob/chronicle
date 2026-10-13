"""Durable, data-driven world projects.

A project is a physical claim on a site plus accumulated labor and reserved inputs.
The first slice deliberately closes only a small loop: clear a place, extract timber,
haul/store it, then build or repair.  The grammar is intentionally wider than that
slice: later roads, mines and bridges should add completion effects as data/resolvers,
not invent another coordination state machine.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import math
from .inventory import Leg

@dataclass
class Project:
    id: int
    kind: str                 # clear | extract | build | repair
    cell: int
    place: int                # exact work place; -1 is not supported in v1
    owner: str
    building: str | None = None
    target_place: int | None = None
    labor_hours: float = 0.0
    required_hours: float = 0.0
    inputs: dict = field(default_factory=dict)       # item -> reserved integer qty
    contributors: dict = field(default_factory=dict) # pid -> hours
    created_day: int = 0

class Projects:
    def __init__(self, engine):
        self.e=engine; self.projects={}; self.next_id=0

    def _owner(self,p):
        return f'h:{p.household_id}' if p.household_id is not None else f'p:{p.id}'

    def _accessible_material_lots(self,p, owner):
        e=self.e; out=[]
        for lot in sorted(e.inventory.lots.values(),key=lambda x:x.id):
            if lot.owner != owner or e.cfg.item(lot.item)['kind']!='material': continue
            if lot.holder==f'p:{p.id}' or (p.place>=0 and lot.holder==f'b:{p.place}') or (p.place>=0 and lot.holder==f'c:{p.cell}' and lot.ground_place==p.place): out.append(lot)
        return out

    def offers(self,p):
        """Only disclose projects a person can begin/continue from their exact place."""
        e=self.e
        if p.place < 0 or p.place not in e.places or e.places[p.place].destroyed_day is not None: return []
        owner=self._owner(p); cell=p.cell; terrain=e.world.terrain[cell]
        out=[]
        active=[q for q in self.projects.values() if q.cell==cell and q.place==p.place and q.owner==owner]
        for q in sorted(active,key=lambda q:q.id):
            out.append({'id':q.id,'kind':q.kind,'building':q.building,'target_place':q.target_place,
                        'progress_hours':round(q.labor_hours,4),'required_hours':round(q.required_hours,4),'continue':True})
        # Clear/extract are site effects. Extraction follows clearing but is not tied to a named building kind.
        if float(e.cells.cleared[cell]) < .999:
            out.append({'kind':'clear'})
        if float(e.cells.cleared[cell]) > 0.0:
            out.append({'kind':'extract','items':['timber']})
        # Existing ruins may be repaired if their catalog requirements can be supplied here.
        for bid,b in sorted(e.places.items()):
            if b.cell!=cell or b.destroyed_day is None or b.owner!=owner: continue
            out.append({'kind':'repair','target_place':bid,'building':b.kind,'materials':e.cfg.building(b.kind)['materials']})
        # Building choices derive only from the catalog. One intact same-kind site per exact work place is enough for v1.
        for kind,spec in sorted(e.cfg.catalog['buildings'].items()):
            if terrain not in [e.world.T[t] if hasattr(e.world,'T') else None for t in []]: pass
            from .world import T
            if int(terrain) not in [T[t] for t in spec['terrain_allow']]: continue
            if spec['requires'] or any(b.cell==cell and b.kind==kind and b.destroyed_day is None for b in e.places.values()): continue
            out.append({'kind':'build','building':kind,'materials':spec['materials']})
        return out

    def _reserve(self,p, owner, need):
        e=self.e; left=dict(need); legs=[]
        for lot in self._accessible_material_lots(p,owner):
            q=min(left.get(lot.item,0),lot.qty)
            if q: legs.append((lot.id,q)); left[lot.item]-=q
        if any(q>0 for q in left.values()): raise ValueError('missing_materials_at_hand')
        # Reservation consumes inputs when the project begins. This gives interruption a real cost;
        # cancellation/refunds are deliberately a later policy layer.
        for lid,q in legs: e.inventory.destroy(e.day,lid,q,'build','project-reserve')
        return dict(need)

    def _new(self,p,a):
        e=self.e; owner=self._owner(p); kind=a.get('project')
        if kind not in ('clear','extract','build','repair'): raise ValueError('unknown_project_kind')
        if p.place<0: raise ValueError('project_requires_place')
        building=a.get('building'); target=a.get('target_place')
        if kind=='clear': need={}; hours=e.cfg['projects']['clear_labor_hours']; building=None; target=None
        elif kind=='extract':
            if a.get('item')!='timber': raise ValueError('unknown_extract_item')
            if float(e.cells.cleared[p.cell])<=0: raise ValueError('uncleared_site')
            need={}; hours=e.cfg['projects']['extract_labor_hours']; building=None; target=None
        else:
            if kind=='repair':
                if type(target) is not int or target not in e.places: raise ValueError('invalid_repair_target')
                b=e.places[target]
                if b.cell!=p.cell or b.owner!=owner or b.destroyed_day is None: raise ValueError('repair_target_unavailable')
                building=b.kind
            if not isinstance(building,str) or building not in e.cfg.catalog['buildings']: raise ValueError('invalid_building')
            spec=e.cfg.building(building); from .world import T
            if int(e.world.terrain[p.cell]) not in [T[t] for t in spec['terrain_allow']] or spec['requires']: raise ValueError('site_not_supported')
            if kind=='build' and any(b.cell==p.cell and b.kind==building and b.destroyed_day is None for b in e.places.values()): raise ValueError('building_already_present')
            need=dict(spec['materials']); hours=float(spec['labor_days'])*e.cfg['agriculture']['activity_hours_per_day']
        q=Project(self.next_id,kind,p.cell,p.place,owner,building,target,0.,hours,self._reserve(p,owner,need),{},e.day)
        self.projects[q.id]=q; self.next_id+=1
        e.ledger.event(e.day,'project_started',[p.id],cell=p.cell,place=p.place,project=q.id,kind=kind,building=building,target_place=target)
        return q

    def work(self,p,a):
        e=self.e; qid=a.get('id')
        if qid is None: q=self._new(p,a)
        else:
            if type(qid) is not int or qid not in self.projects: raise ValueError('unknown_project')
            q=self.projects[qid]
            if (q.cell,q.place,q.owner)!=(p.cell,p.place,self._owner(p)): raise ValueError('project_not_here_or_authorized')
        used=min(e.farming.budgets[p.id],max(0.,q.required_hours-q.labor_hours))
        if used<=1e-8: raise ValueError('project_has_no_work')
        e.farming._spend(p.id,used,'project'); q.labor_hours+=used; q.contributors[p.id]=q.contributors.get(p.id,0.)+used
        e.ledger.event(e.day,'project_worked',[p.id],cell=p.cell,place=p.place,project=q.id,kind=q.kind,labor_hours=used,progress_hours=q.labor_hours)
        if q.labor_hours+1e-7>=q.required_hours: self._complete(q,p.id)
        return q.id,used

    def _complete(self,q,pid):
        e=self.e
        if q.kind=='clear':
            e.cells.clear([q.cell],e.cfg['projects']['clear_fraction'])
        elif q.kind=='extract':
            qty=int(e.cfg['projects']['timber_per_extract'])
            if f'c:{q.cell}' not in e.inventory.holders: e.inventory.register(f'c:{q.cell}')
            e.inventory.create(e.day,'timber',qty,f'c:{q.cell}',q.owner,source='production',process='extract-timber')
            e.inventory.lots[e.inventory.next_id-1].ground_place=q.place
        elif q.kind=='build':
            bid=e.create_place(q.building,q.cell,q.owner)
            q.target_place=bid
        elif q.kind=='repair':
            b=e.places[q.target_place]; b.destroyed_day=None; b.condition=1.0
            spec=e.cfg.building(b.kind); e.inventory.holders[f'b:{b.id}'].store='store' in spec['affordances']
        e.ledger.event(e.day,'project_completed',[pid],cell=q.cell,place=q.place,project=q.id,kind=q.kind,building=q.building,target_place=q.target_place,contributors=sorted(q.contributors))
        del self.projects[q.id]

    def state(self): return {'projects':{k:asdict(v) for k,v in self.projects.items()},'next_id':self.next_id}

"""Settlement bootstrap: pre-existing establishments start with real operators."""
import sys, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from chronicle.config import load
from chronicle.engine import Engine
from chronicle import setup
checks=[]
def check(x,m): assert x,m;checks.append(m);print('ok',m,flush=True)
NOFIRE={s:0.0 for s in ('spring','summer','autumn','winter')}
ov={
 'run.seed':17,'population.initial':8,'population.household_size_dist':[0,1.0],
 'population.roles_mix':{'crafter':1.0},'weather.lightning_p':NOFIRE,
}
e=Engine(load(overrides=ov)); report=setup.generate(e)
rows=list(e.employment.staffing.values())
check(rows and report['staffing']==len(rows),'generated establishments receive initial staff')
for row in rows:
 p=e.people.rows[row.worker]; b=e.places[row.place]
 check(p.role==row.role and e.cfg.role(row.role)['workplace']==b.kind,'operator role matches physical establishment')
 check(b.owner in e.people.owners(p),'initial operator has lawful establishment access')
 check(row.employer is not None and b.owner in e.people.owners(e.people.rows[row.employer]),'staffing records a real establishment owner')
# Kill an operator whose same-household spouse has the same qualified role. The
# lifecycle reconciliation should retain the establishment and install a successor.
row=rows[0]; dead=e.people.rows[row.worker]
mates=[p for p in e.people.rows.values() if p.id!=dead.id and p.household_id==dead.household_id]
check(bool(mates),'fixture supplies a household successor')
# Succession selects an eligible operator, not merely someone with an old label.
# Give the surviving co-resident the compatible craft role before the death batch.
mate=min(mates,key=lambda p:p.id); mate.role=dead.role
dead.died_day=0
e._succession(0,[dead.id])
replacement=e.employment.staffing.get(mate.id)
check(replacement is not None and replacement.place==row.place,'operator death hands establishment work to qualified household successor')
check((row.place,row.role) not in e.employment.vacancies,'filled succession leaves no phantom vacancy')
# Distinct roles sharing one workplace must preserve a vacancy for the lost role,
# rather than treating another operator at that building as a replacement.
from chronicle.employment import Staffing
# This separate role-slot fixture restores household ownership after the preceding
# inheritance assertion, so both co-resident operators have lawful access.
e.places[row.place].owner=f'h:{mate.household_id}'
other=e.add_person(-25*365,dead.cell,e.places[row.place].id,household_id=mate.household_id,role='papermaker',name='Paper')
e.employment.assign_staff(other.id,row.place,'papermaker',setup=True)
other.died_day=0
e._succession(0,[other.id])
check((row.place,'papermaker') in e.employment.vacancies,'shared workshop retains a role-specific vacancy after operator death')

(ROOT/'test-results').mkdir(exist_ok=True)
(ROOT/'test-results'/'establishment-bootstrap.json').write_text(json.dumps({'suite':'establishment-bootstrap','checks':len(checks),'failures':[]},indent=2))
print(f'{len(checks)} checks passed')

"""Source-bound validation. Usage: python tests/validate.py core|checkpoints. core now includes test_agency.py, test_sleep.py, test_cleanup.py, test_config_coverage.py, test_plans.py, test_contact.py, test_worldgen.py, test_rebuild.py, test_llm.py, test_frames.py, test_pressure.py, test_compare.py, test_memory.py, test_prey_coupling.py, test_prey_regulation.py and test_prey_intake.py.
The groups are deliberately separate foreground runs. No stdin transport required.
Projects and Troubleshooting tactics: inspect actual suite output, not dispatch.
"""
import sys, runpy, py_compile, contextlib, json, hashlib, platform, time
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
root=Path(__file__).resolve().parents[1]
logs=root/'test-results/logs';logs.mkdir(exist_ok=True)
mode=sys.argv[1] if len(sys.argv)>1 else 'core'
groups={'core':('test_food_loop.py','test_ecology_controls.py','test_fire.py','test_wildlife_controls.py','test_multi_household.py','test_agency.py','test_sleep.py','test_cleanup.py','test_config_coverage.py','test_plans.py','test_contact.py','test_worldgen.py','test_rebuild.py','test_llm.py','test_frames.py','test_pressure.py','test_compare.py','test_memory.py','test_prey_coupling.py','test_prey_regulation.py','test_prey_intake.py','test_lifecycle.py','test_projects.py'), 'checkpoints':('test_checkpoints.py',)}
if mode not in groups:raise ValueError('argument must be core or checkpoints')
def hashes():
 return {str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(root.rglob('*')) if f.is_file() and '__pycache__' not in f.parts and f.suffix in ('.py','.toml')}
initial=hashes()
for source in (root/'chronicle').glob('*.py'):py_compile.compile(str(source),doraise=True)
results=[]
for name in groups[mode]:
 print('\n=== '+name+' ===',flush=True);started=datetime.now(timezone.utc).isoformat();t=time.monotonic();log=logs/(Path(name).stem+'.txt')
 try:
  with log.open('w') as f,contextlib.redirect_stdout(f),contextlib.redirect_stderr(f):runpy.run_path(str(root/'tests'/name),run_name='__main__')
 except BaseException:
  print(log.read_text());raise
 record={'test':name,'passed':True,'started_at_utc':started,'seconds':round(time.monotonic()-t,3),'log':str(log.relative_to(root))}
 results.append(record);print(json.dumps(record),flush=True)
assert hashes()==initial,'sources changed while validation ran'
report={'group':mode,'finished_at_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),'numpy':np.__version__,'suites':results,'tested_source_sha256':initial}
(root/('test-results/validation-'+mode+'.json')).write_text(json.dumps(report,indent=2))
print('Validation group '+mode+' passed; logs and source hashes saved.',flush=True)

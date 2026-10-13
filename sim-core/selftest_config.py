"""sim-core config self-test: loads defaults, example, sweep; asserts rejections. Exit 1 on failure."""
import os, sys, json
ROOT = os.path.join(os.environ["GOOBSTER_PROJECT_DIR"], "sim-core")
sys.path.insert(0, ROOT)
import chronicle.config as C

cfg = C.load()
print("default run_id", cfg.run_id, "ticks", cfg.derived["total_ticks"], "cells", cfg.derived["cells"])
assert C.load().run_id == cfg.run_id, "run_id not deterministic"

must_reject = {
 "unknown key": {"population.literacy": 0.2},
 "dead section": {"hazards.plague_rate": 0.1},
 "grid keys gone": {"world.grid_w": 64},
 "roles_mix bad role": {"population.roles_mix": {"farmer": 0.5, "trader": 0.5}},
 "stages not increasing": {"stages.youth": 3},
 "auto_record bad event": {"records.auto_record.priest": ["birth"]},
 "seal item missing": {"records.seal_requires_item": "sealing_wax"},
 "cluster weights": {"population.clusters": 3},
 "food weight mismatch": {"catalog.items.grain.weight": 0.5},
 "disease host unknown": {"catalog.disease.murrain.hosts": ["game", "deer"]},
 "role requires unknown cap": {"catalog.roles.judge.requires": ["numerate"]},
 "bad tick length": {"run.days_per_tick": 400},
}
for name, ov in must_reject.items():
    try:
        C.load(overrides=ov)
    except C.ConfigError as e:
        print(f"  rejected  {name:28s} {str(e)[:70]}")
    else:
        print("  ACCEPTED (BUG)", name); sys.exit(1)

ex = C.load(os.path.join(ROOT, "run.example.toml"))
print("example", ex.run_id, ex["run"]["name"])
for name, v in C.load_sweep(os.path.join(ROOT, "sweep.example.toml")):
    print(f"  variant {name:8s} run_id={v.run_id} cells={v.derived['cells']}")
new_cap = C.load(overrides={"capabilities.numerate": {"learn_days": 60, "min_stage": "child", "teachers": ["tutor"], "needs_medium": False, "self_teach": 0.0}})
assert "numerate" in new_cap["capabilities"]
out = os.path.join(ROOT, "config.frozen.example.json")
with open(out, "w") as f: f.write(cfg.to_json())
print("wrote", out)
print("OK")

# Chronicle checkpoints v1

## API

```python
from chronicle.engine import Engine
# After explicit setup, complete at least one normal engine day.
engine.step()
engine.save_checkpoint("world.zip")
restored = Engine.load_checkpoint("world.zip", expected_config=engine.cfg)
restored.run(20)
```

`expected_config` is optional; the archive contains resolved raw config, catalog, derived quantities, seed and run_id. No user TOML/catalog file is consulted during load. Do not modify sources in a live interpreter. Restart after a code change; old-source checkpoints require an explicit future migration.

## Boundary and format

Engine days are the next unprocessed day. Saving day 5 means days 0–4 completed. Day 0 setup and in-progress/failed days are refused. Save before starting external new-day actions. The next load starts the next full day exactly once.

Archive members: manifest.json, state.json, arrays/NNNNNN.npy. Manifest contains schema version, UTC creation timestamp, simulation day, run_id, RNG layout, exact runtime provenance, package source hashes, and member sizes/SHA-256. No pickle, executable imports from the file, or ZIP extraction. Typed JSON preserves map order/key types, sets, tuples, NumPy scalars and shared data identities. NPY preserves array dtype/shape/bytes. Infinity is allowed as an explicit route sentinel, never untyped JSON NaN/Infinity.

Writes are a same-directory temporary file, file fsync, atomic replace and directory fsync on supported platforms. Failed pre-replace writes leave the old target intact. This is single-writer code, not a concurrent database or distributed transaction. A directory fsync failure after replacement can report failure after a valid new target exists; callers should inspect/reload it rather than assume rollback. Checksums are integrity checks, not digital signatures. Apply normal trust/resource isolation to imported files.

Read limit: 128 MiB compressed and total expanded ZIP members, at most 20,000 members. Full in-memory histories and JSON packing use additional memory; production scale/memory benchmarking remains pending. Migration, streaming event histories and retention/rotation are not implemented.

## State completeness and exclusions

The explicit STATE/REFS inventory in checkpoint.py covers all current instance fields, including private daily guards, internal route cache and diagnostic baselines. New fields fail closed until classified. Runtime shared relationships are rebound, not recursively serialized. Dataclass field coverage is checked. Tests also inspect every actual field independently, not just this inventory.

External Ledger output streams, custom callbacks, monkeypatched functions, subclasses, cyclic data containers and persistent RNG Generator objects are unsupported and rejected. Class-level monkeypatching/import hooks are outside this trusted source/runtime contract. No attempt is made to infer caller-injected behavior from a hash.

`digest()` is deliberately not upgraded into the serializer: it omits some state and ignores reporting cadence. For checkpoints use independent object comparison in tests and complete state_fingerprint as an additional check.

## Reproduce

```bash
python sim-core/tests/validate.py checkpoints
python sim-core/tests/validate.py core
```

Reports: test-results/checkpoints.json, validation-checkpoints.json, validation-core.json, and logs/test_checkpoints.txt. Older foundation scripts remain Observatory assets; source ZIP alone does not include every historical asset.

`checkpoints/example-day-5.zip` is an actual synthetic five-day farm save, reloaded and compared through day 25; see test-results/checkpoint-example.json. It requires this exact package source/runtime; do not force-load it on a different architecture.

## Toward Rob's RTX/local-LLM engine (planned, not implemented)

Keep daily physics CPU-side as the authoritative state machine; use the future local model for bounded attention/decisions and a separate chronicler. Only locally available observations, eligible memories and accessible records enter prompts. A model proposes allowed structured actions; the resolver checks location, ownership/consent, capability, shared time budget and resources. Narrative never writes stock or memories directly.

Before connecting inference, persist request identity, model/runtime configuration, prompt/template versions, raw response, validated action and rejection/fallback result. Replay recorded decisions when resuming/comparing runs; do not promise identical generated decisions from seed alone. Add pending/completed inference bookkeeping to the versioned checkpoint contract before asynchronous requests exist. Benchmark the actual workstation/model and run the existing numerical tests there before choosing throughput/batching budgets. No GPU model, driver, runtime endpoint or performance claim is made by this revision.

Tools/persistence conventions: Projects, Project examples, Troubleshooting tactics. Observatory runner checkpoint.json is distinct from this ZIP; a future long-job runner should atomically point to a verified simulation save and its absolute day.

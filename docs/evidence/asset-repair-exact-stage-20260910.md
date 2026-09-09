# P6 exact-byte asset staging evidence — 2026-09-10

## Claim boundary

This run exercised the public S5 seam
`AssetRepairApplication.stage(AssetStageRequest) -> AssetStageResult` against the 14 locally
available GLB representations selected by the committed `003_plate` / `071_can` debt tracer. It
proved exact source digest/size remeasurement, path-free source-manifest binding, loader-closure
enumeration, and immutable CAS admission. It did not execute a simulator, qualify Genesis, settle an
asset, promote an asset, or modify an authoritative historical ledger.

The source tree is intentionally classified as `local_unversioned_robotwin_assets`. It is present
under the local RoboTwin checkout but is neither tracked by nor a gitlink of parent repository commit
`4f6ef23526846ca2f8edccafe01dc9677ed04bb8`. Therefore the absolute deployment locator is not part
of any request, result, or durable identity. The committed path-free manifest is the auditable byte
snapshot; it contains only logical POSIX paths, SHA-256 values, byte counts, and the bound inventory
ArtifactRef—no absolute path, inode, or timestamp.

## Fixed identities

| Record | Bytes | SHA-256 |
| --- | ---: | --- |
| `tests/fixtures/asset_repair_tracer_inventory.json` | 5,859 | `d19dc71323a7c94de129f5217efc158a9076fd70c762dece6b7c7395825d4afa` |
| `tests/fixtures/asset_repair_tracer_source_snapshot.json` | 2,759 | `66793edf4e1e479caf5df64f33648e6567ff5973d2e45e5dda7d582229666aa2` |
| rebuilt repair plan CAS object | 6,698 | `d21594cd4b0315eb2817bc552083b4faa8569e4891e2c8fd21ded9910fbb6993` |
| canonical stage result JSON | 11,597 | `549afab235c79371b85087ca5f6316eda40db4bf391a0c3d21cedcc714f30a65` |
| stage binding | — | `31370d894d0ab7ecd4d33b069eec82db2685dc441d024d7b4a05e8bde17bb0a0` |

The stage result contains 2 assets, 14 staged members, and 57,226,572 total source bytes. Parsing all
14 GLBs found no external loader reference, so each of the 14 representation closures contains its
single GLB root. Every staged member ArtifactRef repeats the source manifest SHA-256 and byte count.

## Reproduction

Run from the clean feature worktree:

```bash
ROBOTWIN_ASSET_ROOT=/home/jingxiang/workspace/robot-harness-gen-env/external/RoboTwin/assets \
pytest -q \
  tests/self_improving/harness/test_asset_staging.py::test_real_plate_and_can_source_snapshot_stages_all_exact_glb_bytes \
  --basetemp=/home/jingxiang/bingsheng/golden-p6-exact-stage-evidence-20260910/pytest
```

Observed result: `1 passed in 0.40s`. The retained local CAS/result evidence root is
`/home/jingxiang/bingsheng/golden-p6-exact-stage-evidence-20260910` (55 MiB, 19 files); the canonical
result is under `pytest/test_real_plate_and_can_source0/asset_stage_result.json`. These bulk CAS bytes
are deliberately not committed.

After staging, all 14 source files were rehashed from the source tree and still matched the committed
manifest. The application has no ledger or source-write operation. Its result explicitly records:

- `exact_source_bytes_staged=true`
- `loader_closure_enumerated=true`
- `simulator_executed=false`
- `runtime_qualification_executed=false`
- `promotion_executed=false`
- `authoritative_ledger_writes_performed=false`

## Remaining gates

This is not Genesis or sim-ready evidence. Collision provenance, stable-pose/settle evidence, native
Genesis load/build/nonzero-step, robot-policy probing, qualification, and atomic ledger promotion all
remain future S5/S3 slices. The source dataset also remains unversioned; a later producer must replace
the local-root binding with a fixed dataset revision or another independently attestable source. The
closure parser currently materializes one captured loader document in memory; this tracer's largest
document is 9,465,600 bytes, so this result is not an unbounded-large-asset resource qualification.

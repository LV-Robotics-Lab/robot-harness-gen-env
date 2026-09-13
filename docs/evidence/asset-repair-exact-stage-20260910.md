# P6 exact-byte asset staging and loader evidence — 2026-09-10

## Claim boundary

Fixed feature commit `8c1c94afae8292dc94ee1d2b08fb7fb08a94bc06` exercised the public S5 seam
`AssetRepairApplication.stage(AssetStageRequest) -> AssetStageResult` for the committed
`003_plate` / `071_can` debt tracer. The staged union contains 14 GLB representation roots and the
seven exact `model_dataN.json` sidecars that RoboTwin `create_actor` reads: 21 unique members,
57,290,434 bytes, and 14 representation-specific loader closures. Each closure contains its GLB
root and matching model sidecar; the real GLBs declare no additional external loader reference.

The stage path remeasured every source digest and size, admitted the exact bytes to a new Harness
CAS, and emitted only CAS refs. Its deep verifier independently bound the explicit expected result,
stage binding, inventory, plan, and source-manifest identities; rebuilt the plan from the trusted
inventory; rehashed all CAS members; reparsed each sidecar; and reran
`loader_document_references` over the exact CAS GLB bytes before accepting each closure.

A separate committed runner then materialized only that verified CAS closure and called the real
RoboTwin `envs.utils.create_actor.create_actor` without a caller-supplied scale. It observed all
seven sidecar scales and ran one headless SAPIEN ground-contact scene for each model variant for 900
steps: seven variants and 6,300 total steps, all with finite final poses and nonzero contact impulse.
Source members, CAS members, and authoritative ledgers rehashed unchanged after the probe.

This is loader-closure and headless ground-contact evidence. It is not a can-on-plate scene, settle
or runtime qualification, Genesis execution, robot-policy testing, or promotion. It produced no
rendered image or video. The source remains `local_unversioned_robotwin_assets`; the RoboTwin worktree
was dirty, so the report binds the exact loaded source closure instead of treating its HEAD as the
complete runtime identity.

## Fixed identities

| Record | Bytes | SHA-256 |
| --- | ---: | --- |
| `tests/fixtures/asset_repair_tracer_inventory.json` | 5,859 | `d19dc71323a7c94de129f5217efc158a9076fd70c762dece6b7c7395825d4afa` |
| `tests/fixtures/asset_repair_tracer_source_snapshot.json` | 4,427 | `f463dbe36b8d31881a2a326bbff87f662aafea754502d261e8b908d0c2e5d388` |
| rebuilt repair-plan CAS object | 6,698 | `d21594cd4b0315eb2817bc552083b4faa8569e4891e2c8fd21ded9910fbb6993` |
| canonical stage result JSON | 16,567 | `a2e786131bb33a3eacd7afd0403dca45a6353a2f6d54aebc100ecb19fda09f23` |
| stage binding | — | `fcf73ec7851e42654a2264d818bfcc60d091ad3669429ecee7f5b7ccd7e0eb89` |
| canonical physical probe request | — | `8461416eee61fcd10b2432a98d9d62d54bfe88479bbf7194fcc1fddfa7b2f019` |
| staged member tree | — | `d57504e41bca15bc8bb9a0af7bbce3449da4ca444177f9b6e4c3dde1f7c9ef8c` |
| final runtime report JSON | 19,318 | `c7a0ac85e1425b12041ab0d91833a0bfb15d57ba04f2b5549ff2fe9d1ecafc5d` |

The report additionally binds:

- runner source: 29,884 bytes, SHA-256
  `b7279c771e54d913ec8415382617cd327b7b9bf6a0e88ed29be77ac6ac43a47c`;
- RoboTwin HEAD `266f3aadf505a4f7fe9af0faa41a20f5f47cd123`, dirty-status SHA-256
  `8a46dc3883c03f4062b915720346e5c37d061eed139ababc2c7dfb73dd7d58ae`, and loaded loader-source
  closure SHA-256 `8f3df14d72174bbb582f87139935ec2d53185b05f2c8325727d1785b21925f37`;
- Python 3.10.20 executable SHA-256
  `ff0d9976054ea10aee690d9f04293889907ddfe84e41f9ab262af8810e537b5e`;
- SAPIEN 3.0.0b1 end-of-run loaded-file closure SHA-256
  `dbd555c9c59cbcff9bda6b59357e6ffd53ee6a075f5fbb40a52eb2835f7a33c8`.

## Reproduction

Run from the clean feature checkout at the fixed commit. First rebuild the exact stage from the
read-only deployment binding:

```bash
ROBOTWIN_ASSET_ROOT=/home/jingxiang/workspace/robot-harness-gen-env/external/RoboTwin/assets \
python -m pytest -q \
  tests/self_improving/harness/test_asset_staging.py::test_real_plate_and_can_source_snapshot_stages_exact_loader_closures \
  --basetemp=/home/jingxiang/bingsheng/golden-p6-fixed-8c1c94a-20260910-tLisCq/pytest \
  -vv
```

Observed: `1 passed in 0.48s`. Then run the committed staged-only probe:

```bash
/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python \
  script/probe_staged_robotwin_assets.py \
  --stage-result /home/jingxiang/bingsheng/golden-p6-fixed-8c1c94a-20260910-tLisCq/pytest/test_real_plate_and_can_source0/asset_stage_result.json \
  --cas-root /home/jingxiang/bingsheng/golden-p6-fixed-8c1c94a-20260910-tLisCq/pytest/test_real_plate_and_can_source0/cas \
  --source-root /home/jingxiang/workspace/robot-harness-gen-env/external/RoboTwin/assets \
  --robotwin-root /home/jingxiang/workspace/robot-harness-gen-env/external/RoboTwin \
  --feature-root /home/jingxiang/bingsheng/worktrees/golden-p6-exact-stage-agent \
  --out /home/jingxiang/bingsheng/golden-p6-fixed-8c1c94a-20260910-tLisCq/asset_stage_real_robotwin_loader_smoke.json \
  --steps 900 \
  --expected-stage-result-sha256 a2e786131bb33a3eacd7afd0403dca45a6353a2f6d54aebc100ecb19fda09f23 \
  --expected-stage-binding-sha256 fcf73ec7851e42654a2264d818bfcc60d091ad3669429ecee7f5b7ccd7e0eb89 \
  --expected-inventory-sha256 d19dc71323a7c94de129f5217efc158a9076fd70c762dece6b7c7395825d4afa \
  --expected-repair-plan-sha256 d21594cd4b0315eb2817bc552083b4faa8569e4891e2c8fd21ded9910fbb6993 \
  --expected-source-manifest-sha256 f463dbe36b8d31881a2a326bbff87f662aafea754502d261e8b908d0c2e5d388
```

Observed: `PASS variants=7 members=21 steps=6300`. The report is path-free even though local paths
are required to reproduce deployment. The external evidence root
`/home/jingxiang/bingsheng/golden-p6-fixed-8c1c94a-20260910-tLisCq` contains 27 regular files and
57,350,105 bytes, including the new CAS, stage result, and report. These bulk/local runtime bytes are
deliberately not committed.

## Test gates

Focused statement and branch coverage:

```bash
coverage erase
coverage run --branch \
  --source=self_improving.harness.asset_stage_verification,self_improving.harness.asset_staging,self_improving.harness.schemas.asset_staging \
  -m pytest -q \
  tests/self_improving/harness/test_asset_staging.py \
  tests/self_improving/harness/test_schema_catalog.py \
  tests/self_improving/harness/test_staged_robotwin_probe.py
coverage report -m --fail-under=100
```

Observed: `183 passed, 1 skipped`; 780/780 statements and 278/278 branches, 100%.

Root functional regression:

```bash
python -m pytest -q \
  --deselect tests/self_improving/harness/test_qualified_replay_bundle.py::test_checked_in_replay_qualification_matches_current_source_identity
```

Observed: `3310 passed, 20 skipped, 1 deselected`. The deselected qualification snapshot was already
known to be stale and was not rewritten by this feature.

## Remaining gates

The two-asset tracer does not repair the remaining 162-ledger population or its 4,082 historical v3
violations. It also does not establish a fixed upstream asset-dataset revision, collision quality,
can-on-plate support/containment, settle qualification, rendered replay, Genesis load/build/step,
`genesis.robot_policy@1`, or atomic ledger promotion. The loader parser currently materializes one
loader document in memory; the largest document in this tracer is 9,465,600 bytes, so this evidence
does not qualify unbounded-large assets.

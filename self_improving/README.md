# Self-Improving Platform

This directory is the integration layer above the deterministic `scene_gen/`
compiler. It consolidates previously separate workspaces without merging their
runtime assumptions into the core trust boundary.

## Ownership

| Path | Status | Responsibility |
| --- | --- | --- |
| `stage5/` | active | Multi-agent scene design, grounding, critique, MCP-lite tools, prompts, and tests recovered from the stage-05 validation tree. |
| `alchedata/` | active | `/gen-env -> /collect -> /train -> /evaluate -> /diagnose -> /transfer`, failure memory, promotion gates, schemas, tests, and curated structured evidence. |
| `asset_pipeline/active/` | active | Asset discovery, ingest, ledger, catalog integration, Web Studio, and simulator-migration adapters from `env-gen-dev`. |
| `asset_pipeline/active/shared/openxsim/` | active shared core | First-party OpenXSim IR, asset contracts, adapters, conformance tests, and bridge scripts consumed by both asset reuse and simulator migration. Vendored dependencies and the MetaSim checkout are excluded. |
| `asset_pipeline/branch_overlays/` | preserved | Source-only snapshots of the alias-screening and asset-sources worktrees. |
| `asset_pipeline/workbench_snapshots/` | read-only | Source and notes recovered from ignored `work/` trees; historical paths are retained and these are not supported entry points. |
| `asset_pipeline/receipts/` | active metadata | Acquired-asset manifest and ledger/model metadata; downloaded meshes and renders are deliberately external. |
| `contributor_notes/` | historical evidence | Unique planning, design, and handoff documents recovered from the named student workspaces, with source-path and SHA-256 index. |
| `sim_adapters/agenticsim_runtime/` | active adapter | Orchestration scripts and hermetic tests recovered from the former non-Git runtime workspace. It is not the retired AgenticSim product repository. |
| `onboarding/yeyuxuan/` | preserved source | RoboLab migration tools, asset overrides, and per-asset provenance recovered from the onboarding report. |
| `legacy/stage04/` | read-only | Historical stage-04 source snapshot retained for provenance and diffing. New work belongs in `stage5/` or another named module. |
| `legacy/robotwin_text2env_alt/` | read-only | Alternate `text2env.tabletop.v0` prototype at source tip `c226358`, including its repair utility and bounded smoke evidence. It must not replace the active Stage 5 compiler. |
| `validation_evidence/openxsim_20260716/` | preserved evidence | Compact JSON, logs, and replay scripts recovered from the former OpenXSim acceptance workspaces. |
| `workspace_archives/20260716/` | provenance | Full-file SHA-256 manifest and the repository Release pointer for the consolidated Jingxiang workspaces. |

Independent projects remain Git submodules in `external/`:

- `OpenReal2Sim` for simulator-translation logic.
- `digital-cousins` for digital-cousin discovery and generation.
- `MetaSim` at the exact commit used by the archived OpenXSim validation sandbox.

The old AgenticSim checkout was a sparse historical TacHarness state. Its four
unique Awesome-Isaac audit files were archived in TacHarness before that local
checkout was deleted; it is not a dependency here.

The PEARL presentation layer lives in `apps/pearl_evidence_portal/`. Its source
history and bounded hosted-report subset are versioned together so the portal
build remains self-contained; build output and dependency caches remain
excluded. It presents platform evidence but does not define acceptance gates.

## Checkout audit

```bash
git submodule update --init --recursive
python -m self_improving --json
```

The audit checks source availability only. It does not import GPU frameworks,
launch simulators, download models, or touch a robot.

Install the hermetic platform-test dependencies with:

```bash
pip install -e '.[dev,demo,platform]'
```

Alchedata keeps its original script-style imports, so its standalone test
command includes both the module root and `scripts/` on `PYTHONPATH`.

Run the complete hermetic regression matrix from the repository root:

```bash
script/run_self_improving_tests.sh
```

The script expands to the following module-level commands:

```bash
python -m pytest -q
PYTHONPATH=.:self_improving/stage5 python -m pytest -q self_improving/stage5/tests
PYTHONPATH=self_improving/alchedata:self_improving/alchedata/scripts \
  python -m pytest -q self_improving/alchedata/tests
PYTHONPATH=self_improving/sim_adapters/agenticsim_runtime \
  python -m pytest -q self_improving/sim_adapters/agenticsim_runtime/tests
(cd self_improving/asset_pipeline/active/1_asset_reuse && \
  PYTHONPATH=.:scripts:../shared/openxsim/source/agenticsim:../../../.. \
  python -m pytest -q tests)
python -m pytest -q self_improving/asset_pipeline/active/web/tests
(cd self_improving/asset_pipeline/active/shared/openxsim && \
  PYTHONPATH=source/agenticsim python -m pytest -q tests)
```

The current source-only baseline is 542 passed and 6 skipped. The skips are
explicit physical/runtime checks that require SAPIEN or the excluded raw
Isaac, SceneAgent, media, and report bundles; they are not silently mocked.

## Artifact policy

This repository keeps source, tests, schemas, prompts, small structured
evidence, asset ledgers, checksums, and provenance. It excludes virtual
environments, caches, training runs, checkpoints, generated datasets, bulk
screenshots/video, and third-party meshes. Those files are large, frequently
regenerated, and may carry separate redistribution terms. Their identifying
metadata stays in `asset_pipeline/receipts/` and `onboarding/yeyuxuan/`.
The 2026-08-14 asset receipt covers 12,047 files (27,637,543,884 bytes) in
Yuxin's local `301`–`361` asset namespace; because `storage_uri` is still null,
that checksum inventory is audit evidence rather than remote recovery proof.

The PEARL portal is the narrow exception for already curated, browser-served
report media under `apps/pearl_evidence_portal/public/reports/`; raw runs and
the portal's generated `dist/` tree are still excluded.

The 2026-07-16 OpenXSim acceptance workspaces are preserved without sibling
checkouts: compact reviewable evidence is tracked under `validation_evidence/`,
while the complete cache-filtered workspace bundle is attached to the
`workspace-consolidation-20260813` repository Release. Its full file manifest
and archive digest are tracked under `workspace_archives/20260716/`.

Exact source workspaces, commits, preservation branches, and exclusions are in
[`source_inventory.json`](source_inventory.json).

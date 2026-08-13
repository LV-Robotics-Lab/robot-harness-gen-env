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
| `asset_pipeline/branch_overlays/` | preserved | Source-only snapshots of the alias-screening and asset-sources worktrees. |
| `asset_pipeline/receipts/` | active metadata | Acquired-asset manifest and ledger/model metadata; downloaded meshes and renders are deliberately external. |
| `sim_adapters/agenticsim_runtime/` | active adapter | Orchestration scripts and hermetic tests recovered from the former non-Git runtime workspace. It is not the retired AgenticSim product repository. |
| `onboarding/yeyuxuan/` | preserved source | RoboLab migration tools, asset overrides, and per-asset provenance recovered from the onboarding report. |
| `legacy/stage04/` | read-only | Historical stage-04 source snapshot retained for provenance and diffing. New work belongs in `stage5/` or another named module. |

Independent projects remain Git submodules in `external/`:

- `OpenReal2Sim` for simulator-translation logic.
- `digital-cousins` for digital-cousin discovery and generation.

The old AgenticSim checkout was a sparse historical TacHarness state. Its four
unique Awesome-Isaac audit files were archived in TacHarness before that local
checkout was deleted; it is not a dependency here.

## Checkout audit

```bash
git submodule update --init --recursive
python -m self_improving --json
```

The audit checks source availability only. It does not import GPU frameworks,
launch simulators, download models, or touch a robot.

## Artifact policy

This repository keeps source, tests, schemas, prompts, small structured
evidence, asset ledgers, checksums, and provenance. It excludes virtual
environments, caches, training runs, checkpoints, generated datasets, bulk
screenshots/video, and third-party meshes. Those files are large, frequently
regenerated, and may carry separate redistribution terms. Their identifying
metadata stays in `asset_pipeline/receipts/` and `onboarding/yeyuxuan/`.

Exact source workspaces, commits, preservation branches, and exclusions are in
[`source_inventory.json`](source_inventory.json).

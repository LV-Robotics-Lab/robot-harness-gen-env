# HYX env-gen history merge — 2026-08-14

This note records the retirement of the separate local
`/home/jingxiang/hyx/env-gen-dev` checkout into HYX's
`/home/jingxiang/hyx/robot-harness-gen-env` worktree.

## Source and strategy

- Source repository: `huyuxinn/env-gen-dev`
- Imported source tip: `e99e11975764710e2080468662a74aaed00ee0df`
- Destination branch: `worktree/hyx`
- The complete source history is retained as the second parent of the merge.
- The existing `self_improving/asset_pipeline/active` tree remains authoritative.
  It already contains the integrated env-gen implementation plus later path,
  portability, recovery, and runtime-validation fixes, so the older source tree
  was not allowed to overwrite it.

Thirteen source-only historical conversion/probe/backfill files were recovered
under `1_asset_reuse/archive/`. They remain legacy references; in particular,
archived scripts may contain historical absolute paths and are not supported
entry points. The intentionally red v1 backfill test was retained as
`backfill_ledger_test_legacy.py` so modern pytest does not collect it. The
source `shared/README.md` was also retained.

The source checkout's 33 small ledger files were moved into this personal
worktree's ignored `self_improving/asset_pipeline/active/data/` tree. Shared
copies and provenance receipts remain in the canonical main worktree. The
personal data manifest has SHA-256
`071f91804eca581f0aa80f006cd9b61c7d0241b324cdab1cd6c188370a940f87`;
it is local evidence, not a remote payload backup.

## Incident-note correction

The imported `e99e119` commit appended a provisional claim that all RoboTwin
copies and all contributor material had been destroyed. Live reconciliation
showed that the directories had been intentionally consolidated: the canonical
worktree still contains the RoboTwin runtime and the migrated asset data, with
manifests and structured evidence. The correction at the top of
`docs/2026-08-14-data-loss-incident.md` and the canonical cleanup receipt are
the authoritative final state; the `e99e119` wording is retained only in Git
history as a time-of-observation record.

After branch push and readback verification, the separate local checkout can be
removed. The personal GitHub source history is reachable from the merged HYX
branch, while ignored payload recovery still depends on its recorded storage
and manifests rather than Git ancestry alone.

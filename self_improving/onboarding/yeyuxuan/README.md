# Yeyuxuan onboarding recovery

This directory keeps the reviewable source and provenance recovered from the
local RoboLab onboarding workspace:

- the reusable RoboLab-to-RoboTwin migration CLI and five earlier one-off tools;
- the 10-, 20-, and 21-asset historical summaries and the current 22-asset PASS summary;
- twenty-two per-asset `SOURCE.md` records;
- before/after asset override configurations used to diagnose orientation,
  semantic, and mass issues.
- a SHA-256 manifest for all 100 local overlay files without redistributing the
  third-party meshes and rendered media.

The source RoboLab checkout was clean at NVLabs/RoboLab commit
`97bc1e7`. The complete 22-commit implementation history was merged at
`6b04a78` and remains reachable from `main`; the former archive tip
`cf9123a9c6736fa76da0c1cc11e2dfc3d0df7910` was retired after reachability
verification. Generated scene packages, runtime JSON, GLB meshes, PNG
previews, MP4 videos, and tar bundles remain external. Exact source, digest, and
exclusion context is recorded in `overlay_manifest.json` and
`../../source_inventory.json`.

Asset 921 was migrated later in the personal worktree. Its reviewable source and
runtime facts are in `robolab_sources/921_robolab_tomato_soup_can/SOURCE.md` and
`robolab_migration_summary_21.md`; its large local evidence is intentionally not
part of the historical 901–920 overlay manifest.

Asset 922 follows the portable receipt pattern established by 921. Its source,
conversion, and runtime facts are in `robolab_sources/922_robolab_gelatin_box/`
and `robolab_migration_summary_22.md`; `overlay_manifest_922.json` and
`overlay_manifest_922.sha256` cover only its external local overlay bytes.

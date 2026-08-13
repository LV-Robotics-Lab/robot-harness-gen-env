# Yeyuxuan onboarding recovery

This directory keeps the reviewable source and provenance recovered from the
local RoboLab onboarding workspace:

- the reusable RoboLab-to-RoboTwin migration CLI and five earlier one-off tools;
- the 10-asset historical summary and the current 20-asset PASS summary;
- twenty per-asset `SOURCE.md` records;
- before/after asset override configurations used to diagnose orientation,
  semantic, and mass issues.
- a SHA-256 manifest for all 100 local overlay files without redistributing the
  third-party meshes and rendered media.

The source RoboLab checkout was clean at NVLabs/RoboLab commit
`97bc1e7`. The complete 22-commit implementation history is preserved on
`archive/yeyuxuan-onboarding-20260813`; current core changes are merged into the
main repository. Generated scene packages, runtime JSON, GLB meshes, PNG
previews, MP4 videos, and tar bundles remain external. Exact source, digest, and
exclusion context is recorded in `overlay_manifest.json` and
`../../source_inventory.json`.

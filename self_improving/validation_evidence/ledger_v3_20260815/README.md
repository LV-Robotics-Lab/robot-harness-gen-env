# Ledger v3 runtime evidence archive

This directory keeps the compact, machine-readable evidence behind the
2026-08-15 ledger-v3 and 2026-08-16 follow-up reports:

- the 44-row color-selection matrix before and after model-level color support;
- the final B11 containment regression reports;
- the final hanger and TV runtime validation reports;
- the two Isaac settle reports and their write-back facts.

`manifest.json` records the SHA-256 of each source file and archived copy. The
only transformation is removal of historical host-specific repository prefixes
from path strings. Runtime media, compiled scenes, meshes, and superseded runs
remain reproducible outputs of the referenced scripts and are not part of this
source archive.

Reproduction entry points:

- attribute matrix and catalog:
  `self_improving/asset_pipeline/active/1_asset_reuse/scripts/5_catalog/`;
- RoboTwin runtime validation: `script/run_scene_runtime.py`;
- Isaac settle validation:
  `self_improving/asset_pipeline/active/2_sim_migration/scripts/isaac_settle_check.py`.

# External asset receipts

This directory preserves the provenance needed to audit Yuxin's local asset
library without redistributing third-party meshes, textures, or rendered media.

Captured from `jingxiang:/home/jingxiang/yuxin/env-gen-dev/data/asset_library`
on 2026-08-14:

- `acquired_manifest.json`: the current acquisition selection, containing 51
  groups and 51 selected asset IDs in the `301`–`361` namespace.
- `asset_library_metadata/`: 63 top-level asset directories and 132 small
  ledger/model/source metadata files. The directory count is larger because
  older selections reused some numeric IDs before the current manifest was
  finalized.
- `asset_library_301_361.sha256`: SHA-256 for all 12,047 files then present in
  the local library, totaling 27,637,543,884 bytes. The manifest itself hashes
  to `e661ac55235d9eec3a1fe5af342b14306b3dcc51a734d54a93f1a037c9a21e60`.

The GLB/USD files, texture trees, PNG snapshots, and source caches are not in
Git. They are external/downloaded assets with source-specific terms. Their
exact digests remain in the full manifest, but there is currently no verified
remote recovery location (`storage_uri` is null). Do not delete the local
asset payload solely because these receipts exist.

Regeneration and acquisition code lives in `../active/1_asset_reuse/`.

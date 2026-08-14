# Student workspace cleanup, 2026-08-14

This receipt records the consolidation of the named Jingxiang contributor
workspaces into `/home/jingxiang/workspace/robot-harness-gen-env`.

Source branches and ignored source snapshots were preserved in Git before any
checkout was removed. Ignored experiment data, checkpoints, dependencies,
RoboLab material, and Yuxin's asset payload were moved into ignored locations
inside the canonical checkout. Their local manifests are listed in
`student_workspace_cleanup.json`; `storage_uri: null` means they are not remote
backups.

The three `.rsync` files are checksum-mode dry-run differences against the
retained or canonical RoboTwin asset tree. They show that the deleted 16 GB
Bingsheng and Yuxin trees differed only in small configuration/cache files and
two generated proxy directories; the final concurrently restored Yuxin copy
differed only in the six Curobo YAML files repaired to use the canonical path.
The relevant source/config deltas were retained before deletion.

The active training process exited naturally at epoch 599/global step 25799.
Its `600.ckpt` was hashed, the whole RoboTwin runtime was moved to
`external/RoboTwin`, 227 generated links and six Curobo configs were repaired,
and a resource-uncontended SAPIEN replay passed. The compact replay evidence is
tracked in `validation_evidence/student_workspace_20260814/`; the checkpoint
and raw runtime media remain local-only and are not remote backups.

The accompanying JSON preserves `local_data` as the path observed when this
receipt was captured. The active repository convention was later simplified to
the ignored root `data/`; the historical fields are intentionally not rewritten.
The original manifest is retained locally as
`data/MANIFEST.legacy-local-data.sha256` with SHA-256
`e03e7938e1122339a77bd2f86b2ab223c383dfd1840e65c6973626fcafe9f7b5`.
The path-rewritten `data/MANIFEST.sha256` has SHA-256
`6ea8a140122937a695720d11ed36604c4fb920125fea3a15bb2d6bde557ddbab`
and all 9,576 entries passed `sha256sum --quiet -c` after the rename.

# Student workspace cleanup, 2026-08-14

This receipt records the consolidation of the named Jingxiang contributor
workspaces into `/home/jingxiang/workspace/robot-harness-gen-env`.

Source branches and ignored source snapshots were preserved in Git before any
checkout was removed. Ignored experiment data, checkpoints, dependencies,
RoboLab material, and Yuxin's asset payload were moved into ignored locations
inside the canonical checkout. Their local manifests are listed in
`student_workspace_cleanup.json`; `storage_uri: null` means they are not remote
backups.

The two `.rsync` files are checksum-mode dry-run differences against Gujie's
retained RoboTwin asset tree. They show that the deleted 16 GB Bingsheng and
Yuxin trees differed only in small configuration/cache files and two generated
proxy directories; the relevant source/config deltas were retained before
deletion.

Gujie's RoboTwin directory remains temporarily in place because an active
training process uses it as its working directory. Moving it, repairing the
generated shadow links, and completing a resource-uncontended SAPIEN replay
are explicit remaining gates—not implied successes.

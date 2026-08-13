# Jingxiang consolidation runtime evidence — 2026-08-14

This directory is the compact, reviewable proof for the final runtime gate of
the student-workspace consolidation. It does not contain raw RoboTwin assets,
the generated video, or the training checkpoint.

The catalog was generated from the canonical RoboTwin checkout at source
commit `266f3aadf505a4f7fe9af0faa41a20f5f47cd123`. It contained 127 asset
entries, 585 models, and 15 usable models. The catalog file itself remained a
local generated artifact with SHA-256
`9ad08664823ee1aaf7b9792c10d62d64f60010f2f67ae65fedbc05ff81019be6`.

The committed scene and reports prove that
`place_a_can_on_the_table_acd20a6814` loaded the real `071_can` model from the
canonical path and completed 900 simulation steps. The validation report is
`pass`, with `fail_count=0` and `not_run_count=0`; it records zero initial and
final robot collisions, no object penetration or drop, full expected table
contact across the 60-sample contact window, and 989 visible pixels.

The runtime emitted 120 video frames (100 unique). The raw PNG/MP4 files remain
under `/tmp/robot-harness-runtime-evidence-20260814` on Jingxiang and are not a
durable backup. Exact hashes of the tracked evidence are in `MANIFEST.sha256`.

# Jingxiang workspace consolidation archive

This record closes the standalone validation workspaces that previously lived
beside `robot-harness-gen-env` on `jingxiang@100.64.0.6`.

The complete cache-filtered payload is attached to the repository Release
`workspace-consolidation-20260813` as
`robot-harness-gen-env-workspace-archives-20260716.tar.gz`.

- Size: `139152925` bytes
- SHA-256: `18ce98febb5c3cd67ac05972fc298d8967334c6d14a108f29abb874e3904f8c7`
- Download: <https://github.com/LV-Robotics-Lab/robot-harness-gen-env/releases/download/workspace-consolidation-20260813/robot-harness-gen-env-workspace-archives-20260716.tar.gz>
- Per-file hashes: [`MANIFEST.sha256`](MANIFEST.sha256)

The archive contains:

- `AgenticSim-openxsim-acceptance`
- `openxsim-validation`
- `openxsim-final-acceptance`
- `openxsim-crosssim-acceptance`
- `robotwin-text2env-stage04-validation`
- `robotwin-text2env-stage05-validation`

Nested Git metadata, Python bytecode, pytest/ruff caches, and egg-info were
excluded. The clean `robot-harness-gen-env-prompt-matrix-20260719` clone was not
duplicated because its content and history were already merged; it was observed
at `73d8c08012c69d52a9837633f8c28fcffd90536b` before cleanup.

Compact OpenXSim JSON, logs, and replay scripts are tracked directly under
`self_improving/validation_evidence/openxsim_20260716/` for ordinary review.

Verify the downloaded archive with:

```bash
sha256sum robot-harness-gen-env-workspace-archives-20260716.tar.gz
```

# Importable asset-reuse package

This directory was moved once from `1_asset_reuse` to `asset_reuse` for canonical C06.
The provider engine remains the same source; this change does not qualify it, replace its
inference policy, or introduce another search implementation.

Public import prefix: `self_improving.asset_pipeline.active.asset_reuse.lib`.
`agenticsim.openxsim` remains an independent declared dependency from the existing shared
source tree; no upstream code is copied into this package. Library sibling imports are relative.
Legacy standalone scripts/tests retain their local `lib` entry convention and may use the new
directory on PYTHONPATH; canonical callers must use the public package import, not that alias.

The migration import test runs from an unrelated working directory, imports the real provider
modules with the declared OpenXSim source dependency, and checks that neither torch nor SAPIEN
is initialized. No search, network, Qwen or physics runtime is executed.

Packaging configuration and external consumers are owned by the integration task and must
replace the old directory/package locator before the migration feature is complete.
Historical Markdown references below this directory have not been rewritten as new runtime facts.
Original Git history and authorship are preserved by the directory move.

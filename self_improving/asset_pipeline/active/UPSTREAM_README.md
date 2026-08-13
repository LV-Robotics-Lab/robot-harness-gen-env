# Active asset and simulator pipeline

This directory is now a module of `robot-harness-gen-env`; it is no longer a
standalone personal repository layered over a symlinked clone. The stable
compiler is available from the repository root, while this module owns asset
discovery, ingestion, admission, catalog extension, Web Studio, and simulator
migration adapters.

## Layout

| Path | Responsibility |
| --- | --- |
| `1_asset_reuse/` | Asset search, acquisition, materialization, validation, ledger, catalog integration, and acceptance tests. |
| `2_sim_migration/` | Simulator migration adapters and bridge experiments. |
| `shared/openxsim/` | First-party OpenXSim IR, adapter contracts, and conformance tests shared by both stages. |
| `web/` | Local asset-pipeline inspection and control surface. |
| `runtime_config.py` | Relocatable defaults for repository, pipeline data, RoboTwin, catalogs, and Python runtimes. |

## Runtime configuration

Repository source is resolved relative to this file. External and ignored
runtime data is injected with environment variables instead of contributor
paths:

- `ASSET_PIPELINE_ROOT`
- `GEN_ENV_ROOT`
- `ROBOTWIN_ROOT`
- `ROBOTWIN_SHADOW_ROOT`
- `ASSET_CATALOG`
- `ASSET_OVERRIDES`
- `OBJAVERSE_DATA_ROOT`
- `SAPIEN_PYTHON`
- `ISAAC_PYTHON`

Downloaded assets, generated shadows, rendered evidence, results, and caches
remain outside Git. Their provenance is tracked in `../receipts/`; the current
full external-asset receipt has `storage_uri: null`, so a checksum manifest is
not permission to delete the local payload.

Run the hermetic tests from the repository root with
`script/run_self_improving_tests.sh`. Physical replay still requires a real
RoboTwin/SAPIEN runtime and must be recorded separately.

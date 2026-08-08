# Pipeline Studio

Flask web frontend for the asset-adaptive scene pipeline (`1_asset_reuse/scripts/scene_acquire.py`
+ `run_scene_runtime.py`). Submit a prompt in the browser, watch stage-by-stage progress, evidence
JSON, and QC/render images for a real pipeline run. Includes a history browser over past
`results/_test/` runs.

## Start

```bash
nohup bash web/serve.sh > /tmp/pipeline_studio.log 2>&1 &
```

Open http://100.64.0.6:8811 (via Headscale).

## Stop

```bash
pkill -f web/app.py
```

## Notes

- Single-run lock: only one pipeline run at a time; a second submit while busy gets HTTP 409.
- The 演示缺口 (exclude_category) field filters the asset catalog for that run only, forcing a
  real tier-1..3 gap import — the imported asset is written into the real catalog (not a sandbox),
  so repeated use may duplicate catalog content.
- Runs submitted from the web UI land in `results/web_runs/` (gitignored), separate from the
  `results/_test/` history fixtures.

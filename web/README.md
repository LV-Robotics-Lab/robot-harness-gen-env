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

## v2 前端（2026-08-12）

横向 stepper 实时展示 7 阶段状态与耗时（服务端 `stage_timeline` 统一计算），
运行中分区增量渲染（展开态/滚动不丢），日志按字节 offset 增量拉取并折叠重复行。

新增只读接口（旧接口字段全部保留）：
- `GET /api/run/<group>/<id>/log?offset=N` — 日志增量读取（单次 ≤256KB）
- `GET /api/run/<group>/<id>/files` — run 目录产物清单（白名单后缀，含 .py）
- `/status` 新增 `stage_timeline` / `log_size` / `server_now`；`/api/runs` 新增 `current`

测试：`python -m pytest web/tests/`（env-gen-yuxin 环境）。

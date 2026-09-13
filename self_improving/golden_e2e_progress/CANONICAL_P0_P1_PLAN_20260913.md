# Canonical P0/P1 推进计划（2026-09-13）

本计划依据 [roadmap](../../repo-docs/canonical-x2env-roadmap.md) 的 P0/P1 与本轮只读发现制定。
它是执行计划，不是能力声明；每一步完成与否以 RESULTS 中的实际记录为准。

## 本轮发现（执行前）

- `worktree/bingsheng` HEAD `e175e47`，工作树干净，远端同步；四例 matrix v2 只用 local 资产。
- 文档中的本机入口 `/home/jingxiang/bingsheng/runtime/harness/` 实体存在：deployment.json 只配置
  `local_enabled`、`codex`、`genesis`，没有 `web` 和 `reconstruction`；`prepared.json` 里的旧
  `canonical-x2env-user-entry-20260913.AydTBI` 是迁移前地址，仅为历史记录。
- web：`YuxinProviderAdapter` 直接复用 `asset_reuse` 引擎；历史隔离实验以 pinned
  `KhronosGroup/glTF-Sample-Assets@90d7ede1` 的 `github_tree` provider 真实检索 4 候选并下载
  Box.glb（1664 bytes，CC-BY-4.0/Cesium）。当前 canonical 部署未配置该 provider，无完整 workflow。
- reconstruction：`ReconstructionAdapter` 调用固定外部 checkout 的 `controlled_segmentation` 与
  `reconstruction_only`。隔离根已归档到
  `/home/jingxiang/bingsheng/archive/2026-09-13-root-layout/preserved/canonical-trellis1-20260913.1U7bmo`
  （4.7G：TRELLIS/SAM2 源、模型权重、gujie-reconstruction 外部 checkout HEAD `ed3b7e2`）。
  其中 `venv/bin/python` 是指向 Gujie simfoundry conda 解释器的符号链接，
  `reconstruction-runtime.json` 仍写迁移前绝对路径，两者都不满足当前 adapter 的非符号链接/固定
  路径要求，需要重新准备而不是直接引用。
- x2env 测试组因旧 ignored `1_asset_reuse/` 缓存目录触发迁移断言；该目录只含 `__pycache__` 和
  `.coverage`，已清理，测试重跑记录见 RESULTS。

## 步骤与验收

| 步骤 | 内容 | 验收 | 预算 |
|---|---|---|---|
| P0-a | 增加公开 `check` 子命令：只读解析 deployment，逐项报告 codex/genesis/local/web/reconstruction 的配置存在性、pin 校验与缺失字段；不打印密钥、不加载模型 | TDD 单测；对当前部署实际输出 web/reconstruction 为 `not_configured` | ≤30 min |
| P1-web-1 | 为当前入口准备 pinned `providers.json`（仅 `github_tree` 固定 commit），写入 runtime/harness 私有配置目录并计算 sha256；新增 `deployment.json` 的 `web` 段 | `check` 报告 web `configured` 且 pin 通过 | ≤10 min |
| P1-web-2 | 一次真实 canonical text 请求（`--source web`）跑完整 workflow：搜索、下载、许可、规范化、登记、Genesis replay/observe/validate、package | 终态与全部阶段耗时、失败码、路径写入 RESULTS；失败保留现场不重跑 | ≤1170+30 s |
| P1-rec-1 | 重建后端重新固定：新隔离根下用实体解释器路径重建 venv（或直接使用 conda 实体路径）、更新 runtime JSON 到当前路径、确认外部 checkout HEAD clean、计算 pin | adapter 身份检查通过（无 `deployment_identity_mismatch`） | ≤30 min |
| P1-rec-2 | 为一张已授权原图（CC-BY-4.0 / `x2env1.0`）写派生授权 ArtifactRef 到当前 CAS；配置 `reconstruction` 段 | `check` 报告 reconstruction `configured` | ≤10 min |
| P1-rec-3 | 一次真实 image 请求（`--source reconstruction`）跑完整 workflow，证明新 geometry digest 运行前不在库中 | 同 P1-web-2 | ≤1170+30 s |
| P1-x | 至少一次来源耗尽后转下一来源成功（local miss → web），沿用 P1-web-2 的请求或独立一次 | 记录 router 记录中的 `source_adapter`/失败与后继成功 | 复用 |
| 文档 | 更新 roadmap P1 措辞、接入指南、USAGE、provenance LEDGER、TODO/RESULTS/DECISIONS；逐功能提交并 push bingsheng | `git diff --check`、本地链接检查 | — |

## 边界

- 不修改 Gujie dirty worktree，不 import 其 editable bytes；只使用 clean 外部 checkout。
- 输出许可来自用户已批准的派生授权，不从模型/代码许可推断；web 资产保留 Cesium 等原作者署名。
- 每次核验不超过 30 分钟；超时保留产物、不原样重跑。
- 不做 main PR、正式资格、robot policy、多物体（P2）。

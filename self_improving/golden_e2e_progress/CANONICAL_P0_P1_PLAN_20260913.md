# Canonical P0/P1 推进计划（2026-09-13）

本计划依据 [roadmap](../../repo-docs/canonical-x2env-roadmap.md) 的 P0/P1 与本轮只读发现制定。
它是执行计划，不是能力声明；每一步完成与否以 RESULTS 中的实际记录为准。

## 实时检查点

- P0-a已提交a0fc1e0，后补CI库存及安装CLI契约；24项初始测试、72项prepare/web/库存测试通过。
- P1-web-1已配置；web-01源轴失败经076c801修复。web-02已下载/规范化/入库/预览，视觉语义拒绝；
  duck-01许可证受阻；7次web均未完整成功，不标P1-web-2/P1-x完成。
- P1-rec-1/2已配置实体解释器、新runtime和派生授权；原固定源码/模型库clean。
- reconstruction-01已实际SAM2分割/TRELLIS新几何49.31秒，规范化登记后geometry不在运行前库中；
  后续各例结果已记录；第四例259.017秒资产resolve与ground成功，compile因unsupported_structural_color失败。
- 所有case命令、时限、测量、summary在 `/home/jingxiang/bingsheng/runtime/harness/p1-evidence/`；
  state及输出分别为同父目录 `state/`、`outputs/`。失败不重写或从测量分母剔除。
- 进一步检查点：数值mesh修复5e7116d真实越过旧节点限制，reconstruction-02因颜色白灰不符pink失败；
  18ce9af增加模型颜色估计→既有规范化传递，并说明刚体无关节应为null。第三次重建299.796秒因
  重复几何证据超256MiB失败；方盒109.797秒仍因动画/扩展候选耗尽失败。
- 完整证据页：[`canonical-p0-p1-20260913.md`](../../docs/evidence/canonical-p0-p1-20260913.md)。

## 实跑后续优先级（不以换prompt代替修复）

0. 优先修复预览回执内嵌及运行JSON缩进放大；保持完整拓扑/哈希和256MiB预算，先RED/GREEN，
   再原现场派生存储核验，最后新固定版本真实重建。不重写旧CAS或失败终态。
1. 当前web仅一次词法query，cube没有扩展到box，命中动画样本即耗尽；需要基于失败类型设计有界
   查询修订/更丰富来源，保留原类别和所有失败。不得映射某个prompt到固定资产URL。
   已实现最多一次失败绑定词法修订和重复停止，真实检验待执行。
2. 资产规范化仅支持有限glTF core。occlusion/扩展/动画必须分别明确支持或拒绝，不能删字段制造通过。
3. 原图重建颜色曾丢失，18ce9af均匀色设计修复待真实通过；纹理/隐藏面恢复仍未实现。
4. 澄清必须展示具体原因；来源closure拒绝时也要保留原始来源回执与部分产物索引，但不能信任坏引用。
5. 达到上述分支成功后再验证local miss→web完整成功和新来源copy-run；P2继续后置。
6. reconstruction-04暴露结构颜色接口缺口：SceneIR自由文本light brown不能被Pillow颜色解析。
   6310f22已让模型提供绑定surface_rgba，不建立light brown专用映射，也不丢弃请求颜色。
   reconstruction-05真实模型已保留light-brown wood-grain并给数值，266.782秒因前景light pink/pink
   属性匹配失败，未到compile。下一切片先诊断语义颜色匹配，不能直接放宽门或硬编码同义词。

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

# Canonical x2env 收敛、验收与 bingsheng 推送实施计划

- 日期：2026-09-13
- 状态：`accepted_matrix_v1_frozen`
- 批准：用户于 2026-09-13 明确批准开始实现；批准基线 `0a8abc01839f2c37a64b9c08f643f48985fb4a79`。
- 目标分支：`worktree/bingsheng`
- 规划来源：`codex/golden-diagnosis-20260911@3f7ec31bcc5839bec706892d438eb01a105aaf30`
- 决策输入：[Q01–Q64](CANONICAL_X2ENV_MERGE_DECISIONS.md)
- 计划完成边界：正常 push `origin/worktree/bingsheng` 后立即暂停；未经用户再次明确批准，不创建
  `worktree/bingsheng -> main` PR。

本文是可执行计划，不表示代码、12 例资格、Gujie 重建、制品上传或 push 已经完成。用户批准本文时，
`Qualification Matrix v1` 才冻结；在此之前只能修正文档歧义，不能把计划草案当成验收结果。

## 1. 交付结果

### 2026-09-13 执行补充（用户批准）

用户批准先推进替代重建后端，保留 Gujie 原接口供后续联调协商；原接口不成为第二套默认 workflow，
未取得许可/运行证据前不计验收通过。替代方案继续遵守固定来源、真实新 geometry、Codex 语义权威、
许可和 Genesis 门，不能以 primitive 替代。允许在计划范围内持续解决依赖与接线问题，不因单个
可解决阻断暂停整份计划；只有确实无法满足 push 条件时收口失败证据。矩阵、阈值与 main PR 授权边界不变。

最终只保留一条默认产品路径：

```text
X2EnvRequest(text?, images[0..8], video?, seed, source policy, constraints)
  -> Harness（唯一生命周期、状态、重试、恢复和 package 权威）
     -> managed CodexBackend.interpret
     -> shared SceneIntent / SceneIR
     -> asset.resolve
        -> managed CodexBackend asset-candidate visual assessment
        -> Yuxin local exact
        -> verified digital cousin（保留，当前可未验证）
        -> Yuxin licensed web
        -> Gujie reconstruction
     -> x2env.compile
     -> x2env.replay
     -> observe
     -> programmatic physical assessment + CodexBackend visual assessment/diagnosis
     -> x2env.validate
     -> [必要时 revise，合计最多 2 次]
     -> sim-ready Genesis environment package 或 failure evidence bundle
```

用户只接触：

- Python：`Harness.submit/status/resume/package`；
- CLI：`x2env submit|status|resume|package`；
- 一个 `result.json` 以及其中列出的本地和远端资产、场景、图片、视频、日志、报告、package 路径。

活跃 Registry 只公开三个业务 Skill：

1. `x2env.compile`；
2. `x2env.replay`；
3. `x2env.validate`。

`asset.resolve`、`observe`、`revise` 是 Harness 内部 Tool；数量不是合同，不为凑“3×3”增加空抽象。
这里的“活跃 Registry”指唯一 `CapabilityRegistry`；另有职责独立的唯一 `AssetRegistry`，不是第三套
执行 Registry。

## 2. 当前事实与术语收口

### 2.1 旧实验路径为什么不能原样合并

当前 `experiment.*` 是为了在旧 exact qualification 不允许 changed closure 时进行开发验证而建立的
临时 namespace，不是目标产品命名。实际调用链仍是：

```text
experiment_cli
  -> LimitedImagePreviewController
  -> ExperimentService / experiment_controller
  -> GoldenRunHarness / SkillRegistry
  -> model stage 或普通 stage
```

阶段列表和字符串分派至少在 `experiment_controller.py`、`experiment_service.py`、
`experiment_stage_handlers.py`、`experiment_model_stages.py` 重复。旧 limited-preview exact Skills 有
MCP 投影，而新实验 controller 直接调用 Registry handler；此前所谓“新实验 MCP 尚未接入”只说明两条
调用证据不一致，不表示 MCP 是新架构必需品。目标架构不再依赖 MCP，旧 MCP 路径退出 active tree。

旧“父 workflow”是 limited-preview controller 建立的用户生命周期容器，里面又创建 stage operation
或 child run。新实验只返回阶段 aggregate `passed`，没有把外层状态原子收口，所以会出现“步骤完成但
父 workflow 仍 active”。目标实现改成一个用户 workflow，所有阶段只是该 workflow 的 operation；
package 可读后才原子提交 `succeeded`，不再向用户暴露父/子两套生命周期。

### 2.2 “通用重建”的精确定义

通用重建是：对不在可复用库中的用户图像或视频，调用固定版本的 Gujie/SimFoundry 几何重建、
规范化和 Genesis import 能力，产生运行前不存在的新 geometry/scene digest。它不是：

- 从五种程序 primitive 中选一个；
- 把本地已有 mouse mesh 复制到新目录；
- 用 prompt 专门生成 cabinet；
- 只生成图片或 JSON 而未进入 Genesis。

Gujie 固定源 `/home/jingxiang/gujie/gen-env@eb0b710...` 的完整 `run()` 同时拥有自己的
checkpoint/workflow，并在语义阶段调用 Gemini。它不能整体套进 Harness 后冒充收敛。实施必须在固定
外部包中暴露“已验证媒体＋SceneIR -> reconstruction/import result”的窄 seam：规划和视觉判断由
Harness-managed Codex 完成，SimFoundry 只保留深度、分割、几何生成、纹理、物理化等重建算法。若
无法分离或所需权重/运行环境不可用，`Gujie reconstruction` 硬门为受阻，禁止 push。

### 2.3 “集成分支”的精确定义

`codex/golden-diagnosis-20260911` 是当前开发成果的审阅和整理工作树，不是最终 authority。
`worktree/bingsheng` 是唯一目标分支。所有开发分支都会进入冻结 manifest 并得到
`absorb / supersede / archive` 处理结论，但不会机械 merge 每个 branch tip。有效行为以小型功能提交
重新落到 bingsheng；重复或过时内容以可恢复 Git ref 和 provenance 留存。

## 3. 目标模块与依赖方向

目标活跃包为 `self_improving/harness/x2env/`。这是现有 Harness 的 canonical implementation，不是
第二套 Harness。

| 目标文件/目录 | 唯一职责 | 主要复用来源 |
|---|---|---|
| `contracts.py` | public/cross-boundary typed models；JSON Schema 与 API 表的唯一编辑源 | 现有 schemas、`x2env_contracts.py`、ingest contracts |
| `harness.py` | `submit/status/resume/package`、状态机、预算、幂等和 recovery | controller、Golden operation/journal 有效语义 |
| `codex_backend.py` | `interpret`、`assess_asset_candidates`、`assess_and_diagnose` 三个结构化 proposal seam | `codex_experiment_proposal.py`；移除 MCP relay/旧 VLM |
| `capabilities.py` | `CapabilityRegistry`、三个 Skill 和 Tool handler 的 typed 绑定 | 精简 `registry.py`、development execution |
| `assets.py` | `AssetResolver` 和 `AssetRegistry`；来源顺序、许可、不可变版本 | Yuxin provider/selection/webfetch/verify/Objaverse |
| `genesis.py` | 多实体/articulation compile、replay、observe、validate adapter | 现有 Genesis backend/execution/assessment |
| `store.py` | 单一 SQLite/CAS、workflow/operation 原子提交和 dead-owner recovery | `golden_store.py`、`run_store.py` 的必要事务 |
| `package.py` | 相对路径 package、loader、failure bundle、artifact publisher port | `portable_genesis_package.py` |
| `cli.py` | 薄 `x2env` 参数解析和人类/JSON 输出 | 仅复用 CLI I/O 习惯，不复用 orchestration |

依赖方向固定为：

```text
CLI/API -> Harness -> CodexBackend | CapabilityRegistry | Store
Harness -> AssetResolver -> YuxinProviderAdapter | GujieReconstructionAdapter
Harness -> x2env Skills -> GenesisAdapter
Harness -> PackageBuilder -> local CAS -> ArtifactPublisher
```

`CodexBackend` 不导入 Registry、SQLite、Genesis 或 provider；`assess_asset_candidates` 消费
Harness 提供的候选摘要/媒体并给出非权威 identity/attribute 建议；`assess_and_diagnose` 在每次 fresh
observation 后运行并同时返回 visual assessment 与可选修订建议，但不决定物理通过。Skill handler 不改变 workflow 终态；
外部 adapter 不拥有 Harness retry/checkpoint；CLI 不包含业务判断。

## 4. 类型、信任边界和生命周期

### 4.1 最小公共类型

- `X2EnvRequest`：`text: str | None`、`images: tuple[InputMedia, ...]`（最多 8）、
  `video: InputMedia | None`、`seed: int`、`allowed_sources`、`constraints`、
  `idempotency_key`、`output_dir`。
- `InputBundle`：原始字节 CAS refs、完整 image decode/video frame sequence、媒体摘要和用户覆盖规则。
- `SceneIntentProposal`：Codex 原始结构化建议、模型身份、输入引用、置信/未知项；不是已接受 SceneIR。
- `SceneIR`：最多 8 个命名实体，字段包括 category、color、dimensions、material、pose、
  articulation_state；关系为 on、inside、left_of、right_of、front_of、behind、near；每个关键字段带
  text/image/video provenance。table/worktop/counter 可以标 `role=structural_support`。
- `ResolvedAssetSet`：每实体的 immutable asset version、source、selection policy、license、
  representation、parent/version lineage 和每一候选的失败/跳过记录。
- `CompiledScene`、`ReplayResult`、`Observation`、`ValidationReport`、`ToolResult`、
  `EnvironmentPackageResult`、`FailureBundleResult`。
- `WorkflowHandle` 和 `WorkflowSnapshot`：只使用 active、succeeded、failed、blocked、cancelled。

Python typed models 是 active schema 唯一编辑源。`script/export_x2env_schemas.py` 只生成/check
`self_improving/harness/x2env/json_schemas/`、Registry descriptors 和 API 字段表；任何手工漂移使 CI
失败。breaking/additive/fix 分别升级 major/minor/patch，Registry 始终固定精确版本和 digest。

### 4.2 三个 Skill 与三个初始 Tool

| Capability | 输入 | 输出和边界 |
|---|---|---|
| `x2env.compile` | accepted SceneIR revision、ResolvedAssetSet、seed、Genesis profile | CompiledScene、静态诊断和 receipt；不授予物理通过 |
| `x2env.replay` | CompiledScene、runtime lock/profile、总 deadline | 轨迹、contact/joint trace、checkpoint、真实帧和 MP4；物理失败仍保留产物 |
| `x2env.validate` | 当前 compiled revision、replay、fresh observation、Codex visual advisory、固定 assertions | visual intent 与 physical profile 分列的报告；模型不能改阈值 |
| `asset.resolve` | 实体语义/尺寸/材质/关节要求、sources、exact/cousin policy、seed、媒体 refs | ResolvedAssetSet、候选 provenance、miss/failure/blocked、不可变新版本 |
| `observe` | 当前 replay/checkpoint、scene revision | TTL 有界 fresh observation、选帧、状态摘要和媒体 refs |
| `revise` | ScenePatch / AssetPatch / NumericsPatch、base revision、failure 和 diagnosis refs | 新 scene revision、child asset version 或白名单数值 revision |

### 4.3 只在权威边界校验

强校验只发生于：

1. 用户和媒体输入；
2. Codex proposal；
3. 外部资产下载/重建导入；
4. 跨进程结果；
5. SQLite/CAS 持久化或恢复；
6. package 和 qualification。

已校验的 typed object 在进程内传递时彼此信任，不在每层重复 schema/hash/qualification。CAS 写入时
计算一次 digest，读取跨信任边界时按 trust epoch/receipt 验证；修掉当前 `resolve()` 与
`resolve_digest()` 的重复 full hash，但不以 path/mtime 作为永久信任。

### 4.4 状态机

```text
persist active workflow
 -> ingest
 -> Codex interpret
 -> accept SceneIR | blocked(clarification)
 -> asset.resolve
 -> compile -> replay -> observe + programmatic physical assessment
 -> Codex visual assessment/diagnosis -> validate
 -> pass -> materialize/read package -> atomic succeeded
 -> repairable failure -> revise -> compile ... (max 2)
 -> deterministic exhausted -> failed
 -> missing external resource/Codex -> blocked
 -> explicit user cancel/deadline -> cancelled
```

`timed_out` 是 `stop_reason/error_code`，不是第六个状态。每次完整 case 在 1770 秒向准确 owner 发
SIGINT并预留 30 秒完成原子日志和状态收口；终态为 `cancelled + timed_out`。相同
`error code + SceneIR/asset versions + evidence digest` 再现时立即停止。provider 每种来源最多尝试一次，
不占合计两次的 revise 预算。

`succeeded` 只在最后 revision 的 replay/validate 通过且 package 已物化、可读、manifest 自洽后提交。
operation 是 workflow 内部日志，不再创建用户可见 child workflow。明确用户取消和 deadline timeout
提交 `cancelled`，不可自动恢复；意外死亡的 child worker 不等同用户取消，workflow 保持 `active` 且
operation 标 `recoverable_dead_owner`，由下一次 `resume` 检查 owner identity 后接续或收口。禁止编辑
SQLite、重复 submit 或跳过未完成 operation。

### 4.5 Development 与 qualified execution identity

同一 Harness 和同一 controller 只支持由部署配置选择的两种身份，不另建 development wrapper：

- `development_candidate`：C04–C14 的 TDD/smoke 使用；明确记录 Git/source/config identity、
  `qualified=false` 和省略门，产物写隔离 namespace，不能 publish 或被默认用户部署加载；
- `qualified`：启动时一次验证 final HEAD、CapabilityRegistry/schema/code closure 和外置 qualification
  report；任何 drift fail closed。最终发布的默认 deployment 只指向该 snapshot。

C15 的 qualifier 在 clean H 上以 `development_candidate` 身份真实执行，并在所有门通过后签发
`qualified` snapshot；之后默认 deployment 重开并以 `qualified` 身份做一次 bounded acceptance。mode
只能来自 operator-owned deployment config，用户 prompt、Codex proposal 或 Skill 输入都不能切换。
在签发前，candidate artifacts 禁止发布；final qualification manifest 显式接纳并绑定这些 artifact
digests 后，ArtifactPublisher 才可把它们作为 `qualification evidence` 上传。原 execution
receipt/identity 仍诚实保持 `development_candidate`，不得回填成 qualified；默认用户 deployment 只
消费新签 snapshot，并把这次接纳记录作为证据而不是改写历史执行。

## 5. 资产来源与版本策略

固定选择顺序：

1. Yuxin local exact；
2. verified digital cousin；
3. Yuxin licensed web；
4. Gujie reconstruction。

`allowed_sources` 可以缩小范围，不能改变许可或完整性门。exact/cousin 是选择策略，
local/web/reconstruction 是获取来源，二者不混成一个枚举。

table、worktop、counter 和隐式 ground 是 compile-owned `structural_support` geometry：按 SceneIR 明确
尺寸生成有限碰撞平面/盒体并记录 `source=harness_structural_geometry` provenance，不进入 foreground
asset provider 顺序。`local_only/web_only/reconstruction_only` 约束和三来源成功统计只作用于用户请求的
foreground assets。该例外不适用于 cabinet、basket、plate 或其他语义对象，也不授权类别专用 generator。

### 5.1 Yuxin

`providers.json`、`a1_providers.py`、`a2_selection.py`、`a3_webfetch.py`、`a4_coverage.py`、
`a5_visual.py`、`a6_verify.py`、`a7_objaverse.py` 和 ledger 成为唯一 provider 内核。实施时用一次
`git mv` 将数字目录整理为可安装包并改成相对 import；旧路径不留复制。Harness 只实现 typed adapter，
不得再写一套 Khronos/Poly Haven/Objaverse 搜索，也不得通过 heavyweight script/subprocess 调用。

`a6_verify.py` 当前默认可加载 Qwen2.5-VL；这条默认推理路径必须退出 canonical runtime。Yuxin 的
CLIP embedding 可以作为非权威候选排序算法保留，但最终 candidate identity/attribute visual assessment
必须通过其现有 injectable infer seam 绑定 `CodexBackend.assess_asset_candidates`，并由 Harness 校验
ToolResult。不得在 Codex 之外保留第二套 Qwen/Gemini VLM。

adapter 在调用边界强制 license policy。首批只接受：

- `CC0-1.0`；
- `CC-BY-4.0`，并把 author、source page、license notice 写入 Registry/package/manifest。

unknown、NC、ND、SA、自定义和 NVIDIA terms 本阶段拒绝并继续下一候选/来源。`providers.json` 当前
`license_gate=false` 必须在 canonical 配置中改为 true；不能靠调用方忘记传 flag。

### 5.2 Gujie

固定来源和所有权：

- source：`/home/jingxiang/gujie/gen-env@eb0b710581fd7794bc01b447b0f77cb871c8a711`；
- Genesis gitlink：`0e74bf392781884ccad765c3f344419c86b872ca`；
- SimFoundry gitlink：`9e34ebefcd020583fbb755a8b57268dce78eca26`。

只读取 committed bytes。若需要新增 reconstruction-only API，应在 Gujie 外部仓库形成独立、可审阅提交
并 pin 新 SHA；本仓只加 adapter 和 provenance，不复制实现。adapter 接收已接受的 SceneIR/Codex 结果，
调用真实重建/导入过程，记录 command、版本、模型/权重 identity、stdout/stderr、输入输出 digest 和
license；不让 Gujie 的旧 TaskOutput/checkpoint/Gemini 重新成为第二套 controller/VLM。

真正通过的判据是：run 前 AssetRegistry 不存在该 geometry digest；固定实现真实执行；新 asset version
完成 normalize/import；其场景通过 Genesis replay/validate。缺权重、凭据、服务、可再分发许可或
无法剥离旧 Gemini 语义控制均为 `blocked_external_resource`，直接阻止 push。

### 5.3 不可变修订

- visual patch 白名单：base_color、roughness、metallic；
- physical patch 白名单：uniform_scale、mass/density、friction、restitution；
- pose、yaw、joint state 是 SceneRevision；
- topology、collision、URDF structure、joint 修改进入 Gujie reconstruction/normalization。

任何 asset patch 创建 child version，记录 `parent_version`、geometry provenance 和 attribute provenance，
绝不原地修改已登记资产。

## 6. 分支、worktree 与 dirty bytes 收敛

### 6.1 已知基线

以下是计划生成时的只读快照，实施第一步必须在 `git fetch --prune origin` 后重做，不能把缓存的远端
状态当最新事实：

- `worktree/bingsheng@ea26524cae7b4a18b5576150376eb6ec1ddddf7b`，相对缓存的
  `origin/worktree/bingsheng` ahead 152，且有用户改动；
- 当前集成 `codex/golden-diagnosis-20260911@3f7ec31...`，相对 bingsheng 为 `0:357`；
- 86 个 local branches、6 个 origin refs（含 symbolic HEAD）、87 个 worktrees；
- 79 个 clean、7 个 dirty、1 个 prunable；
- 集成差异约 586 files、155,187 insertions、1,747 deletions，不能视为一个可审阅 feature；
- Gujie repo 固定 HEAD 正确但工作树很 dirty，只认 committed bytes；
- `origin/worktree/yeyuxuan` 有两个尚未 patch-equivalent 的资产提交
  `1440d59`（tomato soup can）和 `c58c35d`（gelatin box），只在来源/许可/体积审计通过后按资产版本
  导入，不 merge 整个 branch。

### 6.2 第一步：冻结 manifest

创建
`self_improving/golden_e2e_progress/branch-manifests/<UTC>-pre-consolidation.json`，至少记录：

- capture time、repo identity、远端 observed time；
- ref/commit/tree/upstream、worktree path、detached/prunable；
- staged/unstaged/untracked 的 path、size、SHA-256；submodule gitlink、当前 HEAD 和 dirty manifest；
- 相对 bingsheng/integration 的 merge-base、ahead/behind、`git cherry` patch equivalence；
- source author、upstream owner、integration owner 分列；
- `absorb / supersede / archive`、目标 feature commit、验证依据。

冻结后新出现的 branch 不自动进入范围。dashboard 三个 JSON 若仍 HTTP 404，只记录 blocker，不伪造
task id 或状态。

### 6.3 第二步：无损保护 bingsheng

在改变用户工作树之前：

1. 对普通文件分别保存 index diff、worktree diff、untracked manifest 和 byte digests；
2. 用 alternate index/isolated worktree 生成本地
   `refs/archive/x2env-premerge/<timestamp>/bingsheng` snapshot，不切换用户工作树；
3. `external/OpenReal2Sim` 的 untracked/dirty bytes 先在子模块自身生成 local archive snapshot，再让
   主仓 snapshot 记录 gitlink 与子模块 snapshot commit；
4. 扫描 secret 和异常大文件；正常项目 bytes 进入 Git snapshot。若疑似 secret、凭据或违反仓库
   bulk-data 规则，停止该项并请求用户处理，不能为了 Q62 把秘密提交进 Git；
5. 在临时 worktree checkout snapshot，逐文件复算 digest，证明可恢复；
6. 分类为 canonical、独立用户提交、重复/过时。第三类已有可恢复 ref 后直接从 canonical 排除；
   不再为每项暂停，但 archive ref 在最终 push 完成前不得删除。

用户主目录当前列出的 `demo/app.py`、`repo-docs/*`、`tests/demo/test_app.py`、研究/证据文件等必须逐项
归类；不允许用集成分支同名文件覆盖。独立且有价值的用户改动先在 bingsheng 形成自己的提交，不能
混入 x2env feature。

### 6.4 第三步：逐功能吸收

基于“已保护、已提交、工作树 clean”的 bingsheng 新建
`codex/canonical-x2env-consolidation-<date>`。对 manifest 中每个 ref：

- 已是祖先或 patch-equivalent：记录 `absorbed`，不重放；
- 唯一且符合目标架构：从固定 commit 读取 diff，重写/吸收到对应小型 feature commit；
- 旧 preview/MCP/planner/VLM/qualification：记录 `superseded` 或 `archived`，只提取仍唯一的
  Store/Genesis/ingest/测试语义；
- 不确定资产：先完成 source/license/hash/size 审计，再决定登记或 archive；
- 所有来源吸收提交同时更新 `docs/integration-provenance/LEDGER.md`。

禁止 octopus merge、force push、直接 merge 355/357 个历史提交或删除旧 refs 来制造“整洁”。

## 7. 活跃代码去留

### 7.1 保留并演化

- `scene_gen/` 与 `/gen-env`：仍是独立稳定 RoboTwin/SAPIEN 兼容模块，不成为 canonical runtime；
- `image_ingest.py`、`video_ingest.py`：复用完整解码和证据合同；
- Genesis execution/backend/assessment 的真实运行、contact/stability 和媒体逻辑；
- `portable_genesis_package.py` 的相对引用/隔离 runner 思路；
- Golden Store/CAS 的事务、operation、幂等和恢复语义；
- Yuxin provider/selection/webfetch/verify/Objaverse；
- Gujie 固定 reconstruction/import/convert/verify 算法。

### 7.2 收敛重写

- `experiment_inputs.py`、`experiment_model_stages.py`、`experiment_scene_compile.py`：进入共享
  multi-entity SceneIR 路径；
- `experiment_controller.py`、`experiment_service.py`、`experiment_stage_handlers.py`、
  `experiment_runtime.py`、`experiment_package.py`：职责进入唯一 `Harness`/typed capability map；
- `codex_backend.py` 与 `codex_experiment_proposal.py`：合成不依赖 MCP 的 managed Codex backend；
- `registry.py`、`golden_execution.py`、`development_execution.py`：收敛为一次 workflow
  capability snapshot；
- `golden_store.py`、`run_store.py`：收敛为 `state/harness.sqlite` 和 `state/cas/`；
- 单对象 Genesis contracts/backend/trace/evaluator/loader：扩展为实体图、支撑/容器图和 articulation。

### 7.3 退出 active tree

完成 consumer/call-graph 迁移后，删除而不复制到 `archive/`：

- `limited_image_preview_*` 活跃入口和旧 deployment；
- `experiment.*` descriptors/schema/用户入口；
- `mcp_*`、`codex_mcp_*`、MCP Registry projection；
- 旧 planner、独立 VLM、rule-based intelligent fallback；
- 已漂移的旧 qualification snapshot/reader/writer 和只为其服务的 active CI；
- `script/qualify_text2env_validate.py`、
  `script/run_golden_compile_replay_validate.py`、
  `script/run_golden_mcp_compile_replay_validate.py`；
- 其他 canonical call graph 无消费者且被替代的重复 orchestration。

删除前必须完成“deletion test”：新公共 API、12-case runner、CI、文档和 package loader 均不 import/
invoke 这些文件；`rg` 和 import graph 只允许历史 Markdown/provenance 提及。稳定 `scene_gen`、审计工具和
仍有独立消费者的 demo 不按文件名前缀误删。

## 8. TDD 与逐功能提交序列

每个实现切片遵守：先提交/展示 public seam 的 failing test，再写最小实现使其通过，再在同一职责内
refactor。测试只调用公开 API 或明确外部 adapter seam，不 patch 私有分派函数。每个 commit 必须
`git diff --check`，相关快测全绿，provenance/docs 与行为同步。

### C00 — 计划与安全边界

建议提交：`docs(x2env): freeze canonical consolidation plan`

- 本文、Q01–Q64、README/TODO/DECISIONS；
- 不含功能代码或 runtime 结果；
- 用户批准后把状态改为 `accepted_matrix_v1_frozen`。

### C01 — branch manifest 与 dirty snapshot

建议提交：`chore(git): record canonical branch intake manifest`

- fetch 后冻结所有 refs/worktrees；
- 建立并验证 archive refs；
- 将 bingsheng 用户工作分成独立提交；
- canonical branch 只提交 manifest/provenance 指针，不提交 bulk diff archive。

门：任何 unsnapshotted dirty byte、未知 submodule HEAD、secret 或远端漂移都会停止后续整合。

### C02 — 权威 contracts 和 acceptance

建议提交：`docs(x2env): replace legacy workflow authority`

- 重写当前互相冲突的 `CONTEXT.md`、`SEAMS.md`、`ACCEPTANCE.md`、`SKILL_CONTRACTS.md`；
- 固定本文 SceneIR、三 Skills/Tools、五状态、错误码、两次 revision、30 分钟 budget；
- 添加 matrix v1 machine-readable spec 和 prompt/media digests；
- 明确 old MCP/qualification/robot policy 不在 active scope。

测试：文档链接、状态/schema vocabulary、matrix 12 条唯一 ID、4+4+4、固定 digest 和无 legacy active
namespace 的 contract tests。

### C03 — canonical typed models、生成 schema 和 Registry

建议提交：`feat(x2env): define canonical contracts and capability registry`

- 新建 `self_improving/harness/x2env/contracts.py`、`capabilities.py`；
- 只注册三个 Skill 和实际 Tool；
- 生成 JSON Schema、descriptor 和 API 字段表；
- SemVer/digest/check 命令。

RED：非法 9th entity、未知关系、重复 ID、缺 provenance、legacy namespace、错误 handler 版本。
GREEN：边界一次校验，进程内 typed handoff 不再重复解析。

### C04 — 单 Store、状态机和 public API

建议提交：`feat(x2env): add durable harness lifecycle`

- `state/harness.sqlite`、`state/cas/`；
- `Harness.submit/status/resume/package`；
- 调模型前持久 handle；typed operation；原子 terminal；
- 同 idempotency key 同 workflow/result；
- dead-owner recovery 和 clarification input revision。

RED：启动后中断、running owner 已死、重复 submit、错误 base revision、package 未物化却 succeeded。
GREEN：单 workflow operation history 可恢复；旧数据库无 reader/新写。

### C05 — 多模态 ingest 与 managed Codex SceneIR

建议提交：`feat(x2env): route multimodal input through managed codex`

- 复用 image/video 完整 ingest；
- Codex `interpret` 和 `assess_and_diagnose` 只返回 proposal；
- Yuxin candidate visual verify 通过 `assess_asset_candidates` 的 injected port 使用同一 managed Codex；
- controller 校验并接受 SceneIR；
- 字段级 text/image/video provenance 和 conflict clarification；
- Codex 故障使用本地化确定性错误模板。

RED：柜子 prompt 被压成一个对象、粉红/`open`/on relation 丢失；媒体顺序不完整；冲突被静默覆盖；
Codex 试图返回 provider/Skill success。GREEN：proposal 保留所有已支持信息，critical unknown 为 blocked。

### C06 — Yuxin local/web 与 AssetRegistry

建议提交：`feat(x2env): connect yuxin asset resolution`

- 以 `git mv` 整理唯一 provider package 和 relative imports；
- typed `asset.resolve`；
- local exact、verified cousin slot、licensed web tier walk；
- 用既有 infer seam 将 asset candidate visual verify 接到 managed Codex，禁用 default Qwen VLM；
- CC0/CC-BY policy、attribution、download/convert/verify；
- immutable asset versions 和 source failure receipts；
- 删除 `experiment_assets._web()` 与重复 primitive path。

RED：basename/category 假匹配、unknown license、local representation 坏引用、颜色原地修改、同候选重复
请求。GREEN：S01 local 和 S04 web 的 adapter-level tests；一次 local miss→web 真实小冒烟。

### C07 — Gujie reconstruction-only adapter

建议提交：`feat(x2env): connect pinned gujie reconstruction`

- 固定外部 source/runtime；
- 若必要，先在 Gujie repo 独立提交 reconstruction-only seam 并更新 pin；
- 移除 semantic Gemini/旧 TaskOutput ownership，接收 Harness 已验证 SceneIR；
- 重建、import、normalize、AssetRegistry 登记和完整日志；
- secrets 仅环境注入并做 redaction。

RED：调用旧 `run()`、复用 run 前已有 geometry、固定 primitive 冒充、新输出未 Genesis import、缺资源
仍返回 pass。GREEN：在一次小型真实 M04 smoke 中产生全新 digest；若真实依赖缺失则本提交测试可绿，
但总体 push 明确 blocked，不能用 adapter test 抵销。

### C08 — multi-entity Genesis compile

建议提交：`feat(x2env): compile entity and support graphs for genesis`

- SceneIR 实体/关系/坐标求解；
- 每实体 representation、collision、material、pose；
- table/worktop/counter、on/inside 和相对位置；
- 通用 articulated URDF/joint/limits/initial state；
- 编译 closure 和静态检查不再固定 `object/table/ground`。

RED：重复类别合并、支撑目标错误、完整 footprint 越界、container 用外 AABB 假 containment、柜门用
视觉姿态冒充 joint。GREEN：先用小 fixture backend，再运行 S01/S02 的小型真实 compile/load。

### C09 — replay、fresh observe 和 physical validate

建议提交：`feat(x2env): replay and validate multi-entity genesis scenes`

- 请求 seed 贯穿 Genesis；显式 build/load/reset/step；
- baseline/half-dt 连续轨迹、动态 contact owners、joint trace；
- fresh observation TTL；
- visual intent 与 physical profile 分账；
- 多对象 support/containment/contact/penetration/stability，articulation 成功例增加 joint checks；
- 至少一图和一段连续 MP4，记录 total/unique frame。

RED：固定 seed=0、无 reset、只看首末帧、静态 actor 假支撑、contact-free stacking、旧 observation、
Codex 修改 threshold。GREEN：S01–S03 每条通过真实 `compile/replay/validate` smoke。

### C10 — 有界 A/B/numerics revise 与恢复

建议提交：`feat(x2env): close bounded diagnosis and revision loops`

- A：ScenePatch 改 pose/yaw/joint state 后重新 compile；
- B：AssetPatch 产生 child asset version 后重解依赖；
- NumericsPatch 独立白名单，不能调成功阈值；
- 合计 2 次 revision、每 revision 一次 replay、重复 fingerprint 立即停；
- owner interrupt/resume、clarification resume。

RED：只改 receipt 文本、改原 asset、scene/asset 各自获得 2 次预算、相同错误无限循环。GREEN：
补充资格用例真实证明 scene bytes/pose 改变、child asset digest 改变、budget stop 和同 handle resume。

### C11 — relocatable package 和 artifact publisher

建议提交：`feat(x2env): package relocatable genesis environments`

- 相对路径 scene/assets、`run.py`、`runtime.lock.json`、install/check 命令；
- success package 与 failure evidence bundle 分流；
- local CAS materialization；
- ArtifactPublisher port，默认 GitHub prerelease，保留 S3-compatible seam；
- no-clobber、digest filename、远端 download/hash verify。

RED：任何原 workspace/CAS/asset absolute path、少 member、runtime mismatch 仍启动、失败包被标
`sim-ready`、release asset 覆盖同名旧 bytes。GREEN：package static closure 与隔离 runner tests。

### C12 — 唯一 `x2env` CLI

建议提交：`feat(cli): expose one x2env entrypoint`

- pyproject 只增加/保留 `x2env = ...x2env.cli:main` 作为 canonical 用户入口；
- `submit|status|resume|package` 全部调用同一 Harness；
- stdout 人类摘要，`--output/result.json` 机器结果；
- 明确列出 workflow、SceneIR、assets、images、videos、logs、environment/failure bundle 和远端 URI；
- 业务错误非 traceback-first，保留稳定 exit code。

测试：CLI/Python API 同 workflow；缺 Codex、license reject、timeout 和 clarification 均有 agent 友好解释；
旧用户 CLI 不再出现在 active docs/entrypoints。

### C13 — 删除 legacy active graph 和重划 CI

建议提交：`refactor(x2env): remove superseded orchestration`

- 删除 `limited-preview`、`experiment.*`、MCP、旧 planner/VLM、旧 qualification active consumers；
- 删除/下线三个 heavyweight CLI；
- 去除 pyproject `mcp` 默认 extra（仅当无 retained 非canonical consumer 需要）；
- CI 改为 canonical core 100% statement+branch、retained modules 自身门和外部 adapter 集成门；
- 不修、不重签旧 qualification 红测。

门：import graph、entry points、schema snapshot、docs、`rg` 均证明只有一个 canonical Harness/CLI/Registry。

### C14 — 用户、API、接入与运维文档

建议提交：`docs(x2env): publish canonical usage and integration guide`

- quickstart、四种输入命令、Python API；
- 生成的 API/schema 表；
- Skills/Tools/lifecycle/error/recovery；
- Yuxin/Gujie provider 和外部依赖；
- package copy/load、runtime repair；
- testing/qualification/evidence discovery；
- migration（旧 workflow 不可恢复）；
- 逐功能 provenance、完整 walkthrough 和 roadmap；
- 根 `AGENTS.md` 增加本文、进度目录、walkthrough 的精确链接。

### C15 — code freeze

建议提交：`chore(x2env): freeze qualification candidate`

- 只包含实现完成前已知的最终小修、文档模板、case manifest 和 qualification runner；
- working tree clean，记录 HEAD、tree、schema/capability/code closure digests；
- 之后不得提交测量结果或“顺手修复”。任何源码/schema/fixture/threshold 变化产生新 HEAD，旧资格作废。

最终真实 qualification 在 C15 的确切 HEAD 上运行；完整报告只进入对应 evidence prerelease，因此没有
“报告必须包含自身 commit、提交报告又改变 commit”的自引用。Git 中的 RESULTS 可记录冻结前开发
事实；最终报告真实记录 `execution_git_sha=C15_HEAD`，不得把旧 SHA 的运行说成 C15。

## 9. Qualification Matrix v1

本节就是 Q63 要求的冻结内容。用户批准本文后，ID、难度、prompt、media bytes、seed、source policy
和断言全部冻结。首次 qualification 开始后不能因失败改 prompt、seed、难度或 assertion；只有固定输入
bytes 损坏这类非业务错误才可建立 v2，且 v1 原样保留、v2 全量重跑。

### 9.1 固定输入媒体

媒体进入受限的 qualification input pack 和 evidence prerelease，不因现存绝对路径获得身份。现存路径
只用于首次按 digest staging；后两份是用户/Gujie 工作目录的输入媒体，不是 Gujie committed code 或
重建成功证据。

| Logical path | 首次 staging 来源 | Bytes | SHA-256 |
|---|---|---:|---|
| `inputs/centered-red-cube.png` | `/home/jingxiang/bingsheng/generic-experiment-v2-20260913.1QrGYD/inputs/centered-red-cube.png` | 30,643 | `a4c62f05001d759a43aa0bf0964bde7e7ee5df08ae546e21d6ba2e182ef27202` |
| `inputs/centered-red-cube.mp4` | 同目录同名 | 14,899 | `7400aa21cb455c6efb142d96d2f1632d5329cf759aeb01ba498ed39e21dc5df8` |
| `inputs/mouse-and-pen.jpg` | `/home/jingxiang/gujie/鼠标和笔.jpg` | 134,727 | `044e68562224788ad4e395d9c7cae49c148eb509a15f9ff11eb4aa39bc467d48` |
| `inputs/mouse-video.mp4` | `/home/jingxiang/gujie/鼠标视频.mp4` | 393,865 | `5c7aa9c809aaff9c5f4d3076502d99cba0f72e46754102184b9c5c9db8d88634` |

旧 `/home/jingxiang/gujie/鼠标.jpg` 当前不存在，禁止用旧 hash 假装输入已准备。input pack staging 时还要
记录媒体 MIME、尺寸/帧序列和授权范围；若仓库访问控制不允许上传用户媒体，qualification 在上传前
blocked，而不是公开泄露。

### 9.2 所有成功例的共同断言 G

一个 scored case 只有同时满足以下各项才计 1 分：

1. 真实 managed Codex 生成结构化 proposal；输入、模型版本、seed、关键字段 provenance 完整，没有
   静默丢实体、属性或关系；
2. canonical controller 实际调用 `asset.resolve`、`x2env.compile`、`x2env.replay`、`observe`、
   `x2env.validate`，并在 package 可读后把同一 workflow 原子提交 `succeeded`；
3. Genesis 实际 build/load/reset/step；请求 seed 进入 layout/reconstruction/simulator；所有 foreground
   动态实体碰撞开启；
4. baseline/half-dt 都真实连续运行，程序化重算 trajectory、contact、penetration、support、
   containment、stability 和终态一致性；阈值在执行前由合同冻结，不能按 case 结果放宽；
5. fresh observation 与当前 SceneIR/replay revision 绑定；Codex visual intent 与 physical profile 分列，
   两者均通过；
6. 至少一张真实图片和一段由连续帧编码的 MP4；记录 total/unique frames 和时间戳，静态场景不要求
   人工制造物体运动，但不能用一张图循环伪造；
7. environment package 含完整资产闭包、相对引用、loader、`runtime.lock.json`、来源/版本/报告，
   `robot_policy_evaluated=false`、`data_collection_evaluated=false`。

`blocked`、`failed`、`cancelled` 或 `timed_out` 都是 0 分。没有“预期失败也算测试通过”的 scored case；
允许失败只表示不必达到 12/12，failure bundle 仍必须完整。

### 9.2.1 Physics Assertions v1（随计划批准冻结）

单刚体基础继承当前
`self_improving/harness/GENESIS_SCENE_REPLAY_CONTRACT.md`，计划生成时 SHA-256 为
`7064a6425ec8de03ca3fb4d1304d7db60132157741160af11a426c513606de5c`。C02 必须把以下规则逐字转成
canonical multi-entity contract 和 machine-readable profile；实施者没有临近 qualification 再选阈值的
自由：

- baseline：dt=0.004、1000 steps；half-dt：dt=0.002、2000 steps；均为 4 秒、零初速、独立 replay；
- 每个动态 foreground 的末 0.5 秒：translation ≤0.001m、rotation ≤0.5°、drift ≤0.002m/s、
  rotation rate ≤1°/s、excursion ≤0.001m；
- sustained effective speed `max(|v|, radius*|w|) >= 1.5*g*dt` 的连续行数 <5；
- 每条声明的 `on` 支撑关系：末段 contact dropout ≤0.05，接触样本中 target 对 source 的有效向上力
  >1e-6N 的比例 ≥0.8；
- 全轨迹 penetration ≤0.001m；foreground 最低顶点 ≥-0.001m；禁止 ground contact 替代声明支撑；
- `on` 的完整旋转后 visual + collision footprint 在 target-local 支撑面内 margin ≥0.02m；
- `inside` 使用已测 inner-cavity geometry，不用 outer AABB：source 完整 footprint 在 cavity 内的
  horizontal clearance ≥0.005m，最低点距内部支撑面 ≤0.003m，且满足同样 contact/support/penetration 门；
- 多支撑实体对每个声明 target 的末段有效 contact fraction ≥0.5，并同时满足整体 stability 和
  penetration 门；
- baseline/half-dt 之间每个 foreground 最终 position ≤0.01m、rotation ≤5°；
- articulation：joint 类型、axis、finite lower/upper limits 与 closed reference 必须来自 asset version。
  对 `open` revolute joint，limit span ≥30°，初始位置距离 closed ≥max(15°, opening span 的20%)，且位于
  opening span 的20%–80%；4 秒内 joint drift ≤2°、末段速度 ≤1°/s、全程不越 limit 0.5°；
- case-specific 位置/角度/颜色/关系断言按 S01–H04 额外叠加，不能替换上述公共门。

若资产不能提供可信 inner cavity、joint limits 或 closed reference，对应 case 失败
`missing_physical_metadata`，不得估一个宽松阈值。C02 只能把这些已批准数字结构化，任何数值变化都要
形成 matrix/profile v2 并重新取得用户批准，不能在 v1 qualification 中修改。

### 9.3 Simple：必须 4/4

#### S01 — text-only / local exact / seed 11

Prompt：

> 在长90厘米、宽70厘米的桌面上放一个粉红色鼠标。以桌面中心为原点，鼠标中心位于x=10厘米、
> y=-8厘米，绕桌面z轴旋转30度。

- 输入：text only；
- source policy：`local_only`，真实调用 Yuxin exact retrieval；
- 额外断言：mouse 1 + table 1；pink 绑定 mouse；table-local
  `x=0.10±0.01m, y=-0.08±0.01m, yaw=30±1°`；mouse 与 table 真实接触且稳定；
- 若 pink 需要 material revise：生成 child asset version，parent bytes 不变。

#### S02 — image-only / local exact / seed 23

- Prompt：空；
- 输入：`inputs/centered-red-cube.png`；
- source policy：`local_only`；
- 额外断言：block 1 + table 1；red；桌面归一化中心偏差 x/y 各 ≤0.05；不得用隐藏文字 proposal
  代替真实图像理解。

#### S03 — video-only / local exact / seed 37

- Prompt：空；
- 输入：`inputs/centered-red-cube.mp4`；
- source policy：`local_only`；
- 额外断言：block 1 + table 1；red；中心关系；输入 41 帧必须完整、有序解码并有 sequence digest；
  目标是最后静态场景，不声称恢复输入视频中的动作。

#### S04 — image+text / licensed web / seed 41

Prompt：

> 忽略参考图的颜色、尺寸和位置，只保留方块类别。生成一个蓝色、边长8厘米的方块，放在长80厘米、
> 宽60厘米工作台顶面的局部坐标x=-15厘米、y=10厘米，绕z轴45度。

- 输入：上述 text + `inputs/centered-red-cube.png`；
- source policy：`web_only`，只能经 Yuxin provider engine 下载，不允许 local/generated 偷跑；
- 额外断言：blue cube 1 + worktop 1；边长 `0.08±0.002m`；
  `x=-0.15±0.01m, y=0.10±0.01m, yaw=45±1°`；每个被文本覆盖的 image 字段均有 provenance；
  receipt 含 CC0/CC-BY、source URL、下载 bytes/hash 和 attribution（如需）。

### 9.4 Medium：允许失败，计入总分

#### M01 — open cabinet + pink mouse / text-only / seed 101

Prompt 原样固定：

> 在桌上生成一个打开的柜子，柜子上放着粉红色的鼠标。

- source policy：`local_then_cousin_then_web_then_reconstruction`；
- 额外断言：cabinet + mouse + table；pink 只绑定 mouse；mouse on cabinet、cabinet on table；
  cabinet door 是真实 joint，有限位且初始角非零并合法；
- 不允许 cabinet-specific generator。失败按 intent、asset resolution、reconstruction、articulation、
  compile、replay 或 validate 精确归因并生成 failure bundle；它不是独立 hard gate。

#### M02 — basket contains can / text-only / seed 103

Prompt：

> 在厨房操作台上放一个绿色购物篮，篮子里面竖直放一个银色易拉罐；篮子中心位于操作台中心左侧15厘米。

- source policy：默认固定顺序；
- 额外断言：basket + can + counter；green/silver 不串对象；can inside basket 使用真实内腔、底面接触，
  不能用 outer AABB overlap；完整 footprint 不越界；basket local `x=-0.15±0.01m`。

#### M03 — can on plate + mouse / text-only / seed 107

Prompt：

> 在桌面中央放一个白色盘子，盘子中央竖直放一个红色易拉罐；在盘子右侧20厘米处放一个蓝色鼠标。

- source policy：默认固定顺序；
- 额外断言：plate + can + mouse + table；can on plate on table，mouse on table；红/白/蓝绑定正确；
  mouse 与 plate 中心 x 差 `0.20±0.01m`；不得让 can 穿 plate 后直接接触 table。

#### M04 — Gujie new reconstruction / image+text / seed 109

Prompt：

> 只重建图片中的粉红色鼠标，忽略笔。保持鼠标外形与颜色，将鼠标放在水平桌面中心。

- 输入：上述 text + `inputs/mouse-and-pen.jpg`；
- source policy：`reconstruction_only`，provider 必须是固定 Gujie/SimFoundry seam；
- 额外断言：run 前 AssetRegistry 中不存在输出 geometry digest；不能导入旧 mouse archive 或 procedural
  primitive；mouse 1 + table 1，pen 明确被排除，pink；满足共同 G；
- 这是 Gujie real reconstruction 的跨维度 push 硬门：即使总分达到 8，M04 失败且没有其他真实
  Gujie 成功 case 时仍禁止 push。

### 9.5 Hard：允许失败，计入总分

#### H01 — basket, two cans, plate / text-only / seed 211

Prompt：

> 在厨房操作台上放一个蓝色购物篮，篮中并排放两个竖直易拉罐，红罐在银罐左侧10厘米；
> 篮子右侧25厘米放一个白色盘子。

- source policy：默认固定顺序；
- 额外断言：basket + 2 cans + plate + counter；两个 can inside basket；red/silver ID 不串；
  can 中心 x 差 `0.10±0.01m`，plate/basket 中心 x 差 `0.25±0.01m`；多接触、非穿透、稳定。

#### H02 — eight-entity absolute layout / text-only / seed 223

Prompt：

> 在长120厘米、宽80厘米的桌面上放一个购物篮、两个盘子、两个易拉罐和两个鼠标。篮子中心在
> (-35,0)厘米；白盘在(0,15)厘米，蓝盘在(30,15)厘米；红罐在白盘中央，银罐在蓝盘中央；
> 粉红鼠标在(0,-20)厘米，黑鼠标在(30,-20)厘米。

- source policy：默认固定顺序；
- 额外断言：7 foreground + table = SceneIR 上限 8；重复类别各自保留 ID；颜色、位置、两个
  can-on-plate 支撑链正确；所有坐标 `±0.01m`；全图物理 G。

#### H03 — video shape + explicit layout / video+text / seed 227

Prompt：

> 使用视频中的鼠标外形，以最后一帧为参考；忽略视频中的桌面位置。把鼠标放在桌面局部坐标
> (-15,10)厘米，在它右侧25厘米放一个橙色盘子，并在盘子中央竖直放一个银色易拉罐。

- 输入：上述 text + `inputs/mouse-video.mp4`；
- source policy：mouse 允许 reconstruction，其余默认；
- 额外断言：mouse + plate + can + table；mouse shape 的 final-frame provenance；text pose override；
  mouse/plate x 差 `0.25±0.01m`；can on plate；报告每实体实际采用的 source/version。

#### H04 — image+video+text, multi-support / seed 229

Prompt：

> 以图片中的鼠标和笔的位置关系为准，视频只补充鼠标外形。把笔改成黄色，其余颜色保持图片所示；
> 保留笔跨放在鼠标与桌面上的关系。

- 输入：上述 text + `inputs/mouse-and-pen.jpg` + `inputs/mouse-video.mp4`；
- source policy：默认固定顺序；
- 额外断言：mouse + pen + table；image/video/text provenance 分字段；yellow 只绑定 pen；保留
  pen 同时接触 mouse 与 table 的多点支撑，不压缩成普通 pen-on-table；遮挡几何和连续物理/视觉 G；
- 当前 SceneIR 基础关系不单列 multi-support，因此若通用 support graph 不能无硬编码表达，应返回
  `unsupported_relation` 和 failure bundle，而不是篡改断言。

### 9.6 计分与跨维度硬门

push 必须同时满足：

- S01–S04 = 4/4；
- 总分 ≥8/12；
- text-only、image-only、video-only、multimodal 各至少一例成功（简单 4/4 会直接保证）；
- local、licensed web、Gujie reconstruction 各至少一个真实成功；
- digital cousin 合同/顺序保留，可明确 `not_run`，不阻断；
- 所有 12 例都有 success environment package 或 immutable failure bundle；
- 下节 supplementary gates、三次 copy-run、CI/coverage 和 final qualification 全部通过。

M01 失败只扣 1 分，不单独阻止 push；但若它成功，必须满足真实 articulation，不能按普通 rigid cabinet
计分。任何 source/模态硬门不能用其他 8 个 text/local 成功抵消。

## 10. 非计分但必须通过的 supplementary gates

以下不进入 12 分母，每条独立分片且 ≤30 分钟：

1. `source-fallback`：至少一条冻结的真实来源 fallback 必须成功，首选
   `local miss -> licensed web`；`web exhaustion -> Gujie` 只在它自然发生或首选路径不可表达时作为
   替代/补充，不新增第二条昂贵硬门。每个 provider 只尝试一次并保留失败；
2. `fallback-A`：通用 qualification-only fault profile 在一个真实 compiled scene 引入可检测的
   support pose 偏移；真实 Codex 根据物理/视觉证据提出 ScenePatch，controller 校验后 recompile/replay。
   新 scene/compiled/package bytes 和 pose 必须变化；
3. `fallback-B`：通用 qualification-only asset metadata fault profile 产生可检测的允许修订问题；
   Codex 提出 AssetPatch，`revise` 生成 immutable child version，parent 不变，再解析依赖/replay；
4. `bounded-stop`：证明 scene+asset 合计最多 2 revisions，以及重复 fingerprint 提前终止；
5. `dead-owner-resume`：只向受控 child worker 的精确 owner 发 SIGINT，不取消用户 workflow；
   workflow 保持 active/recoverable，确认 committed operation 后同 handle resume；
6. `clarification-resume`：冲突 input blocked，追加 immutable clarification revision 后继续；
7. `idempotency`：相同 key 返回相同 workflow/result，不重复 Codex/provider/Genesis。

fault profile 不属于 public `X2EnvRequest`、active Registry 或 scored matrix，只能由 qualification runner
在外部 adapter seam 注入并完整记账。它验证真实 correction path，不得预填 Codex proposal、成功
receipt 或最终结果，也不得以测试配置改变 validator threshold。

## 11. 三次隔离 copy-run

每个 successful case 都做 package member/hash/relative-path 静态检查；以下三个包还要复制到新的随机
目录，并用 Landlock/等价 OS policy 拒绝原 workspace、CAS、asset source 和原 package：

1. S01：Yuxin local exact 代表；
2. 按固定优先级选择首个“实际至少使用一个全新 Gujie geometry”的成功包：
   `M04 > H03 > H04 > M01 > H02 > H01 > M02 > M03`；
3. 排除前两项已选 case 后，按固定优先级选择首个成功的丰富场景：
   `M01 > H04 > H03 > H02 > H01 > M03 > M02 > S04 > S03 > S02`。

三项必须是三个不同 workflow/package。选择规则在运行前固定，不能事后挑最容易的包。每次 copy-run
使用 package 声明的外部 Python/Genesis
环境，执行 version check、load、reset、step、observation/contact/stability，并产生与原包不同路径的
新图片和连续 MP4。外部 runtime 可以复用，8.5 GB runtime 不复制进包；任何原绝对路径访问立即失败。

## 12. 测试、覆盖率和 30 分钟执行纪律

### 12.1 快速 CI

CI 分成六个可单独执行的 test group，每组用
`timeout --signal=INT --kill-after=30s 1770s ...`，并保留 JUnit/coverage/log：

1. contracts / input / generated schema / CapabilityRegistry；
2. controller / journal / idempotency / recovery；
3. AssetResolver / AssetRegistry / Tools；
4. compile / Genesis adapter contract；
5. package / CLI / docs / artifact publisher；
6. retained `scene_gen` 和其他 active noncanonical modules。

canonical core（unified input、SceneIR、controller、状态机、三个 Skills、Tools、两个 Registry）必须
statement 100% 且 branch 100%。外部 Gujie/Yuxin/Genesis adapter 以边界测试和真实 E2E 验收，不用
`pragma: no cover` 或宽泛 exclude 隐藏业务分支。所有 active CI tests、schema check、ruff、docs link、
`git diff --check` 必须全绿。

仓库规则要求的 `pytest -q` 也作为一个独立 ≤30 分钟 group 运行；若超时，安全中断、记录
`cancelled/timed_out`，不换一个无上限命令重跑。旧 qualification hash failure 从 active suite 删除，
不修 snapshot 或降低新门禁。

### 12.2 真实资格分片

- 12 个 scored cases：每 case 的 `interpret -> package` 是一个 1800 秒总预算，不是每个 stage 各
  1800 秒；
- 7 个 supplementary gates：各自一个预算；
- 3 个 copy-runs：各自一个预算；
- final capability/schema/HEAD binding 和远端 artifact download verify：按独立 shard，各自一个预算。

timeout 后只允许：

- 有明确、已提交的实质代码变化；
- 有专门、已记录的性能优化；
- 用户明确要求；

三者之一出现后再跑同一验证。不能立即换 seed、缩短场景、删除 assertion 或用缓存回执“补过”。

### 12.3 运行环境复用而非重复深验

部署启动时一次验证 Python、Genesis、Gujie/SimFoundry、FFmpeg、Codex 和固定 runtime lock，并生成
有作用域/过期条件的 trusted deployment receipt。各 case 引用同一 receipt，不再做 8.5 GB runtime
full closure 或 TB 级 CAS scan。输入、外部下载、新 asset、跨进程输出和 package 仍逐对象 hash 绑定。

## 13. Package、failure bundle 与明确路径

用户指定 `--output /absolute/output` 后，一个 workflow 固定写入：

```text
/absolute/output/
  result.json
  workflows/<workflow-id>/
    workflow.json
    manifest.json
    inputs/
    scene/
      intent.json
      scene-ir.json
      revisions/
    assets/<asset-id>/<version>/
    media/images/
    media/videos/
    logs/
    reports/
    environment/          # 仅 succeeded
      manifest.json
      runtime.lock.json
      run.py
      scene/
      assets/
      reports/
    failure/              # 仅 failed/blocked/cancelled
      manifest.json
      error.json
      human-readable.md
      partial/
```

`result.json` 至少包含：

- workflow ID、status、stop_reason、revision、timings；
- SceneIR path 或 `not_produced`；
- 每个 asset ID/version/source/local path；
- image/video/log/report paths；
- environment package path 或 failure bundle path（二者互斥）；
- manifest digest；
- GitHub Release tag、asset ID、stable download URI、bytes、SHA-256（发布后）。

successful environment 不含原工作区/CAS绝对路径；failure 在 interpret 前失败时明确
`scene_ir.status=not_produced`，不伪造空 SceneIR。每项 check 写 `passed`、`not_run` 或 `failed`。

`runtime.lock.json` 只记录包的 load/replay 所需 Python、Genesis commit/version、平台和必要
Python/native dependencies；FFmpeg 只在用户要求重新编码视频时成为运行依赖。Codex、provider、
Gujie/SimFoundry pin、重建权重和 build commands 进入独立 `reports/build-provenance.json`，不强迫只想
load/step 的用户安装重建栈。包不复制 8.5 GB runtime。`run.py --check` 在实际 replay 依赖不匹配时
fail closed，并给出可直接执行的安装/修复指引。

## 14. GitHub evidence prerelease

默认 artifact store 是当前仓库 GitHub Release assets，后端隐藏在 `ArtifactPublisher` port 后；S3
adapter seam 保留但本阶段不假设 endpoint/bucket。最终 tag：

`x2env-evidence-<full-or-unambiguous-final-commit>`

release 必须标 `prerelease`，说明“qualification evidence only；不表示 main 已合并或产品发布”。
每个文件名带 case/workflow/digest，例如：

- `qv1-S01-<workflow>-environment-<sha256>.tar.zst`；
- `qv1-M01-<workflow>-failure-<sha256>.tar.zst`；
- `qv1-qualification-manifest-<sha256>.json`；
- `qv1-walkthrough-<sha256>.md`。

GitHub asset 并非平台保证不可变，publisher 必须：

1. 使用新 tag + digest filename；
2. 禁止覆盖/clobber；同名不同 digest 立即失败；
3. 记录 tag/release/asset ID、API/browser download URI、size/hash；
4. 从远端重新下载每个 asset 并复算 SHA-256；
5. 先建 `draft + prerelease` 状态并上传；未封存的 draft 在网络中断后可按 digest 幂等补齐缺失 asset，
   已存在 asset 必须先下载确认同 bytes，始终禁止覆盖；
6. 全部 manifest/download checks 通过后才 publish/seal；已 publish 的 evidence release 永远只读。
   若异常地发布了不完整 release，保留并标 invalid，使用
   `x2env-evidence-<commit>-attempt-<n>` 新 tag/release，不靠空代码提交改变 HEAD。

annotated evidence tag 的 message 写入 final HEAD、tree、schema/capability/code closure、
qualification-manifest SHA-256 和 qualification-report SHA-256；这是 Git 内的最终小型 hash/index。
publication receipt（release/asset IDs、下载 URI 和复验结果）作为 prerelease asset 保存，不能为把它
写回 branch 而改变 H。仓库文档按 tag pattern/`x2env evidence --commit H` 发现最终证据。

顺序为：冻结 H → 在 H 运行资格 → 本地生成完整 manifest → push annotated evidence tag（不改
bingsheng/main）→ 建 prerelease/upload → 远端 download verify → 只有全部通过才 fast-forward/push
`worktree/bingsheng`。若证据上传失败，bingsheng 不 push；失败 prerelease/tag 保留并标失败，不覆盖。

## 15. 用户与接入文档

### 15.1 必交文档

- `README.md`：五分钟 quickstart 和能力边界；
- `docs/x2env/quickstart.zh-CN.md`：text/image/video/multimodal 四种真实命令；
- `docs/x2env/python-api.zh-CN.md`、`cli-api.zh-CN.md`；
- `docs/x2env/scene-ir.zh-CN.md`：字段、provenance、关系、坐标、unknown；
- `docs/x2env/skills-tools.zh-CN.md`：三个 Skill、Tools、ToolResult、状态机；
- `docs/x2env/assets.zh-CN.md`：Yuxin provider、许可、Gujie seam、immutable version；
- `docs/x2env/testing-qualification.zh-CN.md`：快速测试、12 例、30 分钟、证据发现；
- `docs/x2env/package-loader.zh-CN.md`：copy/load/reset/step、runtime mismatch；
- `docs/x2env/recovery-errors.zh-CN.md`：resume、clarification、timeout、友好错误；
- `docs/x2env/migration.zh-CN.md`：旧 experiment/preview/MCP/DB/package 不再 active/readable；
- `repo-docs/walkthroughs/canonical-x2env-one-real-run.md`：逐 operation 主代码/合同/证据；
- `docs/x2env/roadmap.zh-CN.md`。

API 字段表和 schema 链接必须生成，不手抄。`AGENTS.md`、进度 README 和 repo-docs README 都链接
计划、walkthrough、测试入口和 evidence discovery；provenance ledger 与每个来源 feature 同提交。

### 15.2 实现完成后的用户测试

以下是冻结的目标 UX；计划阶段尚不可声称已可运行。

Text-only：

```bash
x2env submit \
  --deployment /absolute/deployment.json \
  --text '在桌上放一个粉红色鼠标。' \
  --seed 11 \
  --sources local,web,reconstruction \
  --allow-cousin \
  --idempotency-key user-text-001 \
  --output /absolute/x2env-output/text-001
```

Image-only：

```bash
x2env submit \
  --deployment /absolute/deployment.json \
  --image /absolute/input.png \
  --seed 23 \
  --idempotency-key user-image-001 \
  --output /absolute/x2env-output/image-001
```

Video-only：

```bash
x2env submit \
  --deployment /absolute/deployment.json \
  --video /absolute/input.mp4 \
  --seed 37 \
  --idempotency-key user-video-001 \
  --output /absolute/x2env-output/video-001
```

Multimodal：

```bash
x2env submit \
  --deployment /absolute/deployment.json \
  --text-file /absolute/prompt.txt \
  --image /absolute/reference-1.jpg \
  --image /absolute/reference-2.png \
  --video /absolute/reference.mp4 \
  --seed 41 \
  --sources local,web,reconstruction \
  --allow-cousin \
  --idempotency-key user-multimodal-001 \
  --output /absolute/x2env-output/multimodal-001
```

查询、恢复和重新物化：

```bash
x2env status  --deployment /absolute/deployment.json --workflow-id <uuid>
x2env resume  --deployment /absolute/deployment.json --workflow-id <uuid> \
  --clarification-file /absolute/answer.json
x2env package --deployment /absolute/deployment.json --workflow-id <uuid> \
  --output /absolute/new-materialization
```

Python：

```python
from self_improving.harness.x2env import Harness, X2EnvRequest

harness = Harness.from_deployment("/absolute/deployment.json")
handle = harness.submit(
    X2EnvRequest(
        text="在桌上放一个粉红色鼠标。",
        seed=11,
        idempotency_key="user-python-001",
        output_dir="/absolute/x2env-output/python-001",
    )
)
snapshot = harness.status(handle.workflow_id)
```

## 16. 最终资格、合入与 push

### 16.1 Freeze 前

1. 全部 C01–C14 feature commits 和 provenance 完成；
2. working tree/submodules clean；
3. fast CI、开发 smoke 和 docs sync gate 通过；
4. 再次 fetch，确认 remote bingsheng 是否前进；
5. 若前进：将新远端提交整合到 canonical branch，审查冲突并重跑所有受影响门；禁止 force。

### 16.2 Freeze 后

1. 创建 C15 final HEAD H，记录 tree/schema/capability/code closure；
2. 在精确 H 上运行所有 12 cases、supplementary gates 和 copy-runs；
3. 每次 ≤30 分钟，失败/timeout 不清理现场；
4. 检查 simple 4/4、总分 ≥8、四输入、local/web/Gujie、fallback/recovery/copy-run；
5. 签发绑定 H 的 qualification report；
6. 创建、上传并远端复验 `x2env-evidence-H` prerelease；
7. 在用户 `worktree/bingsheng` worktree 执行 `git merge --ff-only` canonical branch；
8. 再确认 clean、HEAD=H、origin 没有前进；
9. 正常 push `origin worktree/bingsheng:worktree/bingsheng`；
10. 读取远端 ref 确认 SHA=H；dashboard 可用才写 review，否则记录 HTTP 404；
11. 立即暂停，向用户提交本地/远端 evidence、图片、视频、日志和 package 路径。

不得创建 main PR、不得 merge main、不得删除 archive refs/旧分支、不得把 prerelease 标 product release。

## 17. Push 门和失败处置

### 17.1 必须全部满足

- branch/worktree manifest 全覆盖，每个 ref 有处理结论；
- bingsheng dirty bytes 已 snapshot、分类和提交/排除，字节可恢复；
- 一个 Harness、一个 CLI、一套 CapabilityRegistry + 一套 AssetRegistry、三个 Skills；默认无 MCP；
- managed Codex proposal/diagnosis + controller-owned execution；
- Yuxin local/web 唯一 provider 和 Gujie real reconstruction 都真实通过；
- simple 4/4、total ≥8/12、四输入形态成功；
- fallback A/B、预算停止、idempotency、clarification、dead-owner recovery；
- 三个隔离 copy-run；
- active CI 全绿，canonical core statement/branch 100%；
- final HEAD qualification、GitHub prerelease no-clobber/download verify；
- 文档、API、walkthrough、roadmap、provenance 完整；
- bingsheng fast-forward、clean、普通 push，无 force。

### 17.2 不阻断或明确后置

- verified digital cousin 可 `not_run`；
- M01 柜子可失败，只要总体和跨维度门均满足；
- robot policy/data collection、REST/frontend、canonical MCP projection、S3 实际部署；
- autoresearch、高成功率全矩阵、162 份历史 ledger、全面性能优化；
- 旧 qualification 红测和 legacy workflow reader。

### 17.3 门失败时

- 小型、确定性、在本计划边界内且有通用修复：先补攻击测试再修，不加 prompt/category/case 特判；
- 需要新外部依赖、许可、架构扩张、降低门禁或明显高复杂度：停止并请求用户批准；
- qualification case 普通失败：保留固定输入和 failure bundle；可在通用代码修复后形成新 HEAD 重跑；
- timeout：严格遵守“不立即重跑”，只有实质改动/性能工作/用户指令后再试；
- simple <4、总分 <8、web/Gujie/模态/copy-run/CI/coverage/release 任一硬门失败：诚实报告并禁止
  bingsheng push；
- 不以文档箭头、adapter unit test、旧 run、固定回执、人工 SceneIR 或旧媒体冒充成功。

## 18. 计划完成后的 roadmap

按优先级后置：

1. 对真实 digital cousin 做 source/相似度/Genesis 验证并决定是否成为硬门；
2. 有真实外部 consumer 时才增加 canonical MCP projection；
3. robot policy reset/action/observation/termination 和 data collection；
4. REST/API service 和前端工作台；
5. S3-compatible artifact publisher 的真实部署；
6. 12 例之外的 autoresearch、成功率/性能实验和更广 articulation/containment；
7. 历史 162 ledger 专项债务与独立 legacy migration（若届时有真实需求）；
8. 用户批准后的 `worktree/bingsheng -> main` PR、review/CI 修复与正式发布。

## 19. 实时进度与报告规则

本目录继续是 scratch pad 和单一计划事实源：

- `TODO.md`：当前 doing、下一 commit、blocking command/resource；
- `DECISIONS.md`：稳定决定和计划批准状态；
- `RESULTS.md`：实际测试/运行命令、wall、pass/fail/timeout、SHA 和路径；
- `branch-manifests/`：全分支冻结与 disposition；
- `qualification-matrix-v1.json`：本文批准后生成的机读同源 matrix；
- `docs/integration-provenance/LEDGER.md`：每项来源与整合责任；
- `repo-docs/walkthroughs/canonical-x2env-one-real-run.md`：人类可读完整链。

状态表只使用：`合同或设计`、`组件实现`、`集成接通`、`真实通过`、`受阻`、`未实现`。任何
`真实通过` 必须链接对应 workflow、input/asset/scene/media/log/report/package path 和 digest；无 dashboard
task id 时不伪造同步。

## 20. 当前结论

本计划解决的是“收敛后重做 canonical product line”，不是把 357 个实验提交原样推给 bingsheng。
当前代码仍只有有限单刚体实验能力、父终态/恢复缺口、重复 stage dispatch、旧 MCP/CLI/qualification
活跃负担、72.98% 限定覆盖和 1 个旧资格失败。因而现在不能直接合并并 push。

执行完成且第 17 节全部通过后，才可普通 push `origin/worktree/bingsheng`；随后必须等待用户批准 main
PR。若 Gujie real reconstruction、web、simple 4/4、total ≥8/12 或任何其他硬门最终不通过，就保留完整
证据并停在本地/集成分支，不夸大、不强推。

# 新会话接手 Prompt：通用实验管线实际接线

> 使用方式：把本文件全文作为新 Codex 会话的首条任务指令。本文是可执行交接，不要求新会话读取
> 旧会话历史。开始实现前仍须读取仓库 `AGENTS.md` 及本文列出的权威文件，并以实际 Git/进程状态
> 修正任何在交接后发生的变化。

## 你要完成的目标

你正在接手 `/home/jingxiang/bingsheng/robot-harness-gen-env` 的 Golden E2E 长期任务。下一阶段的
第一优先级已经从“继续深验单鼠标 preview 并重新做 release”改为“把通用实验管线实际接通”。
目标流程是：

```text
用户文本／图像／视频及其组合
→ Harness 接收并路由
→ 各模态解析为共享场景意图
→ prompt/结构化输入校验与有界修正
→ 本地资产检索／联网检索下载／新资产生成
→ 资产规范化、登记、场景编译与组装
→ 真实 Genesis replay
→ Codex 视觉观察＋程序化物理诊断
→ 按失败类型有界修复
→ validate
→ 输出可加载、可重放的 Genesis 环境包
```

正确的控制关系固定为：

```text
用户 / 前端 / API
       ↓
Harness 服务与工作流控制器（唯一入口、状态和生命周期权威）
       ├─ CodexBackend：理解、规划、视觉判断、诊断和修改建议
       ├─ Skill executor：compile / replay / validate / observe
       ├─ 资产检索、生成、复用、版本登记
       └─ journal、重试、恢复、包输出和后续发布门
```

Codex 是 Harness 管理的内部模型/agent 后端，替代旧 planner 和 VLM 角色。不要新建“外部 Codex
主控 Harness”的第二套业务 workflow，也不要要求用户配置或操作 Codex/MCP。

本阶段允许稳定成功率、正式资格和回归矩阵不完整，但不能降低真实性：每个输入分支和资产来源
分支都要有实际实现、可调用 adapter 和明确结果；每种模态至少一个真实成功案例，每种资产来源
至少一个真实成功案例，允许少量案例交叉覆盖。失败必须留下结构化原因、日志和已完成产物。不能
用 mock、固定回执、人工预填最终 proposal 或文档箭头冒充接线。

## 开始前必须做的轻量确认

1. 读取根 `AGENTS.md`，以及：
   - `self_improving/golden_e2e_progress/README.md`
   - 本文件
   - `TODO.md`、`RESULTS.md`、`DECISIONS.md`、`SEAMS.md`
   - `docs/integration-provenance/README.md` 与 `LEDGER.md`
   - `docs/research/gujie-x2env-integration-audit-20260911.md`
   - 与准备修改的模块对应的嵌套 `AGENTS.md`、repo-docs 和 contracts。
2. 检查所有 worktree、branch、Git 状态和进程。不要复验已有 8.5 GB runtime 闭包，也不要先跑旧
   bootstrap、repair、qualification 或 Genesis 全量回归。
3. 使用 `tdd`、`codebase-design` 和 `domain-modeling` 技能。主会话负责范围、接口和验收；把互不
   冲突的实现与小型真实验证交给子 agent，每个 agent 独占文件。
4. 当前 dashboard 三个 JSON 入口在交接时均返回 HTTP 404，没有可用 task id；若仍如此，记录阻断，
   不伪造 dashboard 更新。

## 当前 Git、工作树和运行状态

### 权威工作树

| 用途 | 路径 / 分支 | 交接时状态 | 使用规则 |
|---|---|---|---|
| 用户主目录 | `/home/jingxiang/bingsheng/robot-harness-gen-env`，`worktree/bingsheng`，`ea26524cae7b4a18b5576150376eb6ec1ddddf7b` | 有用户未提交改动 | 严禁 reset、checkout 覆盖、清理或顺手提交 |
| 当前集成工作树 | `/home/jingxiang/bingsheng/worktrees/golden-diagnosis-agent`，`codex/golden-diagnosis-20260911` | 实现安全点 `cc6afa236579f2360d808be68ae85e94fcfd81d8`，tree `8321ecb2ba7202451d81af2c3027990ea64f6f26`；本交接文档提交后应再次 `git status` | 新会话从这里继续；先读实际 `HEAD`，不要回退到 `cc6afa2` |
| 固定开发部署源码 | `/home/jingxiang/bingsheng/worktrees/golden-limited-image-preview-development-cc6afa2`，detached `cc6afa2` | clean | 只作为已运行证据的固定源码，不在这里开发 |
| 固定正式部署源码 | `/home/jingxiang/bingsheng/worktrees/golden-limited-image-preview-release-69aa6c2`，detached `69aa6c2dd24292467043e70f73e3e4910842d0f3` | clean | 保留旧资格/正式库历史，不用于新代码冒充 release |
| 前一开发基线 | `/home/jingxiang/bingsheng/worktrees/golden-limited-image-preview-development-ee30d85`，detached `ee30d854f954076f7b19a2d0dbfac88c14526820` | clean | 仅用于已有耗时对比 |
| Gujie 固定来源 | `/home/jingxiang/gujie/gen-env`，`eb0b710581fd7794bc01b447b0f77cb871c8a711` | 工作树 dirty | 只读取固定 committed bytes；不要把未提交内容算入来源 |

用户主目录交接时的未提交项为：

```text
M demo/app.py
? external/OpenReal2Sim
M repo-docs/change-log.md
M repo-docs/code-map.md
M repo-docs/modules/self-improving-platform.md
M repo-docs/references/source-evidence.md
M self_improving/golden_e2e_progress/RESULTS.md
M self_improving/golden_e2e_progress/TODO.md
M self_improving/harness/IMPLEMENTATION_LOG.md
M tests/demo/test_app.py
?? docs/evidence/digital-cousins-dependency-audit-20260906.md
?? docs/evidence/replay-vlm-integration-progress-20260906.md
?? docs/research/
?? self_improving/studies/ASPIRE/environment_failure_skill_research_plan.md
?? self_improving/studies/ASPIRE/execution_engine_explainer.md
?? self_improving/studies/ASPIRE/libero_api_execution_explainer.md
?? self_improving/studies/ASPIRE/skill_learning_retrieval_explainer.md
```

交接时所有本任务子 agent 均已中断，没有 agent 继续扩展。repair 的 Harness、内部 Codex、MCP、
code-mode host 和 Genesis 进程均已正常退出；未使用 SIGKILL，也没有停止其他会话或服务。当前没有
常驻 limited-preview 服务进程，公共 CLI 每次从 deployment 配置恢复服务状态。

停止时的子 agent 和成果位置：

- `/root/dev_validation_metrics`：已中断；已完成的 normal v4 指标和独立验收保留在
  `/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/acceptance/development-cc6afa2/operator/normal-run-2-v4-execution/result-summary.json`
  与
  `/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/acceptance/development-cc6afa2/normal-run-2/verification-v4.json`。
- `/root/real_normal_case`：已中断；normal 已终态，repair 的 compile/replay 和中断现场位于同一
  `acceptance/development-cc6afa2/` 根。
- `/root/real_normal_case/cc6_release_closure_diff`：已中断；normal 的正式库 unchanged 结果已进入 v4
  summary，repair 没有执行新的 formal compare。
- 当前没有 agent 独占文件或继续写文件；集成 worktree 在交接文档提交后应为 clean。

repair 原进程链为外层 shell/PGID `1124576`、timeout `1124586`、Harness CLI `1124587`、Codex
`1124636`、MCP `1124891`、code-mode host `1125425`；这些 PID 在交接时全部不存在。停止过程先向
匹配 repair command line 的 timeout 发 SIGINT，等待原子日志写完，再向仍匹配该 attempt path 的孤立
Codex session leader 发 SIGINT。不要向这些已失效 PID 发信号，也不要用模糊进程名停止其他任务。

仓库还有许多历史 feature worktree；它们不是当前 authority。新会话若并行开发，应基于当前集成
`HEAD` 建立新的 `codex/` 分支/worktree，避免在已有固定运行 worktree 中修改，也避免多人编辑同一文件。

## 已真实通过的 normal 开发案例

这个结果是下一阶段可复用的组件基线，不需要先重跑。

- 固定实现：`cc6afa236579f2360d808be68ae85e94fcfd81d8`。
- 公共 Harness workflow：`2eaecc85-f73c-4c34-9646-8761d8744ba6`。
- 输入从公共 Harness CLI 提交；Harness 自动调用真实受管理 Codex，Codex 经受限 exact MCP，Harness
  驱动真实 compile、Genesis replay、fresh observation 和 validate。用户没有操作 Codex CLI/MCP。
- 一次 Codex attempt、8 次 exact MCP 调用；父 workflow 正确提交为 `validated_unreleased`。
- 执行身份为 `development_unqualified`；没有 publish operation，正式库字节未变。
- review package 有 75 个成员；唯一 Genesis 场景描述入口 schema 为
  `harness.genesis_scene_spec.v1`，SHA-256 为
  `e543ef65e21d380fc5965ff8f8d4f7fb69e2e2408f7a946f4377624c11f98e00`。
- 产物含一张图片和两段连续 MP4；baseline/half-dt 都是 41 帧、640×480、10 fps、4 秒，unique
  frames 分别 24 和 4；双时间步物理/媒体检查为 34/34。
- observation TTL 为 300 秒；validate workflow 准入余量 188.957408 秒，child preflight 余量
  188.931921 秒；terminal wall 余量 98.951911 秒只用于报告，不是 freshness gate。
- 从首次公共 submit 到 materialized package 的公共命令 wall 总计约
  **3107.369648408 秒**，不含人工等待。submit 自身 3105.833309732 秒。后置 v4 深验另用
  242.613330485 秒，不能加进用户请求耗时，也不能当成请求必需步骤。
- 该 run 仍有 21,027,007 次 CAS full-hash scan、约 1.114 TB 逻辑 CAS 读取、63 次 runtime manifest
  deep read、4 次 qualification binding；Linux `/proc` 记录的实际 storage read 为约 3.477 GB，受页缓存
  影响。不要把旧对比中的 4.10% submit 改善夸大成 Genesis/Codex 加速。

权威证据入口：

| 内容 | 绝对路径 / SHA-256 |
|---|---|
| v4 总验收 | `/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/acceptance/development-cc6afa2/normal-run-2/verification-v4.json`；`74375a8b985dbc53f39e21fa4872007019303807922fa3ff3a904181ebb6e817` |
| 汇总与分阶段耗时 | `/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/acceptance/development-cc6afa2/operator/normal-run-2-v4-execution/result-summary.json`；`b9843b9cc430f8b795c591bb9a39a1fc953755f2058afb9a4dae110a1d1a2813` |
| 可审阅包 | `/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/acceptance/development-cc6afa2/normal-run-2/review-package`；index SHA-256 `36805ab55ea9a0c44c13da63e875a029b192d394ed0b0e5f9738fc23d18cc0a4` |
| 场景描述 | 上述包内 `objects/sha256/e5/e543ef65e21d380fc5965ff8f8d4f7fb69e2e2408f7a946f4377624c11f98e00.json` |
| 图片 | 上述包内 `objects/sha256/4c/4cb9b4afb8672a63161dd337368ab80c9cdc70f48f88735fb6a89e37952a2c73.png` |
| 连续视频 | 上述包内 `objects/sha256/f3/f3da017b543923d1a7494107ba7778812d02af514fc60300f899c0e6f425bd5b.mp4` 和 `objects/sha256/a7/a7f1bad14830e6c59fa874ccfe33087599b76ca4285da065577107e9d91fad5a.mp4` |

分阶段 wall 口径保留为：输入/查询 0.346862302 秒；compile child 27.727171 秒；replay+observe 的
仿真/渲染 child 共 846.40678 秒；validate child 89.98001 秒；runtime/integrity/protocol residual
1215.017650087 秒；Codex/MCP residual 97.18692713 秒；开发封装和 materialization residual
830.704247889 秒。它们含嵌套与 residual 定义，必须按证据中的 measurement basis 解读，不能随意相加。

## repair 的准确中断状态

不要根据原计划推断 repair 已成功。用户改变方向后，本会话在安全边界停止了正在运行的 repair：

- workflow：`b57b603e-bf60-4f23-a9b6-1023c5fcbf38`。
- 父 snapshot：`status=active`、revision 2；当前 receipt head SHA-256
  `7a00bba8bfe6d0d1d4c98ed6639931f2f28ff61eb32e9fa0a6fc2fec98dec870`。
- compile child `f7e14f4d-3f33-4736-9125-b53e630110c3` 成功并提交 revision 1。
- replay child `41cb556e-86c1-42f7-97e1-38e95ba419cc` 成功并提交 revision 2。
- validate operation `3771480a-b088-441a-9fc4-6a384bc61eed` / child
  `8e42d014-d88a-4bde-9c69-73578651d6d3` 在 shared `runs.sqlite` 留下
  `preflight: null → running`，没有 terminal RunState、tool result、ended_at 或 committed revision。
- Codex attempt 1 仍记录为 `started`；owner PID `1124587` 已退出。没有伪造 terminal，也没有手改 DB。
- submit 被 SIGINT 正常中断：外层状态 254、内部 exit `-2`，实际 wall
  1741.124157161 秒。stderr 明确为 `CodexBackend.run` 等待子进程时的 `KeyboardInterrupt`。
- 没有在停止后执行 status、resume、package、materialize、deep verifier 或 formal-library compare。
  原始 compile/replay child、CAS、日志、transport、checkpoint 和父状态全部保留。

现场入口：

```text
/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/acceptance/development-cc6afa2/repair
/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/development-cc6afa2/state/golden.sqlite
/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/development-cc6afa2/state/shared/runs.sqlite
/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/development-cc6afa2/codex-attempts/b57b603e-bf60-4f23-a9b6-1023c5fcbf38-1
```

`submit.time.json` SHA-256 为 `f4f535c0b471c6b30bdb18594590d6591999c8c0ad2b4ffb2dee4339624a7721`，
`submit.stderr.log` 为 `ed7b59be29e8a049f88c374b9988c4b78181837b412860e0fbcc96c5d24fa902`，
`submit.exit-status.txt` 为 `935c13c9dd91be5099b33b68eb3e7bae188757853dfdff01ac9e0fda758e8552`。
不要为新目标自动恢复该 repair。若未来专门恢复，只能由现有 controller 按 dead-owner/recoverable-operation
规则处理；先只读确认，再使用一次有界 `resume`。禁止直接编辑 SQLite、重复 submit 同一 key 或把
中断现场标为成功。

## 当前开发模式、入口和限制

当前公共入口是：

```text
self_improving.harness.limited_image_preview_cli
  submit --image ... (--text ... | --text-file ...) --idempotency-key ...
  resume --workflow-id ...
  status --workflow-id ...
  package --workflow-id ... [--output NEW_DIRECTORY]
```

历史部署的只读/恢复配方如下；新会话不应把重跑它当作开发前置：

```bash
cd /home/jingxiang/bingsheng/worktrees/golden-limited-image-preview-development-cc6afa2
unset PYTHONPATH
PY=/home/jingxiang/bingsheng/worktrees/golden-diagnosis-agent/.venv/bin/python
DEPLOYMENT=/home/jingxiang/bingsheng/golden-limited-image-preview-20260911.PtiswA/development-cc6afa2/deployment.json

$PY -m self_improving.harness.limited_image_preview_cli \
  --deployment "$DEPLOYMENT" status --workflow-id <workflow-uuid>
$PY -m self_improving.harness.limited_image_preview_cli \
  --deployment "$DEPLOYMENT" package --workflow-id <terminal-workflow-uuid> \
  --output <absolute-new-directory>
```

若仅为支持范围内的新开发冒烟，使用新的 idempotency key：

```bash
$PY -m self_improving.harness.limited_image_preview_cli \
  --deployment "$DEPLOYMENT" submit --image <absolute-image> \
  --text-file <absolute-text-file> --idempotency-key <new-unique-key>
```

只有持久 workflow 非终态、旧 owner 已退出、错误非确定性且 operation 可恢复时，才允许一次有界：

```bash
$PY -m self_improving.harness.limited_image_preview_cli \
  --deployment "$DEPLOYMENT" resume --workflow-id <workflow-uuid>
```

CLI 是前台进程；需要停止时先向准确匹配该 workflow/attempt 的 foreground owner 发 SIGINT，让日志和
journal 完成原子退出。不要靠删除 state、修改 SQLite 或杀所有 Codex/Genesis 进程“恢复”。

当前 development mode 已实现：真实 Codex、真实 exact Skills、真实 Genesis 和真实资产；记录 Git
commit/dirty/diff fingerprint、解释器/环境/config、输入输出、日志、执行与省略检查；development
产物与正式库隔离；release 失败不会自动降级。它仍固定：

```text
exact_skills_qualified=true
changed_exact_skill_closure_allowed=false
```

这两个字段在 `limited_image_preview_development.py` 和 public schema 中仍成立。因此修改核心 exact
Skill closure 后，现有固定部署不能直接拿来调试。下一会话已获授权做一个最小契约调整，把“允许
未正式资格化的开发执行”和“取得 release qualification”分开：必须显式由部署配置控制，记录 changed
closure 和省略检查，继续隔离开发产物；不得伪造资格、动态换 verifier、让 prompt/Codex 切换模式，
也不得让 release 自动降级。优先复用现有 controller、journal、MCP、SQLite/CAS，不建第二套系统。

当前入口仍只支持 image+text、mouse-only、exact-only。normal 的包是 75-member 场景描述和证据集合，
虽已逐成员深验，但**尚未**证明从复制后的新目录、不依赖原工作区或原 CAS 绝对路径独立加载和运行。
没有验证机器人 policy 或数据采集接口，也没有授予这些能力。

当前包能力边界应逐项表述：成员和复制后 CAS bytes/SHA 完整性已通过；场景描述、鼠标资产 closure、
物理报告、图片和视频均已进入 review package；从包内入口直接启动 Genesis 尚未执行；跨目录运行
尚未执行；策略接口和数据采集接口均未实现/未验证。不要把前三项静态 closure 合并叙述成 standalone
environment pass。

## Gujie 固定来源和已有 adapter：直接复用，不从零重研

权威审计是 `docs/research/gujie-x2env-integration-audit-20260911.md`。Gujie 有效固定源为：

```text
/home/jingxiang/gujie/gen-env@eb0b710581fd7794bc01b447b0f77cb871c8a711
重点祖先：9e8d28c24ca72ebf4001c2460dc4b174cef603c0
          a8ced2732b48d3eb89e5c40d6cd7fd71b275f178
Genesis gitlink：0e74bf392781884ccad765c3f344419c86b872ca
SimFoundry gitlink：9e34ebefcd020583fbb755a8b57268dce78eca26
```

不要把旧
`/home/jingxiang/gujie/robot-harness-gen-env@a25bc58853db1988aafafe88b544e0573eacbb3c`
当成最新实现；该 worktree 已
prunable。不要纳入 Gujie 当前 dirty/untracked bytes。优先从固定提交读取：

```text
self_improving/sim_adapters/genesis/reconstruct_media.py
self_improving/sim_adapters/genesis/import_simfoundry_scene.py
self_improving/sim_adapters/genesis/task_output.py
self_improving/sim_adapters/genesis/standard_urdf.py
self_improving/sim_adapters/genesis/validate_imported_scene.py
self_improving/sim_adapters/genesis/extract_assets.py
self_improving/sim_adapters/genesis/build_scene.py
self_improving/sim_adapters/genesis/scene_physics_workflow.py
self_improving/sim_adapters/genesis/MEDIA_RECONSTRUCTION_EVIDENCE.md
self_improving/sim_adapters/genesis/PHYSICS_VALIDATION_EVIDENCE.md
```

当前 Harness 已有、应优先复用的 adapter/实现包括：

```text
self_improving/harness/gujie_scene_adapter.py
self_improving/harness/revised_tabletop_scene.py
self_improving/harness/genesis_scene_execution_v2.py
self_improving/harness/genesis_scene_execution_v3.py
self_improving/harness/genesis_scene_replay.py
self_improving/harness/genesis_scene_replay_batched.py
self_improving/harness/genesis_scene_assessment.py
self_improving/harness/genesis_scene_validation_application.py
self_improving/harness/image2env_golden_workflow.py
self_improving/harness/image_ingest.py
self_improving/harness/video_ingest.py
self_improving/harness/x2env_contracts.py
self_improving/harness/x2env_compile_closure.py
self_improving/harness/image2env_compile.py
self_improving/harness/image2env_genesis_compile.py
self_improving/harness/legacy_genesis_asset_staging.py
```

旧 text 路径在稳定 `scene_gen/` 与 `self_improving/harness/handlers/text2env_compile.py`、replay、
validate/validate_v2；它主要面向 RoboTwin/SAPIEN，没有成为统一 Genesis 通用管线。Video 当前只有
deterministic ingest/contracts，没有 runtime compile/replay/validate、Registry 或 MCP 路径。

资产候选在 `self_improving/asset_pipeline/active/1_asset_reuse/`，重点为 provider
`lib/a1_providers.py`、selection `a2_selection.py`、web fetch `a3_webfetch.py`、coverage `a4_coverage.py`、
visual `a5_visual.py`、verify `a6_verify.py`、Objaverse `a7_objaverse.py` 与 ledger 模块。固定来源 refs：
asset reuse ABC `4982cc3660d82743b849fd0ba9cbdfd04933e895`，asset sources
`8c2f058215b4d69d10f15c269148397a9f14ccd2`。这些组件尚未接入当前 limited preview 的通用 workflow。

`external/OpenReal2Sim@4b2d095bd084f04c66be09a6d03220779f5e49ed` 与
`external/digital-cousins@b1aa90a8dec7273e5c5a4a60450dc829083ce0a0` 是候选来源；在固定 `cc6afa2`
checkout 中子模块未初始化，不能称运行接通。联网/生成 provider 若缺凭据或服务，应完成 adapter 并
标为“受阻”，集中列出所需资源；不能标为真实通过。

## 目标环节接线表

状态只使用：`合同或设计`、`组件实现`、`集成接通`、`真实通过`、`受阻`、`未实现`。括号只限定
已经证明的子集。

| 目标环节 | 现有代码与证据 | 接线状态 | 缺口 | 下一动作 |
|---|---|---|---|---|
| Harness 用户入口与生命周期 | limited preview CLI/service/controller；normal v4 | 真实通过（image+text、mouse exact 子集） | 尚无统一三模态请求和通用结果 | 扩展现有请求/路由，保留一套 journal/controller |
| 内部 CodexBackend | `codex_backend.py`、attempt journal、受限 MCP；normal 一次真实 attempt | 真实通过（normal 子集） | repair 的诊断修订未完成；通用动作 schema 未接 | 让 route/intent/diagnosis 决策结构化并由 Harness 校验 |
| 文本输入 | `scene_gen/` 与旧 text2env handlers | 组件实现 | 未接统一 SceneIntent，也未走通通用 Genesis 包 | 写 text→共享意图 adapter，跑一个小型真实案例 |
| 图像输入 | `image_ingest.py`、受限 image preview | 真实通过（mouse exact 子集） | 当前资产策略固定，不能泛化 | 将 image intent 与资产来源选择接到统一 router |
| 视频输入 | `video_ingest.py`、完整逐帧证据合同 | 组件实现 | 没有 video→intent→compile/replay/validate | 接 adapter，并跑至少一个真实视频案例 |
| 多模态组合与共享场景意图 | `x2env_contracts.py`、SceneIR/TaskIR 合同 | 合同或设计 | 没有实际统一 router/merge 规则 | 补最小 discriminated input 和共享 SceneIntent seam |
| prompt/结构化校验与有界修正 | PromptRevision、controlled decision contracts | 组件实现 | 尚未用于三模态通用请求 | 复用现有 control records，限制字段与 revision |
| 本地资产检索 | asset pipeline providers/selection；mouse exact registry | 组件实现 | provider 未接当前 workflow；只真实通过固定 mouse | 加 adapter、来源 receipt、一个非硬编码调用案例 |
| 联网检索/下载 | `a3_webfetch.py`、Objaverse/provider 候选 | 组件实现 | 未接、外部可用性/凭据未实测 | 在隔离目录真实下载一个许可明确资产；受阻则列资源 |
| 新资产生成/重建 | Gujie SimFoundry reconstruction；OpenReal2Sim/digital-cousins 候选 | 组件实现 | 固定实现未接 Harness；子模块/服务可能缺依赖 | 基于固定 Gujie adapter 接一个真实生成/重建调用 |
| exact/digital cousin 选用策略 | x2env contracts、旧 selection | 合同或设计 | 来源与选用策略尚混杂，digital cousin 未跑 | 将 local/web/generated 作为来源，exact/cousin 作为选择字段 |
| 资产规范化、版本登记与复用 | standard URDF 思路、legacy staging、CAS/qualification、ledger | 集成接通（mouse 子集） | 通用 mesh/URDF/单位/引用/版本写入未接 | 产出不可变新版本和依赖 closure；不改已有资产 |
| Genesis compile/组装 | image compile v2/v3、Gujie scene adapter | 真实通过（mouse exact 子集） | text/video/新资产未接 | 让共享意图和 provider 结果消费现有 compiler seam |
| Genesis replay + 媒体 | scene execution v2/v3、replay，normal 双 dt | 真实通过（有限桌面 mouse 子集） | 通用场景与可移植包未跑 | 先跑小场景；保留真实图片和连续视频 |
| fresh observation | observe v3、TTL/state delta；normal | 真实通过（normal 子集） | 组合场景的观测消费未验证 | 按“准入时新鲜”顺序在新案例中复用 |
| 程序化物理 validate | assessment/validation；normal 34/34 | 真实通过（有限 profile） | 只覆盖固定刚体/桌面判据 | 明确每类场景已执行/未执行/失败的 profile |
| 视觉诊断 + 物理诊断合并 | Codex 媒体读取和 validate evidence 均已有 | 集成接通 | repair 在首次 validate 期间中断，没有真实修订闭环 | 先做一个有界场景修复案例；画面判断只作建议 |
| fallback A：场景/布局修复 | advisory、PromptRevision、compile v3、replay v3 组件 | 组件实现 | 未真实完成 diagnosis→修改→replay→validate | 按下节预算跑通一个位置/朝向实际变化案例 |
| fallback B：资产修复/重获 | staging、资产 probe/qualification、Gujie asset 工具候选 | 组件实现 | 没有受控 asset patch→新版本→场景重解路径 | 新建最小资产 patch 工具边界和一个真实案例 |
| 数值配置修正 | 固定 replay profiles | 合同或设计 | 没有独立受限修订动作 | 仅为已分类数值失效增加白名单字段/预算 |
| 可迁移 Genesis 环境包 | 75-member review package、scene entrypoint、媒体和证据 | 组件实现 | 没做复制到新目录后的独立 load/step；可能仍依赖原 CAS/工作区 | 增加 relocatable refs/loader，完成一次 copy-run |
| development/release 隔离 | development record、formal library gate、旧 `69aa6c2` 资格 | 真实通过（隔离语义） | changed exact Skill closure 仍禁止开发调试；新实现未 release | 做最小 dev 契约调整；release qualification 后置 |
| 正式发布、全矩阵、autoresearch | 既有合同与计划 | 合同或设计 | 本阶段明确后置 | 不启动 |
| robot policy / data collection | `genesis.robot_policy@1` 合同 | 未实现 | 未联调、未运行 | 继续后置；不得授予能力 |

## 未完成与已知失败清单

- repair 是准确的非终态中断现场；首次 validate 没有结果，诊断、PromptRevision、recompile、第二次
  replay/validate 都没有发生。
- normal 只覆盖 image+text、固定 mouse exact reuse 和有限桌面；不能证明 text-only、video、组合
  输入、新物体或通用布局。
- video ingest 已通过 deterministic consumer，但没有业务 compile/replay/validate；旧 text2env 也未
  接到统一 Genesis SceneIntent。
- local/web/generated provider 尚未进入当前 controller；联网与生成依赖、凭据和许可证可用性未做
  本阶段真实检查。
- fallback A 只有分散组件，未完成一次真实 Codex 诊断修订闭环；fallback B 和独立数值修订尚未
  形成 Harness 业务路径。
- development 当前禁止 changed exact Skill closure；若不做最小版本化调整，新代码只能通过 unit
  seam，不能跑真实开发 E2E。
- materialized normal package 的 bytes/hash closure 已验证，但没有 package-owned loader，也没有完成
  脱离原 workspace/CAS 的 copy/load/step。
- normal 请求约 3107 秒，并存在大量重复 runtime/CAS 深读。下一阶段可做轻量有界复用，但不要先
  把全面性能优化变成接线前置。
- `69aa6c2` 是旧固定 release 资格成果；`cc6afa2` normal 是开发未发布结果。repair 没有发布，正式库
  未改变。
- Gujie 当前 worktree dirty，OpenReal2Sim/digital-cousins 在固定 checkout 中未初始化；只能使用本文
  列出的 committed refs，或另行固定新来源。
- 162 份历史 ledger 债务仍在；robot policy、数据采集、正式 qualification、autoresearch 和全矩阵
  仍未完成，全部后置。

## 两类 fallback 必须分流

### A. 场景意图或布局失败：回到 compile

适用于数量、位置、朝向、支撑关系、prompt 歧义或 SceneIR 结构不合法：

```text
诊断
→ Codex 提出有界 Prompt/SceneIR 修正
→ Harness 校验允许字段、证据引用、base revision 和预算
→ 生成新 revision
→ compile
→ replay
→ validate
```

至少验证一次实际位置、朝向或其他允许布局字段改变，scene/package bytes 也随之改变。只改 prompt、
receipt 或报告文字不算修复。建议初始预算为每个问题最多 2 个修订、每个修订一次 replay；相同输入、
相同错误码和相同证据摘要再次出现就停止，保留现场并返回结构化 failure。

### B. 资产问题：产生新资产版本或重新获取

适用于 mesh、碰撞体、缩放、单位、URDF、质量、摩擦或资源引用问题：

```text
诊断
→ Codex 提出结构化 asset patch，或调用受限资产编辑工具生成候选文件
→ Harness 在隔离工作目录应用白名单修改
→ 格式、引用、几何及必要物理检查
→ 登记新的不可变资产版本
→ 重新解析场景依赖；需要时重新 compile
→ replay
→ validate
```

不得直接修改已入库资产、旧证据或旧版本。Codex 不得任意编辑业务 SQLite、qualification、verifier、
项目源码或 CAS；修改文件可以是受控工具，但 Harness 必须检查根目录、文件类型、字段、引用和结果。
建议每个资产最多 2 个候选版本、每个版本一次 probe/replay；确定性格式/引用错误重复即停止。获取服务
缺凭据时不要消耗模型重试，返回 `blocked_external_resource` 和所需资源。

仿真数值配置问题走单独的白名单配置修订，例如明确允许的 dt/solver 参数；不能冒充资产或场景修复，
也不能修改成功阈值来制造 pass。Codex/VLM 的画面判断是视觉意图诊断，不替代轨迹、接触、穿透、
支撑和稳定性指标。最终结果应分别报告 visual intent 与 physical profile。

## 真正可用的 Genesis 包目标

新会话不能把 JSON 中出现 `environment_packages` 字段当成交付。开发包可以实际可用而尚未正式认证，
其最小内容是：

- 场景描述和完整必要资产；
- 路径可迁移的资源引用，不引用原 workspace/CAS 的绝对地址；
- 明确的 Genesis load/run 入口；
- Python、Genesis commit 和依赖说明，不复制整个 8.5 GB runtime；
- 输入、来源、资产版本、SceneIntent/修订和验证报告；
- 至少一张可查看图片和一段真实连续视频；
- 对每项检查明确写 `passed`、`not_run` 或 `failed`。

至少执行一次：把包复制到新目录，使用声明的运行环境，在禁止读取原 workspace 和原 CAS 绝对路径
的条件下加载场景、执行若干步并生成新的输出。可以使用外部已声明 Python/Genesis 运行环境，但不能
偷偷依赖原构建目录中的资产。

`sim-ready` 只能授予实际证明的 profile，例如“Genesis 版本 X 可加载、可步进，刚体接触/穿透/
稳定性检查通过”。没有运行机器人策略或数据采集时，明确 `robot_policy_evaluated=false` 和
`data_collection_evaluated=false`。

## 开发与验证策略

- 不先重跑旧 bootstrap、资格重建、TB 级闭包或单鼠标 repair。
- 优先复用当前 development mode。若 changed core Skill 仍被挡，先以 TDD 做上述最小开发契约调整；
  保留 release 隔离和所有请求级真实性/完整性检查。
- 快速测试集中在统一路由、adapter 输入输出、错误语义、状态推进、幂等、恢复和 package loader。
- 每完成一个独立分支就跑一个小型真实案例，再逐步组合。不要每改几行就跑昂贵 Genesis 候选或安排
  全套代码审查。
- 共同运行环境和同一不可变证据可复用；记录可信身份、作用域和失效条件。不要仅凭路径、mtime 或
  文件名含 hash 永久信任。
- Git 管理项目改动，固定依赖说明管理 runtime；哈希用于必要的输入、资产、输出和报告绑定。
- 正式 release qualification、大规模 autoresearch、全矩阵成功率、全面性能优化、162 份历史 ledger
  清理和 robot policy 后置，不删除原计划也不标记完成。
- 资产“本地库／联网搜索／生成”是获取来源；`exact`／`digital cousin` 是选用策略。只实现真实需要
  的对应关系，不为术语再建通用插件系统。

## 直接执行顺序

1. 轻量确认本交接的 Git、进程、normal 和 repair 状态；不重跑已完成验收。
2. 盘点 text/image/video 入口和现有 local/web/generated provider，产出基于真实 callsite 的接线图。
3. 补统一输入、router，以及各模态到共享 SceneIntent/SceneIR 的 adapter；错误和部分产物进入现有
   workflow journal。
4. 接通本地检索、联网获取、新资产生成/重建；在隔离目录规范化，并登记可复用不可变资产版本。
5. 把共享意图和资产结果接入现有 Genesis compile/replay/observe/validate，不另建执行权威。
6. 接通场景/布局 fallback A、资产版本 fallback B，以及必要的受限数值配置修正；设置预算和停止条件。
7. 输出有 loader 的路径可迁移环境包，并完成一次复制到新目录后的真实 Genesis load/step/新媒体验证。
8. 用少量真实案例覆盖三种输入和三种资产来源；允许交叉覆盖，完整记录失败，不追求本阶段高成功率。
9. 提供一个 Harness 实验服务/CLI 入口、能力清单和 walkthrough，暂停供用户审阅。

可并行的内容由新会话根据文件所有权决定。不要让两个 agent 修改同一文件，也不要把“合同存在”或
“组件测试通过”写成“集成接通/真实通过”。

## 本阶段完成时必须展示

- 一个用户只操作 Harness 的入口；
- 每种模态至少一个真实成功案例；
- local/web/generated 每种资产来源至少一个真实成功案例，或明确的外部资源阻断；
- 两类 fallback 的真实执行结果和有限重试停止证据；
- 一个复制到新目录后真实加载、步进、产生新输出的 Genesis 包；
- 图片、连续视频、环境包、日志、workflow 状态和验证报告入口；
- 状态表中每项只能使用本文六个状态词，并列出仍未实现/受阻的部分；
- 小型真实案例的耗时和失败，不用单一百分比掩盖分支差异；
- 干净、逐功能的提交和更新后的 TODO/RESULTS/DECISIONS/provenance/repo-docs。

完成上述实验版本后暂停。不要在本阶段继续正式 release、全量历史债务、autoresearch、机器人策略、
无关前端或通用插件化。

# Self-Improving Harness 实施日志

这份日志是当前实施工作的主账。它记录已验证事实、接口决策、实验尝试、产物位置和
验收结果；没有实际运行证据的能力不得在这里标记为完成。

## 目标与边界

- 目标：接通 compile、资产复用/缺失生成与入库、replay、validation、VLM fallback、
  LLM System 2 编排，以及真实代码事件驱动的前端工作台。
- 物理真值边界：渲染、VLM 判断、进程退出码都不能替代 SAPIEN/RoboTwin 连续运行时
  证据；发布资格只由哈希绑定的 validate 门禁给出。
- 产品边界：Phase 2 允许实现与验证仿真闭环，但不把它描述成已经部署的真机闭环。
- 提交纪律：一个独立 feature 一个提交；代码 feature 先写公共接口测试，再做最小实现，
  并以该接口的 100% 覆盖率作为提交门。

## 已确认的公共 Seam

用户已批准完整的 Self-Improving Harness 范围，并明确要求继续实施、不在阶段间等待确认。
据此，以下项目契约作为 TDD 的已确认公共 Seam：

1. `ArtifactResolver.resolve(ArtifactRef) -> ResolvedArtifact`：只按内容身份解析并校验制品；
   不静默改写已经哈希绑定的场景。
2. `SkillRegistry.invoke(skill_ref, parameters) -> RunState`：版本选择、默认值、依赖解析、
   attempt、审计和执行结果只有一个权威入口。
3. Text2Env `compile / replay / validate` Adapter：复用现有 `scene_gen` 实现，返回既有严格
   schema；资产检索、生成和准入证据作为额外制品保留，不偷塞进冻结输出模型。
4. `EventSink.publish(RunEvent)`：执行代码在实际阶段边界发布事件；API 和前端只能消费这些
   事件，不能用定时器伪造阶段进度。
5. System 2 Agent 只生成类型化计划、选择 Skill、诊断失败和提出候选修复；System 1/0
   Adapter 执行确定性编译、仿真或控制器调用；晋升门禁仍由确定性回归结果决定。

## 阶段计划与完成证据

| 阶段 | 状态 | 完成证据 |
|---|---|---|
| 0. 基线、架构和追溯主账 | 完成 | 本文件、代码接线图、环境能力审计 |
| 1. 资产与 runtime 证据完整性 | 待开始 | 攻击测试、迁移报告、最终帧/步数一致性 |
| 2. Harness 核心 | 进行中 | Registry、resolver、receipt、事件流单元测试 |
| 3. compile → 资产入库 | 进行中 | 真实 fixture 端到端运行与入库 ledger/receipt |
| 4. replay → validate | 待开始 | 实际播放调用、连续帧、哈希绑定验证报告 |
| 5. VLM fallback 研究 | 待开始 | baseline、逐次实验 TSV/JSONL、消融与总结 |
| 6. LLM System 2 | 待开始 | agent 计划、上下文包、工具回执、回归晋升 |
| 7. 前端工作台 | 待开始 | 四页面、真实事件流、浏览器端到端测试 |
| 8. 总验收与文档同步 | 待开始 | 全量测试/覆盖率/真实回放/repo-docs 审计 |

## 决策与尝试记录

### 2026-08-31 / A000：建立基线

- 事实：当前分支为 `worktree/bingsheng`，工作树起始时干净；Harness 只有 14 份公共 schema
  与校验模型，没有 Registry、resolver 或 handler。
- 事实：同步回来的主要能力在 asset pipeline、scene/runtime 与跨仿真层，不是 Harness
  本身的升级。
- 基线测试：仓库全量 `125 passed`；Harness `21 passed`；asset pipeline `287 passed,
  1 skipped`；schema snapshots、`git diff --check` 和 runtime CLI help 均通过。
- 外部状态：ClawCross dashboard 的 portfolio、tasks、project 三个约定入口均返回 HTTP 404；
  因此本轮不能把 dashboard 当作可读取的任务真相源。
- 已知前置缺口：ledger v3 标识早于字段真实迁移、adaptive settle 的最终物理状态与最终画面
  不一致、resolved scene 含机器绝对路径、批量 probe 输出误入版本库。
- 决策：先做能被 compile/replay 共用的内容寻址和账本准入接口，再做 handler；避免为每条
  路由复制路径与摘要逻辑。

### 2026-08-31 / A001：内容寻址制品存储与解析

- 红灯：新增公共 Seam 测试时，`self_improving.harness.artifacts` 不存在，测试在收集阶段失败。
- 实现：增加 `LocalArtifactStore`，写入 `artifact://sha256/<digest>` 的不可变本地 CAS；增加
  `ArtifactResolver`、`ResolvedArtifact` 与带稳定 `reason` 的 `ArtifactResolutionError`。
- 安全边界：`file://` 只作为经 `sha256 + bytes` 双校验的外部输入定位器；CAS URI 中的摘要
  必须与 `ArtifactRef` 一致。定位器不被当作身份。
- 攻击用例：覆盖 URI/摘要错配、字节数错配、等长内容篡改、不支持的远程 URI 与缺失制品，
  均 fail closed。
- 验证：Harness `24 passed`；新模块 statement coverage `100%`；ruff 与 diff check 通过。
- 产物：`self_improving/harness/artifacts.py`、
  `tests/self_improving/harness/test_artifacts.py`。

### 2026-08-31 / A002：真实执行事件记录器

- 红灯：公共包没有 `RunRecorder` / `RecordingEventSink`，事件测试在导入时失败。
- 实现：增加内部 `RunEvent` envelope，为既有公共 `Event` 补上 run/skill 身份；
  `RunRecorder` 独占 seq、attempt、时间、状态转换与 `RunState` 组装，handler 只报告真实阶段。
- 决策：不修改冻结的 `harness.event.v1`；运行身份属于内部传输 envelope。前端只能消费
  `EventSink`，不能根据 sleep、文件 mtime 或日志关键词推演进度。
- 攻击用例：拒绝开始前 progress、重复 start、用 running 伪装 finish、终态后 retry、未开始
  build state 和时钟倒退；所有非法事件都在进入 sink 前被拒绝。
- 验证：事件模块 statement coverage `100%`；完整成功/进度/重试/终态序列能通过既有
  `RunState` 不变量校验。
- 产物：`self_improving/harness/events.py`、
  `tests/self_improving/harness/test_events.py`。

### 2026-08-31 / A003：把 compile 从 CLI 提炼成深 Module

- 红灯：仓库没有可导入的 `scene_gen.compiler`；测试只能复刻 CLI 步骤。
- 实现：`compile_scene(CompileRequest) -> CompileOutcome` 现在一次完成 parse、catalog load、
  可选确定性 proxy、solve、package 与 static validation；不扫描输出目录、不打印、也不二次
  解析 prompt。原 `generate_scene.py` 已变成参数/错误呈现 Adapter。
- 真实回调：每个阶段完成后才产生 `CompileEvent`，包括真实产物路径；没有基于时间或日志猜测。
- 失败分类：请求边界、资产缺失、资产生成拒绝与 bounded solver exhausted 分别保存稳定 code、
  stage 和 details，供 Harness 映射 blocker。
- 实证：fixture can-on-plate CLI 成功，package manifest 和 resolved digest 一致，静态验证为预期
  `incomplete`；非法短请求写入结构化失败报告并退出 2；缺失 hexagonal pedestal 在显式
  workspace 中生成、复用并进入 effective catalog。
- 验证：compiler 模块 statement coverage `100%`；相关 compiler/builder/asset-generator
  `20 passed`；ruff 与 diff check 通过。
- 边界：这一切片完成“生成并进入有效 catalog”，但还没有通过 `asset_ledger.v3` 准入；不能把
  proxy 目录称为正式资产入库。下一切片补 ledger、selection/admission receipt 与原子 promote。
- 产物：`scene_gen/compiler.py`、`tests/scene_gen/test_compiler.py`、变薄后的
  `script/generate_scene.py`。

### 2026-08-31 / A004：本机运行环境能力审计（只读）

- compile：Py3.11 环境可立即运行。
- RoboTwin replay：唯一完整环境为 `robotwin-5090`；短 2-step smoke 已真实加载 `071_can`、
  物理步进、渲染 4 相机、输出 2 帧 MP4 与 digest-bound evidence。它只因正式门要求 120 帧
  而 validation fail；已有同场景 900-step/120-frame 历史 PASS，可作为首条正式回归。
- VLM：本地 Qwen2.5-VL-3B 已对真实 render 运行，约 9.7 秒且输出 5 项 hash-bound checks；
  CLIP 检索也已用 10,696 thumbnails/缓存真实运行。远程 OpenAI/Moonshot 当前无 key。
- Isaac：引擎可 headless 启动，但旧绝对资产路径阻断 resolved→USD→settle。
- MuJoCo：primitive transfer + 20-step smoke 可跑；真实资产 OBJ 与 table/support settle 仍缺。
- 决策：正式 replay 先选可实跑的 RoboTwin `071_can on table`；Isaac/MuJoCo 不用假替身冒充
  闭环，分别等 portable asset resolver 和真实 support gate 后再晋升。

### 2026-08-31 / A005：生成资产的 v3 ledger 准入与原子提升

- 红灯：compile 的 proxy 只有 OBJ/metadata/provenance 文件，没有正式账本；新增端到端测试时
  `GeneratedAssetAdmitter` 不存在。
- 实现：compiler 新增通用 `AssetAdmitter` Seam；平台 Adapter 将 run staging 中的生成资产复制
  到 `.incoming`，构建并校验完整 `asset_ledger.v3`，再用单次原子 rename 提升到
  `asset_library/generated/<asset_id>`。solve 只使用提升后的路径。
- 账本事实：`external_ids.env_gen`、每个 representation 的 `frame`、`geometry_state`、`files`、
  collision representation 的 `collision_meta`、stable pose 的 `measured_against`、完整生成器
  prompt/seed/version/params/license 均已保留；不写 v3 已删除的 runtime defaults。
- 资格边界：当前只写 `generation_qc=pass`，回执明确 `physical_qualification=pending_settle`；
  未经过 SAPIEN settle 的资产虽然已经可审计入库，但不会冒充物理晋升通过。
- 原子性与幂等：同内容重跑为 `reused`；结构校验失败时 pool 不出现目标目录；提升后文件校验
  失败时移到 `.rejected`；缺账本、文件篡改或 ledger 身份错配都 fail closed。
- 实证：hexagonal pedestal 从自然语言输入生成，ledger `check_files=True` 为零 violations，
  effective catalog 和 resolved scene 都引用正式 pool 路径；第二次运行 ledger digest 不变。
- 验证：admission + compiler 两个新模块合计 statement coverage `100%`，端到端 `9 passed`；
  active ledger/audit 回归 `92 passed`，ruff 与 diff check 通过。
- 产物：`self_improving/harness/assets.py`、
  `tests/self_improving/harness/test_asset_admission.py`、compiler 的 admission Seam。

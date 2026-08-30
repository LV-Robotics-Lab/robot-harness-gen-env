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
| 0. 基线、架构和追溯主账 | 进行中 | 本文件、代码接线图、环境能力审计 |
| 1. 资产与 runtime 证据完整性 | 待开始 | 攻击测试、迁移报告、最终帧/步数一致性 |
| 2. Harness 核心 | 进行中 | Registry、resolver、receipt、事件流单元测试 |
| 3. compile → 资产入库 | 待开始 | 真实 fixture 端到端运行与入库 ledger/receipt |
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

# 进度与结果

## 2026-09-09 初始化

- Dashboard/ClawCross 项目状态读取：失败，三个公开状态 URL 和 Harness dashboard 均返回 HTTP 404。
- 当前分支：`worktree/bingsheng`。
- 当前 HEAD：`d8b3787`（`chore(portal): remove PEARL evidence portal`）。
- 当前工作树已有用户修改和未跟踪文件；本任务不会批量暂存、还原或覆盖这些内容。
- 本仓库的本地 `worktree/gujie` ref 指向旧祖先 `a25bc58`，不能代表 Gujie 当前实现。
- Gujie 的真实独立克隆位于
  `/home/jingxiang/gujie/gen-env`：当前 HEAD `a8ced27`，其 `origin/worktree/gujie` 同样指向
  `a8ced27`；该提交比已合并 bingsheng 的 `4f6ef23` 多 52 个提交。该工作目录另有大量未提交
  Genesis adapter、测试和文档修改，审计必须把 committed ref 与 dirty workspace 分开，且保持只读。
- 历史 `gujie-x2env-design.md` 明确自称“架构设计阶段”，不能作为已实现 x2env 的证据；真实实现
  资格必须从上述独立克隆的代码、测试和运行产物重新判断。
- 功能基线、Genesis 可用性和路由成功率：待调研后测量，历史测试数字不冒充当前 HEAD 基线。

## 2026-09-09 当前工作树测试基线

- 命令：`pytest -q`
- 结果：`3015 passed, 19 skipped, 1 failed in 142.21s`。
- 唯一失败：`test_checked_in_replay_qualification_matches_current_source_identity`。
- 原因：当前用户修改的 `self_improving/harness/IMPLEMENTATION_LOG.md` 与
  `text2env.replay@1.0.0` qualification manifest 固定的 bytes/SHA-256 不一致；fail-closed 行为符合
  设计，但当前工作树不是可晋升基线。
- 该数字只是当前共享工作树观察值；不能与后续切片测试相加，也不能代表 clean HEAD 或 Genesis
  真实运行通过。
- `python -m self_improving --json`：`ready=false`，因为 HEAD 已删除
  `apps/pearl_evidence_portal`，但 registry 仍把它列为 required；统一平台门当前在测试前失败。
- `python script/export_harness_schemas.py --check`：20 份 Harness schema snapshot 全部通过。
- 当前默认 Python 不含 Genesis。Gujie 独立克隆已有隔离环境
  `/home/jingxiang/gujie/gen-env/venv/genesis`，可导入 Genesis `1.3.3`，源码绑定到该克隆的
  `external/genesis-world`；这只证明依赖可导入，不证明任何 golden case 已运行。
- 在 Gujie 当前 dirty workspace 上运行默认离线 suite：
  `venv/genesis/bin/python -m pytest -q self_improving/sim_adapters/genesis/tests`，结果
  `770 passed, 55 skipped in 230.98s`。跳过项包含显式 opt-in 的真实 Genesis/模型/媒体用例；该结果
  证明大量确定性合同测试可用，但不计作真实物理或 sim-ready golden-line 证据。

## 2026-09-09 Gujie 现存运行产物初检

- 文本任务“场景中有一张桌子。上面放着一个黄色杯子。整体摆放简洁。”的 `TaskOutput.verify()`
  成功复核 1,925 个 manifest 文件，终态为 `physics_passed`。
- 其 Genesis 物理报告记录 `simulation_executed=true`、1,500 steps、桌/杯完整指标通过；最终 orbit
  媒体为 180/180 个唯一解码帧、30 FPS、6 秒。主线程已目视检查 overview：内容为桌面、黄色杯子
  和简洁背景。
- 该产物来自 Gujie dirty workspace，不在当前 Harness run/Registry/ToolResult/portable receipt 中，且
  报告含部署绝对 locator；目前只能作为可接入的真实 Genesis 竖切，不是本任务最终 golden case。
- 图片重建任务“从鼠标原图重新重建并保留 SimFoundry 原始支撑平面”仅到 `scene_built`；preview
  可见重建鼠标，但 physics/final_render 均为 `not_run`，遗留失败为 native-service capability preflight。

## 2026-09-09 并行审计复核

- System 2、portable receipt、validate-v2 和 replay bridge 专项：`756 passed in 24.01s`。
- Asset ledger audit/migration/settle trust 专项：`87 passed`；asset admission：`104 passed`。
- 动态 ledger audit：历史 asset library `audited=66 clean=1 violating=65`；upstream
  `audited=97 clean=0 violating=97`。历史 162 份合计恰好 4,082 条违规。
- `migrate_v3 --dry-run`：预期 exit 1，`migrated=0 failed=163`，确认 migration 与 runtime
  qualification 之间存在 bootstrap deadlock。
- RoboTwin 827 个 representation primary 中 825 个可按 SHA-256 从本机源恢复，2 个 USD 缺失；
  第三方旧副本只有 27 个资产 cohort 可进入 exact-byte staging。
- 详细 claim/evidence/caveat 见 `AUDIT.md`。本次审计未修改 Gujie workspace、ledger 或资产字节。

## 2026-09-09 Codex 自动化可用性预检

- 本机：`codex-cli 0.153.4`，提供 `codex mcp`、`codex exec --json` 和 `--output-schema`。
- 只读、无工具、`--ephemeral` 最小真实 Codex 调用 exit 0，在 6.22 秒内返回精确 JSON
  `{"codex_preflight":true}`；JSONL 包含 start/turn/agent-message/completion 事件。
- 此预检只证明当前身份可启动真实 Codex 非交互回合和取得 usage，不证明 MCP 工具调用、视觉理解、
  路由、Genesis 或 golden E2E。会话 identity 不写入公开进度或 dashboard。
- 官方 Codex MCP 文档确认 STDIO/Streamable HTTP、server instructions、项目级 config、tool timeout 和
  per-tool approval 配置；正式证据仍须由当前仓库 MCP server 的 negotiated clientInfo 与 tool-call
  receipts 对账。

## 2026-09-09 契约与实验设置确认

- 用户确认 S1–S6 公共 TDD seam、exact qualified Skill MCP 和 `genesis.robot_policy@1` 最终门。
- 用户允许新增 optional dependency `mcp>=2.2,<3`，但依赖只能在 P4 的正常 feature 切片中冻结；
  autoresearch 循环本身不安装或升级依赖。
- Autoresearch 已确认 36 个 routing cases、12 个 execution cases、primary
  `/metrics/closed_loop_success_rate`（higher is better），以及最多 30 次或 12 小时的首轮预算。
- 这表示允许开始 TDD，实现完成前仍不能声称 MCP、Genesis robot-policy 或 golden E2E 已通过。

## 2026-09-09 P6 只读资产修复计划 tracer

- public seam：S5 `AssetRepairApplication.plan(AssetRepairPlanRequest) -> AssetRepairPlan`；本切片不实现
  `stage` 或 `promote`。
- 独立双轴 review 先拒绝原实现：任意 caller CAS 可自报 exact、duplicate-key/non-canonical JSON 可通过、
  public plan 的 totals/disposition/next-step/followup 不自洽、Windows drive 与 `.` 可通过、missing 被错误
  导向 reacquire，且 v1 不能表达未来 full baseline。修正后的 focused RED 曾为 import error，随后在第一轮
  GREEN 后以 31 个失败暴露 canonical fixture 和旧错误预期，均保留在本次过程记录中。
- 冻结 canonical fixture：`tests/fixtures/asset_repair_tracer_inventory.json`，5,736 bytes，SHA-256
  `8e54c0d52d16ed5d9de19c55aa37e81012cb28ca6b25443a4285ce1b0daf94e2`。两份 ledger 的 hash/bytes 与
  9/49 violation 已独立复算；主线程也对本机先前 replay materialization 的 14 份 visual/collision GLB
  重算 SHA-256/bytes，全部与 fixture 的 observed fields 一致。
- 证据限定：这些 GLB 没有 committed dataset revision，也尚未由本切片复制进 CAS；inventory 因而明确
  `byte_probe_source=local_unversioned_robotwin_assets`、`byte_probe_revision=null`。Application 只接受
  组装时显式给定的 trusted inventory ArtifactRef，并重验 exact `LocalArtifactStore`、CAS 与 canonical
  bytes；输出固定 `probe_bytes_in_cas=false`，不把本机观测冒充 portable recovery closure。
- tracer 结果：`003_plate` 为 9 条结构违规、2/2 observed digest matches；`071_can` 为 49 条结构违规、
  12/12 observed digest matches。合计 `planned_ledger_count=2`、`planned_violation_count=58`、
  `planned_representation_count=14`，下一步均为 `stage_and_rehash_observed_primary`。
- hash 相同但旧 size 错时进入 stage/rehash + remeasure；hash mismatch 或 missing 都只能
  `build_new_identity_or_retire_asset`。public model 重新推导 disposition、next-step、固定 followup、
  item/selection、三层 totals 与 full-baseline flag，伪造任一绑定均 fail closed；v1 可表达受信的
  `full_baseline`，但本切片没有声称已运行全量。
- 其他攻击覆盖：untrusted/missing/non-canonical/duplicate-key inventory，非 CAS ref、错误 media/schema、
  未知/重复/乱序 asset、重复/乱序 debt detail、availability/observed identity 冲突、绝对/逃逸/反斜杠/
  Windows-drive/`.` path；portable path pattern 同时导出到 JSON Schema。
- focused gate：
  `pytest -q tests/self_improving/harness/test_asset_repair.py tests/self_improving/harness/test_schema_catalog.py --cov=self_improving.harness.asset_repair --cov=self_improving.harness.schemas.asset_repair --cov-branch --cov-report=term-missing --cov-fail-under=100`
  为 `52 passed in 0.73s`，两个新增模块 statement/branch 均为 `100%`；28 份 schema snapshot、Ruff
  和 `git diff --check` 通过。Harness 全域排除已单独记录的 stale replay identity 门后为
  `2091 passed, 19 skipped, 1 deselected in 61.41s`；根套件以同样的单项显式排除运行，为
  `3104 passed, 19 skipped, 1 deselected in 172.43s`。本切片没有改 expected hash 或伪造重签。
- 主张边界：fixture 只覆盖 2/162 ledgers、58/4,082 violations、14/1,101 primary probes；它没有
  重放全量 scanner，也没有把资产字节入 CAS、写 ledger、枚举 loader closure、执行 settle/Genesis 或
  产生 qualification/promotion receipt，因此只能算 E0 contract-tested + local-observation tracer。

## 2026-09-09 P0 readiness 修复与 clean baseline

- RED：删除 PEARL portal 后，公共 `python -m self_improving --json` 仍把它当 required module，退出 1。
- GREEN：保留历史 module identity 供旧消费者发现，但设为 `required=false`；其他 required modules
  不变。focused registry tests `3 passed`，当前共享 checkout 的真实 audit 为
  `ready=true/required_failures=[]`，portal 明确为 `missing`。
- Feature commit：`5ac9108 fix(platform): align readiness after portal removal`。
- Detached clean worktree 根套件：`3016 passed, 19 skipped, 1 failed in 143.26s`。唯一失败仍是
  `text2env.replay@1.0.0` 的 checked-in qualification source identity；clean tree 首个漂移文件是
  `self_improving/harness/__init__.py`，证明问题早于共享工作树的未提交 IMPLEMENTATION_LOG 变化。
- 原 2026-09-02 qualification settings、CAS、解释器、资产和媒体工具仍在本机，但其 delegated cgroup
  已不存在。不能复写三份资格文档或更新期望 hash；最终在实现树稳定后用新隔离 scope 跑真实固定案例
  并重签，或者显式撤销旧 Skill 资格。

## 2026-09-09 三方整合来源台账

- 新建 `docs/integration-provenance/`，以功能级 `integration_id` 分别记录 Bingsheng、Gujie、Yuxin
  的原始来源、第三方上游、Harness 整合责任、固定 commit、dirty snapshot、目标路径和验证状态。
- 初始台账只把当前 Harness/合同和 Yuxin 历史 active tree 标为 `integrated`；Gujie x2env/Genesis
  以及 Yuxin 面向新 Skills 的复用能力仍为 `candidate`。
- 当前没有任何新条目标为 `runtime_pass`：资产 ledger 债务、Codex/MCP 同-run 闭环和当前 Harness
  内的真实 Genesis 最终能力门均尚未完成。
- 根 `AGENTS.md` 已要求后续每个来源整合切片与台账更新同一提交，防止最终集中补写造成来源漂移。
- 三份人员档案已扩展为模块/功能矩阵：Bingsheng 细分 Text2Env 加固、Harness schema/CAS/Registry、
  compile/replay/validate、可信状态、Golden workflow、资产修复、Codex/MCP 与总体整合；Gujie 细分
  x2env/URDF/SimFoundry/Genesis adapter；Yuxin 细分检索、筛选、转换、ledger、catalog 与跨仿真验证。

## 2026-09-09 P1 S1 workflow-start 纵切

- 首个 RED：公共测试导入 `self_improving.harness.golden_run.GoldenRunHarness` 时得到
  `ModuleNotFoundError`；随后只实现 S1 的 `start()`，没有预先实现 submit/read/dispatch。
- 安全边界：调用方提交的是按 SHA-256 绑定的 `harness.world_fact_evidence.v1` 与 registry snapshot，
  不能直接提交或自称 `TrustedWorldState`；Harness 解析 canonical bytes、校验 `user_input` provenance
  和 `request.*` namespace 后自行构建可信状态。
- 输出同时保留逻辑 `state_sha256` 与状态 JSON 的 CAS `state_ref`，并产生不含 planner/provider/model
  身份字段的 `harness.workflow_start_receipt.v1` 及 revision 0 父快照。
- schema catalog RED：新增测试要求 23 个精确 schema 时为 `5 failed, 1 passed`；接线并导出 3 份新
  schema snapshot 后变绿。
- focused 验证：`19 passed in 0.53s`；`golden_run.py` 52/52 statements、8/8 branches，
  `schemas/workflow.py` 100/100 statements、14/14 branches，均为 100%。
- 攻击测试覆盖非 canonical JSON、伪装 fresh observation、错误 namespace、非 UTC clock、非 CAS/错
  digest URI、错误 media/schema、重复或乱序 initial evidence，以及非法 snapshot 时间序。
- 本切片只证明可信父 workflow 的启动证据链；尚不证明幂等恢复、compile/replay/validate 调度、MCP
  transport、Genesis 或最终晋升。

## 2026-09-09 P1 durable start/idempotency 纵切

- RED：跨 Harness 实例重复 `start()` 时原实现没有 durable idempotency authority，且公共
  `GoldenRunConflictError` 不存在；随后以 `(principal_id, idempotency_key)` 为唯一键，在 SQLite
  `BEGIN IMMEDIATE` 事务内执行 query-or-create。
- 相同 canonical request digest 返回原 snapshot，测试用会主动失败的 clock/UUID factory 证明 retry
  没有重新取时钟或分配 identity；同 key 异 request 在新 CAS/identity 前以
  `HARN_IDEMPOTENCY_CONFLICT` 拒绝。
- 恢复路径重验 canonical SQLite snapshot、registry/state/receipt/input CAS closure、逻辑 state digest
  与 start receipt/request binding；非 canonical DB bytes、跨记录 swap、CAS bytes 漂移和不一致 revision
  均 fail closed。workflow/operation identities 同步收紧为 UUID4。
- `GoldenRunHarness` 只接受 exact `LocalArtifactStore`，并已从 `self_improving.harness` 公共 façade 导出；
  protocol lookalike 不能绕过 CAS authority。
- focused 验证：workflow + schema catalog `29 passed in 0.68s`；`golden_run.py` 112/112 statements、
  24/24 branches，`schemas/workflow.py` 99/99 statements、14/14 branches，均为 100%；23 份 schema
  snapshot check 与 Ruff 通过。
- 审阅仍发现两个下一切片 blocker：RegistrySnapshot 目前只被 resolve、未 strict parse/资格闭包验证；
  start receipt 还没有 canonical request ArtifactRef，且 RunSnapshot receipt-head 仍锁死为 start receipt。
  因此本切片不声称完整可重放 workflow 或 exact qualified dispatch。

## 2026-09-09 P1 Registry/request 审计闭包纵切

- RED：把内容为非 JSON 的 CAS 对象伪标为 `harness.registry_snapshot.v1` 时，旧 `start()` 只做
  artifact resolve 便成功；同时 start receipt 只保存 request digest 而无 request ArtifactRef，且
  `RunSnapshot.receipt_head` 永久限定为 start receipt schema。后续契约复核又以 5 个 schema-catalog
  失败锁定 qualification report 已被 Registry 引用、却没有公共 schema identity/snapshot 的遗漏。
- GREEN：新增正式 `RegistrySnapshot`，只含按 MCP tool name 排序且 skill/tool 唯一的 qualified entries；
  每项 strict-canonical 解析 descriptor、qualification、qualification report，并交叉绑定 exact skill、
  MCP tool、implementation、case、regression command、status 与 schema catalog。缺 CAS、坏 canonical
  bytes、伪造版本绑定和未知 schema 分别给出稳定的 unavailable/input-invalid/unqualified/drift 错误。
- 当前真实 fixture 只列 `text2env.compile@1.0.0`；旧 replay qualification 有 source drift，目标
  text2env v2、image/video 和 Genesis Skills 尚未 qualification，因此没有被提前列入可用目录。
- canonical `RunStartRequest` 现在先入 CAS；start receipt 和 parent snapshot 都保存其 ArtifactRef，恢复时
  重算并核对 request、state、registry 与 receipt 全闭包。receipt head 接受精确
  `harness.workflow_<name>_receipt.v1` family，为后续 operation/transition 留出版本化演进空间。
- 验证：`48 passed in 0.94s`；`golden_run.py` 147/147 statements、28/28 branches，
  `schemas/registry_snapshot.py` 45/45 statements、12/12 branches，`schemas/workflow.py` 112/112
  statements、20/20 branches，公共 qualification-report schema projection 7/7 statements，均为
  100%；25 份 Harness schema snapshot、Ruff 与 `git diff --check` 全部通过。projection 没有修改
  已合格 compile 所绑定的 `qualification.py` 源码，compile qualification loader 仍通过。Harness 全域
  排除已单独记录的 stale replay qualification 身份门后为
  `2045 passed, 19 skipped, 1 deselected in 51.35s`。
- 限定：该闭包能拒绝不一致或损坏的调用方快照，但还没有与运行中 Registry/handler 的安装身份取交集；
  P2/P4 必须补这层生产 trust root 后，才可把 snapshot entry 暴露成 exact callable MCP Skill。

## 2026-09-09 P4 官方 MCP 2.2 隔离 spike

- 项目环境未安装 MCP；在隔离临时环境验证 `mcp==2.2.0`，没有修改项目依赖或 lock。官方 v2
  low-level `Server` 与 `Client` 的进程内和真实 stdio 两条最小路径均协商协议 `2026-07-28`，并完成
  exact tool list/call；这只证明 SDK seam，不证明项目 Harness MCP 已实现。
- 选择 low-level Server：adapter 只做 SDK type 与 S1 public type 映射，所有 schema/canonical/receipt/
  idempotency 校验仍由 Harness 负责；不提供 generic `skill.invoke`、alias、latest 或版本范围，也不在
  MCP server 内调用 Codex。
- SDK server 只广告 input schema，不替项目执行入参验证；timeout/cancel 后 durable operation 也不能
  假定回滚，caller 必须凭 command/idempotency identity 从 workflow resource 恢复。
- `uv lock --check` 当前已因历史 metadata 漂移失败，CI 的 editable pip 安装也没有消费 lock。P4 加入
  optional `mcp>=2.2,<3` 时必须同时更新 frozen runtime/CI lane，并记录 mcp、mcp-types 与 negotiated
  protocol 的精确身份；在 S1 `submit/read` 和真实 Registry trust root 完成前不写假 adapter。

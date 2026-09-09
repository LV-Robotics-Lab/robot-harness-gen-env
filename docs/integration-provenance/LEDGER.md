# 功能级整合台账

本文件是 Bingsheng、Gujie、Yuxin 三方工作进入 Golden E2E 的核对清单。状态定义与更新规则见
[README](README.md)。以下是建账时已核实的基线，不把候选实现或历史测试扩大为完成主张。

具体人员工作已精确到模块与功能，分别见 [Bingsheng](sources/bingsheng.md)、
[Gujie](sources/gujie.md) 和 [Yuxin](sources/yuxin.md)。本 Ledger 不重复整张模块矩阵，只跟踪每组
能力是否已接入、验证和晋升。

## 汇总

| ID | 能力 | 原始来源 | 生命周期 | 验证 | 当前结论 |
|---|---|---|---|---|---|
| INT-BS-001 | Harness、Registry、证据链与总体整合基线 | Bingsheng/current history | integrated | blocked | 已有正式组件，当前共享工作树基线仍有资格哈希失败 |
| INT-BS-002 | Golden E2E 合同、门禁与实施审计 | Bingsheng | integrated | contract_pass | 合同已确认，真实 Golden run 未完成 |
| INT-YX-001 | 历史资产检索/复用/入库管线 | Yuxin/HYX | integrated | blocked | 代码与历史已整合，162 份 ledger 有 4,082 条 v3 违规 |
| INT-YX-002 | 新 x2env Skills 的检索与复用能力 | Yuxin/HYX | candidate | not_run | 等待按新 Skill/receipt 合同接入 |
| INT-GJ-001 | 媒体输入、逐帧完整性与 `genenv.*` 输出 | Gujie | candidate | not_run | 固定为候选，尚未进入 Harness Golden run |
| INT-GJ-002 | 标准 URDF closure 与 SimFoundry 导入 | Gujie + third-party | candidate | not_run | 需经 anti-corruption adapter 和资产资格门 |
| INT-GJ-003 | Genesis 构建、物理判据、回放与最终媒体 | Gujie + Genesis | candidate | not_run | 有独立工作区证据，尚非当前 Harness sim-ready 证据 |

## INT-BS-001 — Harness 与总体整合基线

- `lifecycle`：`integrated`
- `verification`：`blocked`
- `origin_owner`：Bingsheng 及仓库历史中已归属的原作者
- `recorded_git_authors`：Bingsheng Xie；早期基础与被整合模块按各自提交保留
  LV-Robotics Lab、BorisGuo、huyuxin、yuhang5090 等 identity
- `upstream_owner`：RoboTwin、SAPIEN 及各被适配项目分别保留自身归属
- `integration_owner`：Bingsheng
- `source_repository`：本仓库历史
- `source_ref`：首次台账 `bf9f24a`；本次模块归属复核所覆盖的实现锚点
  `0f787d0c4d7779dde24a0be68505a304e014e153`
- `dirty_snapshot`：共享工作树有用户修改；不作为固定来源
- `source_paths`：`scene_gen/`、`self_improving/harness/`、相关 `script/`、`tests/`
- `target_paths`：同上
- `integration_method`：原生实现与跨来源总体整合；具体功能仍按 git history 和后续条目归属
- `contract_spec`：`docs/contracts/`、`self_improving/golden_e2e_progress/CONTRACTS.md`
- `verification_evidence`：`self_improving/golden_e2e_progress/RESULTS.md`
- `golden_run`：`none`
- `known_gaps`：detached clean baseline 为 3016 passed、19 skipped、1 failed；唯一失败仍是旧 replay
  qualification source identity，须在实现树稳定后真实重签或撤销
- `last_verified`：`2026-09-09`

### 核对结论

Bingsheng 是 Harness 系统和最终代码整合责任人；这不自动把被整合的 Gujie、Yuxin 或第三方字节
改记为 Bingsheng 原创。当前只可声明组件基线存在，不能声明统一 Golden E2E 已通过。

## INT-BS-002 — Golden E2E 合同与实施审计

- `lifecycle`：`integrated`
- `verification`：`contract_pass`
- `origin_owner`：Bingsheng
- `recorded_git_authors`：Bingsheng Xie `<xieziyin_shangshu@outlook.com>`
- `upstream_owner`：`none`
- `integration_owner`：Bingsheng
- `source_repository`：本仓库
- `source_ref`：合同/边界提交 `ece6c257731381f8a1812c0b8978a6dba3c21554`、
  `55f9ff2d00ff762ce019ed4e5a942e3163cb65bf`、`2bd5e71d711e1d356ac4055bd6765c48dda4b0a9`
- `dirty_snapshot`：`none`
- `source_paths`：`self_improving/golden_e2e_progress/`
- `target_paths`：同上
- `integration_method`：审计、领域建模、合同设计与验收门冻结
- `contract_spec`：`CONTEXT.md`、`CONTRACTS.md`、`SEAMS.md`、`AUTORESEARCH_SETUP.md`
- `verification_evidence`：`AUDIT.md`、`RESULTS.md`、commits `ece6c25`、`55f9ff2`、`2bd5e71`
- `golden_run`：`none`
- `known_gaps`：MCP、Codex 同-run 闭环、x2env Skills、真实 Genesis robot-policy 门尚未实现
- `last_verified`：`2026-09-09`

### 核对结论

合同和验收边界已落库，但 `contract_pass` 只表示设计材料可核对，不是运行时完成证明。

## INT-YX-001 — 历史资产检索、复用与入库管线

- `lifecycle`：`integrated`
- `verification`：`blocked`
- `origin_owner`：Yuxin（历史别名 HYX、huyuxinn）
- `recorded_git_authors`：原来源含 huyuxin `<huyuxin346@gmail.com>`；合并后相关模块还包含
  yuhang5090、BorisGuo、Bingsheng Xie
- `upstream_owner`：各资产源及 RoboTwin 保留自身许可与归属
- `integration_owner`：Bingsheng
- `source_repository`：已移除的原工作区 `/home/jingxiang/yuxin/env-gen-dev` 的归档历史
- `source_ref`：main `ecea99faaec4e082e1fdbc3716ee498cea8dbf87`；working snapshot
  `0b87fed8a2903be8a13f146b9a2b549658a79b74`；history merge
  `899c649179608d950f7ac776a645d21734af2d20`
- `dirty_snapshot`：归档快照身份由 `self_improving/source_inventory.json` 固定
- `source_paths`：资产 provider、selection gate、fetch、coverage、conversion、materialization、ledger、
  catalog admission 与 simulator verification 历史
- `target_paths`：`self_improving/asset_pipeline/active/` 及其 archive/overlay 记录；只读修复规划落点
  `self_improving/harness/asset_repair.py`、`self_improving/harness/asset_staging.py` 及对应 schemas
- `integration_method`：保留来源历史后整合；active tree 为当前规范落点。首个 P6 切片只从组装时
  显式受信且 strict-canonical 的 inventory CAS 分类 observation disposition，不修改历史 ledger，也不
  放松 validator；source population、inventory sample、planned selection 三层 totals 分别绑定，只有
  full scope + 完整 inventory + 完整 selection 才产生 `full_baseline_evaluated=true`。第二个 P6 切片
  只对这两个 tracer asset 建立 path-free source manifest，经 deployment-only root
  binding 逐 member 重算 identity、枚举 GLB loader closure 并写入新 Harness CAS；不写旧 ledger
- `contract_spec`：`self_improving/source_inventory.json`、资产管线内合同与 README
- `verification_evidence`：`self_improving/golden_e2e_progress/AUDIT.md`、`RESULTS.md`；
  `tests/fixtures/asset_repair_tracer_inventory.json`；
  `tests/fixtures/asset_repair_tracer_source_snapshot.json`；
  `tests/self_improving/harness/test_asset_repair.py`（2-ledger E0 plan tracer）；
  `tests/self_improving/harness/test_asset_staging.py` 与
  `docs/evidence/asset-repair-exact-stage-20260910.md`（14-GLB exact-stage tracer）
- `golden_run`：`none`
- `known_gaps`：只完成 2/162 ledger 的 classification 与 14/1,101 primary probes 的本机 exact-stage；
  14 份 probe bytes 已进入本次保留的本机 CAS，但来源仍没有 fixed dataset revision，不能泛化为可移植
  全量来源。162/4,082/1,101 虽已独立扫描复核，但还没有全量 scanner/manifest attestation；历史全量
  仍有 4,082 条 v3 违规，collision/settle/Genesis qualification 和原子 promotion 均未实现
- `last_verified`：`2026-09-10`

### 核对结论

Yuxin 的资产检索与复用工作已经保留并整合，但历史资产证据债务使其不能整体获得 runtime
qualification。Bingsheng 新增的 plan/stage adapter 先把受信 audit observations 映射为 repair
disposition，再把两个 tracer asset 的 14 个已匹配 GLB 逐字节重验并写入新 CAS；它不是 Yuxin 原始
字节，也没有改变任何权威 ledger。该本机未版本化 source manifest 只证明固定 byte closure，不证明
collision、settle、Genesis qualification 或全量 portability。原工作区已不再作为可读取的当前来源，
后续核对应以 source inventory、path-free manifest 和归档 ref 为准。

## INT-YX-002 — 新 x2env Skills 的资产检索与复用能力

- `lifecycle`：`candidate`
- `verification`：`not_run`
- `origin_owner`：Yuxin
- `recorded_git_authors`：huyuxin；当前 active 复合实现另含 yuhang5090、BorisGuo、Bingsheng Xie
- `upstream_owner`：各资产源
- `integration_owner`：Bingsheng
- `source_repository`：本仓库已整合历史
- `source_ref`：asset reuse ABC `4982cc3660d82743b849fd0ba9cbdfd04933e895`；asset sources
  `8c2f058215b4d69d10f15c269148397a9f14ccd2`
- `dirty_snapshot`：`none`
- `source_paths`：`self_improving/asset_pipeline/active/`
- `target_paths`：待建立的 text/image/video x2env qualified Skills 与 acquisition receipts
- `integration_method`：复用 provider/selection/materialization 能力，新增 Skill 输入输出、CAS 与 receipt adapter
- `contract_spec`：`self_improving/golden_e2e_progress/CONTRACTS.md`
- `verification_evidence`：`none`
- `golden_run`：`none`
- `known_gaps`：新生成、精确复用、digital cousin 三种路径尚未在各 x2env Skill 下形成 Golden line
- `last_verified`：`2026-09-09`

### 核对结论

候选复用的是资产检索、选择、物化和入库机制，不复用历史 ledger 的不完整证据。每个新产物必须
重新通过 schema、CAS、来源、许可和运行时资格门。

## INT-GJ-001 — 媒体输入与 `genenv.*` 任务输出

- `lifecycle`：`candidate`
- `verification`：`not_run`
- `origin_owner`：Gujie
- `recorded_git_authors`：yuhang5090 `<1205492990@qq.com>`、LV-Robotics Lab
  `<lv.robotics.lab@gmail.com>`；与自然人 Gujie 的 identity 映射未由仓库证据确认
- `upstream_owner`：媒体解码依赖各自上游
- `integration_owner`：Bingsheng
- `source_repository`：`/home/jingxiang/gujie/gen-env`
- `source_ref`：HEAD `eb0b710581fd7794bc01b447b0f77cb871c8a711`；重点祖先
  `9e8d28c24ca72ebf4001c2460dc4b174cef603c0`、`a8ced2732b48d3eb89e5c40d6cd7fd71b275f178`
- `dirty_snapshot`：当前源工作树含 modified `external/SimFoundry` 与 untracked
  `external/WorldComposer/`；未固定字节不得归入 HEAD
- `source_paths`：`self_improving/sim_adapters/genesis/reconstruct_*`、`media_*`、`task_output*`
- `target_paths`：待建立的 image/video normalization、x2env Skills、Harness ArtifactRef/CAS
- `integration_method`：anti-corruption adapter；映射而非让 `genenv.*` 成为 Harness 权威 schema
- `contract_spec`：`self_improving/golden_e2e_progress/CONTRACTS.md`
- `verification_evidence`：源工作区测试/产物只作为候选证据，详见 `AUDIT.md`
- `golden_run`：`none`
- `known_gaps`：媒体输入到当前 Harness receipt/promotion 的哈希闭包尚未建立
- `last_verified`：`2026-09-09`

### 核对结论

计划复用 Gujie 的媒体 hash、逐帧完整性和任务输出经验；Harness 将新增自己的受信状态、CAS、
ToolResult 与晋升边界。未固定的 dirty workspace 内容不能在完成核对时算入已整合来源。

## INT-GJ-002 — 标准 URDF closure 与 SimFoundry 导入

- `lifecycle`：`candidate`
- `verification`：`not_run`
- `origin_owner`：Gujie（adapter/integration）
- `recorded_git_authors`：yuhang5090、LV-Robotics Lab；与自然人 Gujie 的 identity 映射未确认
- `upstream_owner`：SimFoundry 及资产原作者
- `integration_owner`：Bingsheng
- `source_repository`：`/home/jingxiang/gujie/gen-env`
- `source_ref`：`eb0b710581fd7794bc01b447b0f77cb871c8a711`; committed SimFoundry gitlink
  `9e34ebefcd020583fbb755a8b57268dce78eca26`
- `dirty_snapshot`：源 SimFoundry gitlink 当前有未提交变化，尚未固定
- `source_paths`：`standard_urdf*`、`import_simfoundry_assets*`、`import_simfoundry_scene*`
- `target_paths`：待建立的 portable asset closure、SceneIR/bundle adapter 和 x2env Skills
- `integration_method`：移植 adapter 逻辑，保留第三方归属；输出重新经过 Harness qualification
- `contract_spec`：`self_improving/golden_e2e_progress/CONTRACTS.md`
- `verification_evidence`：`none`
- `golden_run`：`none`
- `known_gaps`：未完成路径可移植性、许可闭包、CAS 入库和真实 Genesis load/build/step 复核
- `last_verified`：`2026-09-09`

### 核对结论

可复用的是 Gujie 编写的标准化与导入适配方法，不是把 SimFoundry 第三方代码改记为 Gujie 或
Bingsheng。任何进入 Harness 的资产必须单独固定来源、许可和完整 closure。

## INT-GJ-003 — Genesis 构建、物理验证、回放与最终媒体

- `lifecycle`：`candidate`
- `verification`：`not_run`
- `origin_owner`：Gujie（adapter/workflow）
- `recorded_git_authors`：yuhang5090、LV-Robotics Lab；与自然人 Gujie 的 identity 映射未确认
- `upstream_owner`：Genesis World
- `integration_owner`：Bingsheng
- `source_repository`：`/home/jingxiang/gujie/gen-env`
- `source_ref`：`eb0b710581fd7794bc01b447b0f77cb871c8a711`; Genesis World gitlink
  `0e74bf392781884ccad765c3f344419c86b872ca`
- `dirty_snapshot`：除已声明 dirty 项外，不以工作树状态代替 commit
- `source_paths`：`position_solver*`、`scene_stabilization*`、`scene_physics_workflow*`、
  `validate_imported_scene*`、`replay_text_half_dt*`、`physics_criteria*`
- `target_paths`：待建立的 Genesis replay/validate Skills、portable receipt、promotion gate
- `integration_method`：移植物理判据与工作流，通过 Harness adapter 调真实 Genesis API
- `contract_spec`：`genesis.rigid_scene@1` 中间门、`genesis.robot_policy@1` 最终门；见
  `self_improving/golden_e2e_progress/CONTRACTS.md`
- `verification_evidence`：独立工作区存在真实物理产物，但尚未绑定当前 Harness run；见 `AUDIT.md`
- `golden_run`：`none`
- `known_gaps`：同-run compile/replay/validate/observation/diagnosis/promotion、可移植闭包和 robot action
  probe 尚未完成
- `last_verified`：`2026-09-09`

### 核对结论

Gujie 的场景构建、bounded solver、稳定化、half-dt 回放、接触/支撑/穿透判据和 physics 后媒体门是
重要候选实现。它们只有在当前 Harness run 中由固定输入和资产哈希驱动、真实 Genesis 执行并通过
最终能力门后，才能把验证状态改为 `runtime_pass`。

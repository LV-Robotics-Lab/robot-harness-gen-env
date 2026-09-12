# 功能级整合台账

## INT-EXPERIMENT-EXECUTION-001 — 同一 Golden 的开发执行身份

- `lifecycle`：`integrating`；`verification`：`contract_pass`。
- `origin_owner`：Bingsheng Harness；`integration_owner`：Bingsheng workstream；
  `recorded_git_authors`：来源固定 Git 记录；`upstream_owner`：Python/SQLite/Pydantic。
- `source_repository`：本仓库；`source_ref`：`9460a4b`，候选 `fbd0441`；`dirty_snapshot`：`none`。
- `source_paths`：Golden aggregate、execution catalog、Registry kernel；
  `target_paths`：`development_execution.py` 与同一 aggregate/catalog/registry/schema。
- `integration_method`：部署显式启用，声明实现/config哈希绑定；开发receipt无正式qualification refs，
  复用同一 SQLite controlled child lifecycle 和父 journal；release不自动降级。
- `contract_spec`：`self_improving/harness/DEVELOPMENT_EXECUTION_CONTRACT.md`。
- `verification_evidence`：S1/Registry 68 passed；一次较宽回归131 passed、1旧compile资格hash失配，
  registry.py变化后保持fail closed，未重签。新模块branch coverage 88%，未达到100%。
- `golden_run`：受控echo仅测state/restart；`known_gaps`：真实实验Skill整合待完成，旧release固定版本保留。
- `last_verified`：`2026-09-12`。

## INT-EXPERIMENT-PACKAGE-001 — 包内入口与隔离 copy-run

- `lifecycle`：`integrating`；`verification`：`runtime_pass`（归档包内执行版本、单 baseline）。
- `origin_owner`：Bingsheng Harness runner、Gujie 场景判据来源；`integration_owner`：Bingsheng workstream。
- `recorded_git_authors`：固定 Git 来源记录独立保留；`upstream_owner`：Genesis、Mesa/LLVM、媒体工具。
- `source_repository`：本仓库；`source_ref`：`9460a4b` runner，Gujie `eb0b710`；实现候选 `eff10bb`。
- `source_paths`：`genesis_scene_cpu_backend.py`、`_genesis_cpu_child.py`、`_genesis_scene_child.py`；
  `dirty_snapshot`：实际执行的小型源码闭包固定在输出包 manifest，不以随后提交冒充已执行字节。
- `target_paths`：`portable_genesis_package.py`、`_portable_genesis_child.py`；
  `integration_method`：包内固定 runner、相对资产闭包，外部声明 Python/Genesis 运行环境。
- `contract_spec`：`self_improving/harness/PORTABLE_GENESIS_PACKAGE_CONTRACT.md`。
- `verification_evidence`：8 tests passed；真实 copy-run
  `/home/jingxiang/bingsheng/generic-portable-copy-nisz5oyp/execution/result.json`，SHA
  `002846c88ee00379141c0a191c4658fce7542e6973e0f34ad47f74dc5f56a3e6`。
- `golden_run`：`none`；`known_gaps`：双dt、controller整合、robot/data未运行；
  profile选择/资源检查后续代码仅离线验证，最终集成 E2E 仍需真实运行。
- `last_verified`：`2026-09-12`。

## INT-EXPERIMENT-ASSET-001 — 三种来源与新资产版本

- `lifecycle`：`integrating`；`verification`：`contract_pass`（真实获取与规范化，physics not_run）。
- `origin_owner`：Yuxin/HYX active provider、Bingsheng procedural generator；
  `integration_owner`：Bingsheng Harness workstream；`recorded_git_authors`：以来源 Git 记录为准。
- `upstream_owner`：agenticsim/openxsim、trimesh/SciPy、Khronos/Cesium，各资产许可单独保留。
- `source_repository`：本仓库；`source_ref`：`4982cc3660d82743b849fd0ba9cbdfd04933e895`、
  `8c2f058215b4d69d10f15c269148397a9f14ccd2`；generator 基线 `9460a4b`；实现 `dc08481`。
- `dirty_snapshot`：`none`；`source_paths`：active provider/web staging、`scene_gen/asset_generator.py`。
- `target_paths`：`self_improving/harness/experiment_assets.py`；`integration_method`：调用现有获取/生成，
  规范化相对路径 URDF/GLB/凸包，保存不可变版本；scale/mass/friction patch 产生新版本。
- `contract_spec`：`self_improving/harness/EXPERIMENT_ASSETS_CONTRACT.md`。
- `verification_evidence`：9 passed，包含真实 local/web/generated；
  `/home/jingxiang/bingsheng/generic-assets-evidence-20260912.xs7UnK/cases`。
- `golden_run`：`none`；`known_gaps`：尚未接 controller/Genesis，质量惯量/摩擦是估计；
  generated 只支持明确的程序化几何，不代表任意图像重建；开发环境需要 SciPy。
- `last_verified`：`2026-09-12`。

## INT-EXPERIMENT-INPUT-001 — 通用实验输入与共享意图

- `lifecycle`：`integrating`；`verification`：`contract_pass`。
- `origin_owner`：Bingsheng Harness；`integration_owner`：Bingsheng workstream。
- `recorded_git_authors`：本切片 Git 提交记录独立于团队责任名。
- `upstream_owner`：Pillow、FFmpeg；Gujie 提供被参考的媒体完整性设计。
- `source_repository`：本仓库；`source_ref`：`9460a4b9ac3825e44b6e6641d5fdafe3034a8a6e`；
  Gujie 参考固定 `eb0b710581fd7794bc01b447b0f77cb871c8a711`，未复制 dirty bytes。
- `source_paths`：`image_ingest.py`、`video_ingest.py`；`dirty_snapshot`：`none`。
- `target_paths`：`self_improving/harness/experiment_inputs.py`。
- `integration_method`：调用既有 ingest，新增 FFmpeg 完整解码 adapter 与共享建议校验。
- `contract_spec`：`self_improving/harness/EXPERIMENT_INPUTS_CONTRACT.md`。
- `verification_evidence`：`tests/self_improving/harness/test_experiment_inputs.py`，5 passed；
  实际 FFmpeg 12-frame 解码。此数不是业务 E2E。
- `golden_run`：`none`；`known_gaps`：controller、资产与 Genesis 尚未接入该输入；
  解释仅单刚体桌面 profile，视频只支持 H.264/yuv420p/CFR。
- `last_verified`：`2026-09-12`。

本文件是 Bingsheng、Gujie、Yuxin 三方工作进入 Golden E2E 的核对清单。状态定义与更新规则见
[README](README.md)。以下是建账时已核实的基线，不把候选实现或历史测试扩大为完成主张。

具体人员工作已精确到模块与功能，分别见 [Bingsheng](sources/bingsheng.md)、
[Gujie](sources/gujie.md) 和 [Yuxin](sources/yuxin.md)。本 Ledger 不重复整张模块矩阵，只跟踪每组
能力是否已接入、验证和晋升。

## 汇总

| ID | 能力 | 原始来源 | 生命周期 | 验证 | 当前结论 |
|---|---|---|---|---|---|
| INT-BS-001 | Harness、Registry、证据链与总体整合基线 | Bingsheng/current history | integrated | blocked | 已有正式组件，当前共享工作树基线仍有资格哈希失败 |
| INT-BS-002 | Golden E2E 合同、门禁与实施审计 | Bingsheng | integrated | contract_pass | 整体多模态 Golden 尚未完成；首条 production image rigid-scene run 见 INT-BS-006 |
| INT-BS-006 | Image exact-reuse 到 Genesis rigid-scene 的首条生产 workflow | Gujie 场景语义 + Genesis/SimFoundry upstream + Bingsheng integration | integrated | runtime_pass | 固定 `1cf8908` 的四个已注册 Skill 在一次 workflow 中完成 replay、fresh observe 与 validate；权限止于 `genesis.rigid_scene@1` |
| INT-YX-001 | 历史资产检索/复用/入库管线 | Yuxin/HYX | integrated | blocked | 两资产 tracer 已通过 exact-stage 与 headless loader runtime；162 份 ledger 总体仍有 4,082 条 v3 违规 |
| INT-YX-002 | 新 x2env Skills 的检索与复用能力 | Yuxin/HYX | candidate | not_run | 等待按新 Skill/receipt 合同接入 |
| INT-GJ-001 | 媒体输入、逐帧完整性与 `genenv.*` 输出 | Gujie | candidate | not_run | 固定为候选，尚未进入 Harness Golden run |
| INT-GJ-002 | 标准 URDF closure 与 SimFoundry 导入 | Gujie + third-party | candidate | not_run | 需经 anti-corruption adapter 和资产资格门 |
| INT-GJ-003 | Genesis 构建、物理判据、回放与最终媒体 | Gujie + Genesis | integrating | runtime_pass（固定刚性场景子集） | Gujie 的有限桌面/双时间步语义已由 Harness 独立实现并进入首条生产 rigid-scene workflow；不泛化为其余场景 |
| INT-GJ-004 | Image/Video exact Skill 合同、legacy mapping 与 Harness-native media ingest | Gujie source + Bingsheng integration | integrating | runtime_pass（image exact-reuse 子集） | Image exact-reuse 已闭合首条 production line；Video 与其余资产路径仍未实现 |

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

## INT-BS-003 — Runtime content manifest 前置

- `lifecycle`：`integrated`
- `verification`：`runtime_pass`（仅本条声明的内容清单capture/部署重验）
- `origin_owner`：Bingsheng（Harness 合同与整合责任）
- `recorded_git_authors`：本切片以提交实际 git author 为准，不由目录推断自然人身份
- `upstream_owner`：Python、Genesis World、Torch、Quadrants 等运行依赖仍归各自上游；本切片不复制其实现
- `integration_owner`：Bingsheng
- `source_repository`：当前 robot-harness-gen-env
- `source_ref`：基线 `a8cf4a4c62011b901bf35793f4e218afae8299c6`；本切片新增独立 Harness 实现
- `dirty_snapshot`：没有从 Gujie dirty worktree 纳入实现或依赖权威
- `source_paths`：既有 `self_improving/harness/artifacts.py` 与 strict schema 合同
- `target_paths`：`self_improving/harness/genesis_runtime_manifest.py`、
  `self_improving/harness/schemas/genesis_runtime_manifest.py`、catalog/export/snapshot 与公共测试
- `integration_method`：独立实现声明根的流式 byte capture、CAS-only read、显式 deployment verification；
  不改变旧 runtime lock/probe v1 语义，不签发资格
- `contract_spec`：`self_improving/golden_e2e_progress/SEAMS.md` 的 S3 前置与 `DECISIONS.md` D019
- `verification_evidence`：公共测试27 pass，147 statements/52 branches 100%；catalog8 pass、94 schema
  snapshots、Ruff/format/diff通过。固定`3662ba6`已真实capture六根166,141成员/8,542,495,955bytes，
  并在第二独立绝对映射和CAS的新进程中重验；精确supporting closure为82,070objects。
  详见`docs/research/genesis-runtime-closure-audit-20260911.md`。另一个staged Genesis probe完成1,000steps，
  但仍使用旧v1 lock；不可把它宣称为新manifest驱动的production execution
- `golden_run`：`none`
- `known_gaps`：只验证声明的成员范围及调用方提供的 process identity；实际 import/native discovery、
  fixed-process binding、production runtime lock/asset qualification 仍未闭合
- `last_verified`：`2026-09-11`

## INT-BS-004 — Manifest 驱动的固定 CPU execution

- `lifecycle`：`integrated`
- `verification`：`runtime_pass`（固定CPU单资产execution，不授予其他权威）
- `origin_owner`：Bingsheng（Harness合同与整合责任）
- `recorded_git_authors`：以本功能提交的实际git author为准
- `upstream_owner`：Genesis World、Python、Torch、Quadrants及其他依赖保留各自归属
- `integration_owner`：Bingsheng
- `source_repository`：当前robot-harness-gen-env
- `source_ref`：Harness基线`92ae72f`；Genesis固定`0e74bf392781884ccad765c3f344419c86b872ca`
- `dirty_snapshot`：不采用Gujie dirty源码作为执行入口
- `source_paths`：现有Genesis asset probe合同与Harness-owned CPU backend
- `target_paths`：`self_improving/harness/genesis_runtime_execution.py`、
  `genesis_runtime_launcher.py`、`_genesis_cpu_child.py`及版本化execution schemas和公共测试
- `integration_method`：独立固定launcher执行声明归档；Landlock只读运行根、独立输出与seccomp socket
  限制；实际module/native/process observation绑定同一次fresh probe，成功receipt最后写入
- `contract_spec`：`self_improving/harness/GENESIS_RUNTIME_EXECUTION_CONTRACT.md`、SEAMS S3、D020
- `verification_evidence`：相关离线gate 37 passed/2 formal skips；97 snapshots。固定`fd50576`正式
  Producer已在两套独立部署完成1,000 CPU steps、完整observation和strict reload；同manifest、
  new runtime lock、request与probe完整关联。独立新进程各重验81,971个supporting CAS对象。
  坏XML真实拒绝且三份失败输出/索引与18partial对象闭合。详见
  `docs/evidence/golden-runtime-execution-20260911.md`及对应acceptance JSON
- `golden_run`：`none`
- `known_gaps`：真实执行覆盖收集中；durable asset qualification、production Skill/MCP、environment
  replay、robot-policy和promotion均未获得本切片权威。CAS纯读只证明内容一致，不产生live capability
- `last_verified`：`2026-09-11`

## INT-BS-005 — Durable Genesis 单资产资格

- `lifecycle`：`integrated`
- `verification`：`runtime_pass`（真实单资产签发及executed恢复；第二CAS独立验收通过）
- `origin_owner`：Bingsheng（Harness合同与整合责任）
- `recorded_git_authors`：以本功能提交实际git author为准
- `upstream_owner`：Genesis World及Python/Torch等依赖保留各自归属；资产来源仍按INT-GJ记录
- `integration_owner`：Bingsheng
- `source_repository`：当前robot-harness-gen-env
- `source_ref`：运行实现`fd50576`，测试与运行证据`5ceead3`；issuer为新增独立Harness实现
- `dirty_snapshot`：未使用Gujie dirty runner或历史pass作为资格来源
- `source_paths`：冻结的`GenesisAssetQualificationV1`与manifest-bound固定execution producer
- `target_paths`：`self_improving/harness/genesis_asset_qualification_application.py`及对应公共测试
- `integration_method`：固定producer成功的live capability才建立trusted executed记录；SQLite幂等与
  恢复重验完整CAS，不注入任意producer/report。独立issuer源码作为CAS证据，qualification最后写入
- `contract_spec`：`self_improving/harness/GENESIS_ASSET_QUALIFICATION_APPLICATION_CONTRACT.md`
- `verification_evidence`：真实RED252.56s已保留；19离线用例通过。正式3 passed/865.35s，覆盖真实
  坏XML失败闭包、直接live资格与无live app重启/UUID幂等、executed断点新app恢复签发；第二CAS
  独立验证通过（61.60s、无Genesis）。真实executed并发恢复与CAS坏字节拒绝也通过；固定提交
  集中验收见`docs/evidence/golden-genesis-asset-qualification-acceptance-20260911.json`。issuer源码SHA
  `b0da423a3f9dc304b0adac98d81cf1c0f9c1cd0c80df3c0f9dfde9ab06c07ef2`
- `golden_run`：`none`
- `known_gaps`：仅单资产给定物性参数的backend_load/collision_probe；不授予production Skill、
  MCP、environment replay、robot-policy或promotion。全部新core 100%覆盖目标尚待收口
- `last_verified`：`2026-09-11`

## INT-BS-006 — 首条 production image → Genesis rigid-scene workflow

- `lifecycle`：`integrated`
- `verification`：`runtime_pass`，仅限固定 mouse exact-reuse 与 `genesis.rigid_scene@1`
- `origin_owner`：Gujie x2env 提供被适配的 scene import、单刚体几何、有限桌面支撑、双时间步和
  physics criteria 语义；固定来源提交的 git author 记录仍归其来源历史
- `recorded_git_authors`：Harness 集成提交 `1cf8908acb83b86b15a7ae8a7c96cb943cc39945`
  记录 Bingsheng Xie `<xieziyin_shangshu@outlook.com>`；不由目录或邮箱推断自然人映射
- `upstream_owner`：Genesis World、SimFoundry、Mesa/LLVM、Python/Torch/NumPy 等各自保留上游归属
- `integration_owner`：Bingsheng Harness workstream
- `source_repository`：Gujie 固定来源 `/home/jingxiang/gujie/gen-env` 与当前
  `robot-harness-gen-env`
- `source_ref`：Gujie `eb0b710581fd7794bc01b447b0f77cb871c8a711`；SimFoundry
  `9e34ebefcd020583fbb755a8b57268dce78eca26`；Genesis World
  `0e74bf392781884ccad765c3f344419c86b872ca`；Harness 集成链 `c68aedc`、`2b07885`、
  `872401b`、`1cf8908acb83b86b15a7ae8a7c96cb943cc39945`
- `dirty_snapshot`：不采用 Gujie dirty runner、modified SimFoundry 或 untracked WorldComposer；正式运行
  只使用固定来源与 Harness 自有冻结 backend/runtime manifest
- `source_paths`：Gujie 的 `self_improving/sim_adapters/genesis/` 场景/布局/物理路径及三份历史
  mouse 仿真视角；这些视角配合 operator-authored exact-reuse proposal，不是 fresh reconstruction
- `target_paths`：`self_improving/harness/{gujie_scene_adapter,genesis_scene_execution_v2,
  genesis_scene_application_v2,genesis_fresh_capture,genesis_fresh_capture_application,
  genesis_scene_assessment,genesis_scene_validation_application,image2env_golden_workflow}.py`、
  对应 schemas、冻结 contracts 与 tests
- `integration_method`：Harness 独立拥有 exact registered Skill、typed request/receipt、CAS、SQLite
  terminal、固定进程、runtime/deployment 验证与跨阶段 lineage；它适配来源语义而不复制 Gujie runner，
  也不把 Genesis 或 SimFoundry 第三方代码改记为 Harness 原创
- `contract_spec`：`self_improving/harness/IMAGE2ENV_GOLDEN_WORKFLOW_CONTRACT.md`、
  `GENESIS_SCENE_EXECUTION_V2_CONTRACT.md`、`GENESIS_FRESH_CAPTURE_CONTRACT.md`、
  `GENESIS_SCENE_ASSESSMENT_CONTRACT.md`
- `verification_evidence`：
  [`golden-image2env-genesis-rigid-scene-workflow-20260911.md`](../evidence/golden-image2env-genesis-rigid-scene-workflow-20260911.md)；
  当前 126 份 committed public schema snapshots；正式 attempt 2 为 `1 passed in 1610.72s`
- `golden_run`：复用已注册 compile UUID `a42a3a83-98ea-4c01-a35f-3003a9109c49`，新 replay
  `bfc30903-0a69-478b-94ed-3ad2c9bbea03`、observe v3
  `90af3aa7-6d77-4c5e-87d8-185b3bea36ea`、validate
  `510c3c7d-c020-4623-b2bc-67e0dd045212` 均为 durable `succeeded`；summary CAS
  `46f21368f0b7dadb0491809b164fa1dac585f79691d68961001f2c928394f5b6`
- `known_gaps`：`genesis.observe@3.0.0` 在独立进程恢复已验证末态、清零速度并执行 0 physics steps；
  它证明 `observed_restored_final_pose`，不证明原 replay 的同进程连续观测。SimFoundry/VLM 提供的质量、
  摩擦等值仍是 estimate；本次运行只验证环境在这些参数下的行为，不是实物测量。输出级迁移在新 CAS
  路径重验 4,115 objects / 1,970,263,053 bytes，但只复制 runtime manifest 索引，未复制约 8.5GB
  deployment member closure，因此不构成完整 runtime relocation。MCP/Codex 同-workflow、Video、三种
  资产路径、promotion 与 robot policy 均未由本条完成
- `last_verified`：`2026-09-11`

### 真实失败与成功边界

Attempt 1 保留了必要失败：replay `00668101-6976-4d45-9c52-c35f78814e19` 成功，旧
`genesis.observe@2.0.0` 在 capture 后 `314.40631s` 才拒绝，超过未修改的 `300s` TTL；observe
terminal 为 failed、零 artifacts，validate UUID 从未进入 intent/invocation/state，且没有 success summary。
Attempt 2 使用全新 UUID，经实际 baseline/half-dt Genesis 执行、v3 fresh capture、validate 和 delivery
成功；delivery 比 expiry 早 `34.50027s`。两段直接 CAS MP4 为 `f3da017b…`、`a7f1bad1…`，fresh
PNG 为 `4cb9b4af…`；相同确定性像素摘要不替代新的 capture event/timestamp lineage。

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
- `target_paths`：`self_improving/asset_pipeline/active/` 及其 archive/overlay 记录；只读修复规划与
  staging 落点 `self_improving/harness/asset_repair.py`、`asset_staging.py`、
  `asset_stage_verification.py` 及对应 schemas；真实 staged-only probe 位于
  `script/probe_staged_robotwin_assets.py`
- `integration_method`：保留来源历史后整合；active tree 为当前规范落点。首个 P6 切片只从组装时
  显式受信且 strict-canonical 的 inventory CAS 分类 observation disposition，不修改历史 ledger，也不
  放松 validator；source population、inventory sample、planned selection 三层 totals 分别绑定，只有
  full scope + 完整 inventory + 完整 selection 才产生 `full_baseline_evaluated=true`。第二个 P6 切片
  对这两个 tracer asset 建立 path-free source manifest，经 deployment-only root binding 逐 member
  重算 identity，并把 14 个 GLB roots 与 7 个 `model_dataN.json` sidecars 组成 21-member union 写入新
  Harness CAS。deep verifier 以显式 expected refs 重建 plan、重验 CAS/sidecar/GLB 引用图；固定 runner
  仅从 CAS 物化后调用真实 RoboTwin `create_actor`，执行 7×900 headless SAPIEN ground-contact steps。
  整条切片不写来源资产或旧 ledger
- `contract_spec`：`self_improving/source_inventory.json`、资产管线内合同与 README；
  `self_improving/harness/schemas/asset_staging.py`、
  `self_improving/harness/asset_stage_verification.py`
- `verification_evidence`：`self_improving/golden_e2e_progress/AUDIT.md`、`RESULTS.md`；
  `tests/fixtures/asset_repair_tracer_inventory.json`；
  `tests/fixtures/asset_repair_tracer_source_snapshot.json`；
  `tests/self_improving/harness/test_asset_repair.py`（2-ledger E0 plan tracer）；
  `tests/self_improving/harness/test_asset_staging.py`、
  `tests/self_improving/harness/test_staged_robotwin_probe.py`；fixed source commits
  `9760a0349864964a527c04d32dfa607455ff8a97`、
  `292d09b7775f75576063f40969c2de078d1c45ab`、
  `bd61a9e1504b1462c1bb24799a38e76f5cdf26e4`、
  `8c1c94afae8292dc94ee1d2b08fb7fb08a94bc06`；主分支逐功能整合提交
  `8bd0d33`、`5b5ae78`、`d9df27b`、`16eb232`，证据/来源同步提交 `52a21ac`；
  `docs/evidence/asset-repair-exact-stage-20260910.md` 绑定 stage result
  `a2e786131bb33a3eacd7afd0403dca45a6353a2f6d54aebc100ecb19fda09f23`、stage binding
  `fcf73ec7851e42654a2264d818bfcc60d091ad3669429ecee7f5b7ccd7e0eb89` 与外部 runtime report
  `c7a0ac85e1425b12041ab0d91833a0bfb15d57ba04f2b5549ff2fe9d1ecafc5d`
- `golden_run`：`none`
- `known_gaps`：只完成 2/162 ledger 的 classification 与 14/1,101 primary GLB probes；7 份 loader
  sidecar 使本次 exact-stage union 为 21 members，但来源仍没有 fixed dataset revision，不能泛化为全量
  portability。162/4,082/1,101 虽已独立扫描复核，仍没有全量 scanner/manifest attestation；历史全量
  仍有 4,082 条 v3 违规。此次 runtime 只证明真实 `create_actor` 的 CAS-only loader closure 与地面接触；
  can-on-plate、settle/runtime qualification、rendered replay、Genesis、robot policy 和原子 promotion
  均未实现
- `last_verified`：`2026-09-10`；fixed source feature
  `8c1c94afae8292dc94ee1d2b08fb7fb08a94bc06`；integrated root feature `16eb232`

### 核对结论

Yuxin 的资产检索与复用工作已经保留并整合，但历史资产证据债务使其不能整体获得 runtime
qualification。Bingsheng 新增的 plan/stage adapter 先把受信 audit observations 映射为 repair
disposition，再把两个 tracer asset 的 14 个 GLB 与 7 个 sidecar 逐字节重验并写入新 CAS；这些
Harness adapter/verifier/runner 是 Bingsheng 的整合工作，不改变 Yuxin 来源能力、RoboTwin 上游归属或
任何权威 ledger。当前最强主张是该 21-member closure 已由真实 RoboTwin `create_actor` 从 CAS-only
物化目录读取并完成 7×900 headless SAPIEN ground-contact probe；它不证明 can-on-plate、qualification、
Genesis、promotion 或全量 portability。原工作区已不再作为可读取的当前来源，后续核对应以 source
inventory、path-free manifest、归档 ref 与固定 runtime report 为准。

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

- `lifecycle`：`integrating`
- `verification`：`runtime_pass`，仅限已经适配的固定 mouse 单刚体/有限桌面子集
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
- `target_paths`：Harness 自有 `gujie_scene_adapter.py`、scene execution/replay/assessment/fresh-capture
  producer/application、版本化 schema、qualification 与 workflow assembly
- `integration_method`：移植并收窄物理判据与工作流，通过 Harness adapter 和固定进程调用真实 Genesis
  API；来源代码不成为 Harness runtime authority
- `contract_spec`：`genesis.rigid_scene@1` 中间门、`genesis.robot_policy@1` 最终门；见
  `self_improving/golden_e2e_progress/CONTRACTS.md`
- `verification_evidence`：固定 `1cf8908` 的首条 production rigid-scene workflow 与
  [`golden-image2env-genesis-rigid-scene-workflow-20260911.md`](../evidence/golden-image2env-genesis-rigid-scene-workflow-20260911.md)
- `golden_run`：Image exact-reuse compile → Genesis replay → restored-final-pose observe v3 → validate
  已在一次 assembly 中真实通过；不包含诊断、状态推进或 promotion
- `known_gaps`：完成的是单 mouse、单刚体、axis-aligned 有限桌面和给定 estimate 参数；通用场景、
  新资产/digital-cousin、Video、Codex/MCP 闭环、完整 runtime deployment relocation 与 robot action
  probe 仍未完成
- `last_verified`：`2026-09-11`

### 核对结论

Gujie 的场景构建、有限支撑、half-dt 回放、接触/支撑/穿透判据和 physics 后媒体门已有一个收窄
子集进入 Harness 的真实 production workflow。`runtime_pass` 只属于该固定输入、固定资产与固定
`genesis.rigid_scene@1` profile；Gujie 其余 x2env 能力仍按候选处理。Harness 的状态、CAS、资格和
执行封闭属于 Bingsheng 集成工作，Genesis/SimFoundry 仍归各自上游。

## INT-GJ-004 — Image/Video exact Skill 合同与 legacy adapter mapping

- `lifecycle`：`integrating`
- `verification`：`runtime_pass`，仅限 image exact-reuse 的首条刚性场景生产线；Video 仍为合同/ingest 状态
- `origin_owner`：Gujie（被映射的媒体/URDF/scene/physics adapter 候选行为）；Bingsheng（Harness 合同
  与映射整合）
- `recorded_git_authors`：来源固定提交实际记录 yuhang5090 `<1205492990@qq.com>`、LV-Robotics Lab
  `<lv.robotics.lab@gmail.com>`；与自然人 Gujie 的 identity 映射未由仓库证据确认
- `upstream_owner`：输入媒体各自权利人、SimFoundry、Genesis World 及被引用资产原作者
- `integration_owner`：Bingsheng
- `source_repository`：仓库内旧 `worktree/gujie` ref 与独立 `/home/jingxiang/gujie/gen-env`
- `source_ref`：旧 ref `a25bc58853db1988aafafe88b544e0573eacbb3c`；实际固定实现
  `eb0b710581fd7794bc01b447b0f77cb871c8a711`；重点祖先
  `9e8d28c24ca72ebf4001c2460dc4b174cef603c0`、
  `a8ced2732b48d3eb89e5c40d6cd7fd71b275f178`；SimFoundry gitlink `9e34ebef`；Genesis World
  gitlink `0e74bf39`
- `dirty_snapshot`：独立来源有 modified `external/SimFoundry`、untracked `external/WorldComposer/`
  及其他本地项；均未计入固定 ref 或 mapping fixture
- `source_paths`：`reconstruct_media.py`、`media_support.py`、`media_checkpoints.py`、`task_output.py`、
  `standard_urdf.py`、`import_simfoundry_scene.py`、`position_solver.py`、`scene_stabilization.py`、
  `validate_imported_scene.py`、`physics_criteria.py`、`scene_physics_workflow.py`、
  `replay_text_half_dt.py` 及来源 tests/evidence
- `target_paths`：`self_improving/harness/schemas/x2env.py`、
  `self_improving/harness/schemas/media_ingest.py`、`self_improving/harness/image_ingest.py`、
  `self_improving/harness/schemas/video_ingest.py`、`self_improving/harness/video_ingest.py`、
  `self_improving/harness/x2env_contracts.py`、`self_improving/harness/schemas/x2env_compile_evidence.py`、
  `self_improving/harness/x2env_compile_closure.py`、`self_improving/harness/image2env_compile.py`、
  `self_improving/harness/image2env_compile_application.py`、
  `self_improving/harness/legacy_genesis_asset_staging.py`、
  `self_improving/harness/schemas/genesis_asset_probe.py`、
  `self_improving/harness/genesis_asset_probe.py`、`self_improving/harness/genesis_cpu_backend.py`、相关 schema
  snapshot、`docs/contracts/X2ENV_IMAGE_VIDEO_SKILL_CONTRACT_V1.md`、
  `docs/contracts/GUJIE_GENENV_ADAPTER_MAPPING_V1.md`、两份 legacy media fixtures
- `integration_method`：strict contract 与 anti-corruption mapping 不复制来源 runtime，不把 `genenv.*`
  设为 Harness 权威，也不注册无 qualification handler。后续 X1 media ingest 是独立 Harness 实现：
  image 由 Pillow 完整解码生成 metadata-free canonical PNG/probe；video 由 injected complete-decoder
  protocol 生成逐帧 ordered manifest/probe；两者均有 pure-read consumer 重算闭包。没有复制 Gujie
  实现字节或调用其内部模型/orchestrator
- `contract_spec`：上述两份正式 contract，以及
  `self_improving/golden_e2e_progress/X2ENV_IMAGE_VIDEO_IMPLEMENTATION_PLAN.md`
- `verification_evidence`：`tests/self_improving/harness/test_x2env_contracts.py`、
  `tests/self_improving/harness/test_schema_catalog.py`、
  `tests/self_improving/harness/test_image_media_{ingest,consumer}.py`、
  `tests/self_improving/harness/test_video_media_ingest.py`、72 份 committed schema snapshots；
  原 16 个 x2env
  snapshot 已用标准 Draft 2020-12 validator 分别 standalone 与 catalog 模式执行。固定来源入口和两份
  现存 output 已独立核对：fresh-image manifest closure 失败，existing-reconstruction image output 只作为
  legacy Genesis baseline candidate。固定 `fe271db55992c6d0880ba2a8903dc233bce2bb3d` 的 CPU image
  ingest/consumer 验收为 focused 38 pass、178 statements/44 branches 100%；固定
  `13e8f01bd963375bdf65d8940434b240cbbb6117` 的 video ingest/consumer 为 focused 74 pass、
  343 statements/104 branches 100%，并以 system FFmpeg 6.1.1 独立完成 18-frame full decode。system
  binary 未进入 qualification。固定 `c817ebf7353d62b7e6fd5df8a65575e93adaa374` 又以 controlled Registry
  candidate 两次生成相同 input-bound exact-reuse package：fresh CAS `12→21→21`，21 个递归 ArtifactRef
  全闭合，handler/verifier 375 statements/92 branches 100%；该执行没有调用或复制 Gujie runtime，Harness
  production compile runtime evidence 仍为 `none`。后续 fixed-source 审计确认，账本所列 exact pass 输出当前
  不存在；最接近 mouse archive 的 15-member URDF bytes可作 representation candidate，但旧 Task/workflow
  absolute locator失效、physics threshold不合当前合同，且 7 个 recorded runtime module SHA 与 `eb0b710`
  0/7匹配，因此不得迁移为当前 Genesis runtime lock或asset qualification。固定 `40bfa213...` 的
  controlled application 只从同一 CAS/SQLite 重验 candidate、exact qualification ArtifactRef 与 deep closure；
  无 live application 重启恢复通过，但没有纳入任何 Gujie runtime 或 production authority。固定 `2ddd5e6`
  只读取 legacy `asset.json`/15 members并经 stable snapshot发布unqualified representation；没有复制 Gujie
  执行代码、旧pass状态或runtime/qualification权威。asset-probe schema只据固定来源行为重新定义
  Harness证据合同；Harness-owned backend 直接调用固定 Genesis public API，不复制 Gujie runner。固定
  `7e145274...` 已对 staged mouse representation fresh 执行无相机 CPU/Newton 1,000-step probe：源8个
  collision members在Genesis中实际合并为1个loaded geom，1,001 rows重算989 contact steps/4,040 contacts、
  最大穿透0.487 mm、正force/impulse与settled；23-object CAS复制到第二绝对路径后无需Genesis/source root
  可strict reload。证据见 `docs/evidence/golden-genesis-cpu-asset-probe-20260911.{md,json}`。该 pass 尚未
  签发issuer/qualification。后续 Harness 切片已经独立封闭 runtime manifest、固定 execution、durable
  asset qualification、registered image compile，以及 scene replay/observe v3/validate；固定
  `1cf8908` 的当前公共 schema 总数为126，完整证据见 INT-BS-006
- `golden_run`：Image exact-reuse 子集已完成首条 production rigid-scene workflow；Video 与新资产/
  digital-cousin 路径仍为 `none`
- `known_gaps`：sealed production video decoder、Video Registry/runtime、三种资产路径、MCP/Codex
  同-workflow、promotion、完整 runtime deployment relocation 与 robot policy 尚未实现；Image 当前输入是
  Gujie 历史仿真视角加 operator-authored proposal，不是自主视觉重建
- `last_verified`：`2026-09-11`

### 核对结论

本条先把 Gujie 固定来源中的媒体/资产/场景/物理语义映射进 Harness contract，随后独立实现了
Harness-native CPU image/video ingest consumers。Image 与 Video identity 不混用；legacy output 适配后固定为
non-ready candidate；media ingest 的 pass 不能扩大为 compile runtime pass。此后只有 image exact-reuse
的固定 mouse 子集进一步完成了 production compile/replay/observe/validate；不得把它泛化为 Video 或三种
资产 lane。Gujie 的 15-frame sample 也不能替代完整 source-video identity。`standard_urdf.py` 的当前能力
是单 rigid link 并明确拒绝 joint，不能据此宣称 articulated support。

## INT-GUJIE-SCENE-001 — 有限桌面与双时间步实际接入

- `lifecycle`：`integrated`
- `verification`：`runtime_pass`，仅限固定 mouse exact-reuse 的 production rigid-scene 子集
- `origin_owner`：Gujie x2env
- `recorded_git_authors`：`LV-Robotics Lab <lv.robotics.lab@gmail.com>`，不推断自然人映射
- `upstream_owner`：Genesis、SimFoundry；软件 renderer 为 Mesa/LLVM
- `integration_owner`：Bingsheng Harness workstream
- `source_repository`：`/home/jingxiang/gujie/gen-env`
- `source_ref`：`eb0b710581fd7794bc01b447b0f77cb871c8a711`；Genesis
  `0e74bf392781884ccad765c3f344419c86b872ca`
- `dirty_snapshot`：`none`；不导入同事 dirty runner
- `source_paths`：`self_improving/sim_adapters/genesis/` 下的 `import_simfoundry_scene.py`、
  `standard_urdf.py`、`position_solver.py`、`physics_criteria.py`、`validate_imported_scene.py`、
  `scene_physics_workflow.py` 及近码合同中的判据来源
- `target_paths`：`self_improving/harness/gujie_scene_adapter.py`、`genesis_scene_replay.py`、
  `genesis_scene_execution_v2.py`、`genesis_scene_application_v2.py`、`genesis_fresh_capture.py`、
  `genesis_scene_assessment.py`、`image2env_golden_workflow.py`、对应 schemas、runner 与 tests
- `integration_method`：适配坐标转换、单刚体完整几何、有限桌面支撑和双 dt 判据；Harness 新增
  CAS、typed scene、纯读重算及连续媒体。当前仅单对象/axis-aligned Box，不声称通用布局搜索、
  多物体重建或 articulated support；不复制 vendor、不沿用历史 pass。
- `contract_spec`：`GUJIE_SCENE_ADAPTER_CONTRACT.md`、`GENESIS_SCENE_REPLAY_CONTRACT.md`
- `verification_evidence`：`docs/evidence/golden-gujie-scene-software-20260911.md` 与
  [`golden-image2env-genesis-rigid-scene-workflow-20260911.md`](../evidence/golden-image2env-genesis-rigid-scene-workflow-20260911.md)；
  baseline/half_dt 为1,001/2,001行并各保留41帧和直接MP4
- `golden_run`：固定 `1cf8908` 的 attempt 2 已把 registered image compile、实际 Genesis replay、
  observe v3 和 validate 串在一次 workflow；权限止于 `genesis.rigid_scene@1`
- `known_gaps`：当前是单对象/axis-aligned Box 与 operator-authored exact reuse；不代表通用布局、
  fresh reconstruction、Video、MCP/Codex、promotion、完整 runtime deployment relocation 或 policy
- `last_verified`：`2026-09-11`

### v1 受约束执行增量

Harness 后续新增固定 OSMesa/llvmpipe scene producer、双 profile 进程限制、完整运行成员观测与
CAS pure reader。固定 Gujie 场景在 manifest `e37a013b…` 下真实通过 1,001/2,001 行、各41帧和
34项检查；详细成功与一次缓存污染失败见
`docs/evidence/golden-genesis-scene-execution-v1-20260911.md`。该增量的来源适配仍归 Gujie，
进程封闭、CAS、执行回执和媒体完整性为 Harness integration；Mesa/LLVM/Genesis 保留各自上游归属。
生命周期仍为 `integrating`：v1缺可达MP4和fresh capture event，且只绑定早期compile candidate，
不能据此改成最终Skill或M1完成。

### Production image compile 增量

Harness 已把固定 Gujie mouse 的三份真实仿真视角图像，通过 deterministic media ingest、operator-authored
proposal、现有 durable asset qualification 和本条有限桌面 adapter，接入已注册
`image2env.compile@2.0.0`。三条 registered terminal、第二CAS/SQLite搬迁及源码/成员漂移拒绝见
`docs/evidence/golden-production-image-compile-20260911.md`。Gujie提供场景/几何/视角来源，Harness
负责typed request/receipt、资格、Registry与恢复。当前不将这些历史仿真视角称为 fresh reconstruction
或自主视觉理解；后续 production replay/observe/validate 闭合只适用于这条 exact-reuse 子集。

### Production replay、observe v3 与 validate 增量

固定 `1cf8908` 新增注册的 `image2env.replay@2.0.0`、`genesis.observe@3.0.0` 与
`image2env.validate@2.0.0`，并由 assembly 消费已注册 `image2env.compile@2.0.0` 终态。Attempt 1
保留旧 observe v2 在 `314.40631s > 300s` 的真实 TTL 失败；attempt 2 以全新 UUID 完成两次时间步
物理执行、独立 fresh capture、validation 与限时 delivery。Observe v3 在单独进程恢复 final pose、速度
清零、0 physics steps，权威为 `observed_restored_final_pose`，不是 replay 进程连续性。Harness 拥有
runtime/qualification/CAS/receipt/state 实现；Gujie 拥有被适配的场景语义来源，Genesis、SimFoundry 与
Mesa/LLVM 保留上游归属。SimFoundry/VLM 的质量和摩擦字段仍是 estimate，不是测量。

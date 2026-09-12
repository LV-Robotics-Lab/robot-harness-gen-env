# 功能级整合台账

## INT-CANONICAL-C08-REPLAY — 同workflow双profile执行

- `origin_owner` / `integration_owner`：Bingsheng Harness workstream，复用canonical Genesis内核。
- `source_ref`：运行接口`013391c`、compile/controller `12b1c3a`、CAS完整性`e14684f`。
- `target_paths`：x2env/replay.py、harness.py、store.py、contracts.py及对应测试。
- `verification`：16项replay/生命周期/schema测试通过；显式runtime替身，不是Genesis整链资格。
- `known_gaps`：实际dual replay、fresh observation/诊断与最终物理/包门尚未闭合。

## INT-CANONICAL-ASSET-CLOSURE — 跨Store receipt缺链回归

- `origin_owner` / `integration_owner`：Bingsheng Harness workstream，新完整性校验。
- `source_ref`：真实preview scope `canonical-asset-preview-20260913.OG1lLA` 与Yuxin源CAS；
  已复制receipt未复制全部引用的失败保留，不将原运行能力扩大到来源闭包通过。
- `target_paths`：x2env/artifacts.py、assets.py及两个测试文件。
- `verification`：实际缺少engine_record已复现；33项相关测试通过；旧state未修补或追认资格。

## INT-CANONICAL-C04-PIPELINE — 同workflow推进local解析与compile

- `origin_owner` / `integration_owner`：Bingsheng Harness workstream，复用canonical独立组件。
- `source_ref`：Store `85a0c2b`、Codex `7dcb40c`、compile `d881ccd`、AssetRegistry `df8ee98`。
- `target_paths`：x2env/harness.py、store.py、contracts.py、resolver.py与相关测试/生成schema。
- `verification`：18项相关测试；实际controller调用resolver/compile，模型预览为显式替身。
- `known_gaps`：真实带资产整链、web/reconstruction controller路由、replay及最终包尚未完成。

## INT-CANONICAL-C10-ASSET — 受限属性修订与geometry定义v2

- `origin_owner` / `integration_owner`：Bingsheng Harness workstream，新不可变版本实现。
- `source_ref`：canonical资产注册`38071da`；新geometry_basis为mesh_world_vertices_faces.v1。
- `target_paths`：x2env/assets.py、asset_revision.py及对应测试。
- `verification`：12项revision测试；换色非新geometry、尺度变化是新geometry回归通过；物理not_run。
- `known_gaps`：仅base_color/mass/friction；其余patch字段明确unsupported；完整fallback仍待controller。

## INT-CANONICAL-C08-COMPILE — 共享SceneIR到相对运行包输入

- `origin_owner` / `integration_owner`：Bingsheng Harness workstream，新确定性编译seam。
- `source_ref`：canonical contracts `4fddb17`、资产`38071da`、运行接口`013391c`。
- `target_paths`：x2env/compile.py及其公共测试；不复制旧实验或核心scene_gen编译器。
- `verification`：5项组件测试；固定已知布局、资产引用与真正bytes修订；运行资格尚未执行。

## INT-CANONICAL-C08-RUNTIME — 真实Genesis与受限资源引用

- `origin_owner`：Bingsheng Harness执行内核与Gujie标准URDF审计；`upstream_owner`：Genesis。
- `source_ref`：Harness `c0236bd66e1988d786f36f61d90471d321d23224` 的
  genesis_scene_cpu_backend/portable_genesis_package；Gujie `eb0b710` standard_urdf；
  Genesis `0e74bf392781884ccad765c3f344419c86b872ca`（版本1.3.3）。
- `integration_owner`：Bingsheng Harness；Git记录身份按原manifest，不推断自然人。
- `target_paths`：x2env/genesis_runtime.py、genesis_child.py；无旧controller/资格导入。
- `verification`：首个2刚体真实smoke和13项runtime测试见RESULTS，增量顶点/seed审计尚待真实重跑。
- `known_gaps`：完整物理profile、canonical流程、独立包copy-run资格尚未完成。

## INT-CANONICAL-C07-TRELLIS — 保留原接口的外部重建接线

- `origin_owner`：Gujie x2env；`upstream_owner`：Microsoft TRELLIS、Meta SAM2/DINO、FlexiCubes，
  软件与权重许可分别记录于研究报告；不推断输入/产物授权。
- `source_ref`：Gujie `eb0b710581fd7794bc01b447b0f77cb871c8a711` → 独立接口
  `ed3b7e2882b2f1eec0ead916d0377776aff93f7f`；TRELLIS独立兼容补丁`750ccb6e7138b7592804148fa21d35961f51e036`。
- `integration_owner`：Bingsheng Harness workstream；记录Git身份不等同于自然人归属。
- `target_paths`：x2env/adapters/reconstruction.py；未复制外部模型实现或权重。
- `verification`：5项adapter测试；原链真实几何/SAM2/规范化证据见研究报告。
- `known_gaps`：canonical adapter GPU调用、同workflow/Genesis/包尚未完成；用户媒体授权待确认。

## INT-CANONICAL-C04-OWNER — 原子head与孤立进程门

- `origin_owner` / `integration_owner`：Bingsheng Harness workstream，新实现，无第三方代码新增。
- `source_ref`：canonical `38071da` Store与`7dcb40c`受管理模型进程回执。
- `target_paths`：x2env/store.py、harness.py及两份公共生命周期/产物测试。
- `verification`：canonical快测151项通过；含真实外部测试进程，不计E2E资格。
- `known_gaps`：包succeeded门、clarification提交尚待后续slice。

## INT-CANONICAL-C05-VISUAL — 原验证器注入managed Codex

- `origin_owner`：Yuxin视觉验证seam；`integration_owner`：Bingsheng Harness workstream。
- `source_ref`：单份迁移`e67f2c6`中的a6_verify.py；原Git作者按来源manifest，不推断身份。
- `target_paths`：x2env/codex.py、asset_advisory.py；复用verify_candidate而非复制验证算法。
- `verification`：13项相关测试；只替身传输，真实视觉判断not_run，物理not_run。

## INT-CANONICAL-C06-ASSETS — 单引擎provider与不可变资产组件

- `origin_owner`：Yuxin资产管线；新规范化/登记为Bingsheng Harness整合实现。
- `upstream_owner`：OpenXSim检索下载、Khronos/Cesium样本，按原文件与运行license evidence分别保留。
- `source_ref`：provider固定来源`4982cc3660d82743b849fd0ba9cbdfd04933e895` /
  `8c2f058215b4d69d10f15c269148397a9f14ccd2`，canonical单份迁移`e67f2c6`。
- `integration_owner`：Bingsheng Harness workstream；`recorded_git_authors`按来源manifest，
  不从Yuxin目录名推断自然人身份。
- `target_paths`：x2env/adapters/yuxin.py、normalization.py、assets.py、store.py及对应测试。
- `verification`：44项相关测试；实际web搜索获取、3真实静态规范化证据见RESULTS。
- `known_gaps`：尚未全部接入同workflow；未跑Genesis、visual selection、copy-run；无新资格。

## INT-CANONICAL-C05-PARTIAL-INTENT — 真实模型失败驱动修复

- `origin_owner` / `integration_owner`：Bingsheng Harness workstream；新实现，无新增第三方代码。
- `source_ref`：canonical `85cc0afb8d17b067c1c1e3d4768effa857bf282b`；Git记录作者不推断自然人身份。
- `target_paths`：x2env/contracts.py、codex.py及对应测试/生成schema。
- `verification`：31项契约/模型测试通过；固定85cc0af真实transport通过但SceneIR拒绝，原证据见RESULTS。
- `known_gaps`：意图未知轴需资产/编译解析；本修复未证明Genesis、矩阵或可迁移包。

## INT-CANONICAL-C01 — Clean 来源基线

- `lifecycle`：`integrating`；`verification`：`contract_pass`（仅Git保护核验，不是运行验收）。
- `source_ref`：bingsheng `ea26524`、integration批准计划`0a8abc0`、补充决定`479388b`；
  source-intake manifest记录全部92个固定refs，未机械merge。
- `origin_owner` / `recorded_git_authors`：按逐ref manifest，不由branch推断身份；原dirty作者未核实。
- `upstream_owner`：各原条目不变；`integration_owner`：Bingsheng Harness workstream。
- `dirty_snapshot`：主目录`0993082727d55ea78b965439a19642e2c46d65f6`、子模块`65980ebf`，
  其余五份见branch-manifests/20260913-other-dirty-protection.json。
- `integration_method`：先保护并恢复核验，再拆用户文档为c94eecd/46a2718；其余过时hunks归档。
- `contract_spec` / `verification_evidence`：progress/CANONICAL_C01_INTAKE_20260913.md。
- `known_gaps`：64个absorb仅表示按C03–C14选择性采用，实际实现/验证/目标SHA待逐功能填入。

## INT-USER-INTAKE-002 — 用户历史证据与文档 hunk

- `lifecycle`：`reference_only`；`verification`：`not_run`（未重跑历史实验）。
- 原作者/记录身份/整合责任同 INT-USER-INTAKE-001；固定来源仍为 archive
  `0993082727d55ea78b965439a19642e2c46d65f6`，没有改写历史证据 bytes。
- 两份 `docs/evidence/` 20260906 dependency/replay-vlm 文档逐字保存；implementation log 的 A042/A043、
  RESULTS 中具日期的两段、change-log 的 20260906 条目、code-map 中仍存在的 compile/receipt 索引及
  platform 中 compile-only 历史段落按 hunk 保留。
- 删除旧资格说明、资产根变化和其他过时状态回退未吸收；具体原 bytes 可从 archive 回查。
- 此项不改变任何产品行为，不是当前 canonical、Genesis 或 qualification 通过证据。

## INT-USER-INTAKE-001 — 原始用户研究文档保护后独立吸收

- `lifecycle`：`reference_only`；`verification`：`not_run`（仅 bytes 与保护快照比较）。
- `source_ref` / `dirty_snapshot`：本地 archive `0993082727d55ea78b965439a19642e2c46d65f6`。
- `origin_owner`：用户原未提交文档，实际作者未核实；`recorded_git_authors`：快照提交记录
  `Bingsheng Xie <xieziyin_shangshu@outlook.com>`，不推断为全部文档原创作者。
- `upstream_owner`：研究中引用的 ASPIRE/相关上游按原文；`integration_owner`：Bingsheng Harness。
- `source_paths` / `target_paths`：`self_improving/studies/ASPIRE/` 的四份研究 Markdown，同路径原 bytes。
- `integration_method`：独立保留研究，不导入策略代码，不将文档叙述计为 canonical 运行。
- `contract_spec` / `verification_evidence`：C01 用户 dirty 分类；
  `self_improving/golden_e2e_progress/CANONICAL_USER_INTAKE.md`。
- `known_gaps`：研究内容未重新执行或重新审计；原 archive 保留。

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
| INT-YX-001 | 历史资产检索/复用/入库管线 | Yuxin/HYX | integrated | blocked | 两资产 tracer 已通过 exact-stage 与 headless loader runtime；162 份 ledger 总体仍有 4,082 条 v3 违规 |
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
## INT-CANONICAL-C02 — 批准验收合同冻结

- origin_owner / integration_owner：Bingsheng 项目；第三方实现本提交未吸收。
- source_ref：`0a8abc01839f2c37a64b9c08f643f48985fb4a79` 的批准实施计划与Q01–Q64。
- target_paths：`CONTEXT.md`、golden progress的SEAMS/ACCEPTANCE/SKILL_CONTRACTS及两份v1 JSON。
- integration_method：逐字固定prompt与数值判据；不改变来源策略或降低替代重建验收门。
- verification_evidence：`tests/self_improving/harness/x2env/test_acceptance_contract.py`，4 passed。
- runtime_verification：not_run；没有新的模型、Genesis或发布能力授予。
- recorded_git_authors：以本提交Git元数据为准，不推断自然人身份映射。
## INT-CANONICAL-C03A — 类型化入口与精确能力注册

- source_ref：批准计划`0a8abc01839f2c37a64b9c08f643f48985fb4a79`与C02`88b4885`。
- origin_owner / integration_owner：Bingsheng项目；本slice为新canonical实现，没有复制第三方执行代码。
- target_paths：`self_improving/harness/x2env/{contracts,capabilities,schema_export}.py`及生成schema。
- verification_evidence：四份contract/registry/export测试，39 passed；当前新包语句和分支100%。
- runtime_verification：not_run；typed handoff测试handler不是实际compile/replay/validate。
- recorded_git_authors：见本提交Git元数据，不据路径推断自然人。
## INT-CANONICAL-C06A — 单次资产复用包搬迁

- origin_owner：Yuxin/HYX来源责任标签；upstream_owner：OpenXSim与各provider原作者，未重归属。
- source_refs：`4982cc3660d82743b849fd0ba9cbdfd04933e895`、`8c2f058215b4d69d10f15c269148397a9f14ccd2`；
  本次从canonical基线内已集成的单份代码搬迁，保留其后续历史。
- recorded_git_authors：固定来源为LV-Robotics Lab；后续a1有BorisGuo、a6有yuhang5090记录，
  不将Git identity映射为同一自然人。
- integration_owner：Bingsheng项目；integration_method：git mv106文件、相对imports、活跃路径适配。
- target_paths：`self_improving/asset_pipeline/active/asset_reuse/`及明确旧路径消费者。
- verification_evidence：packaging/asset_admission/新public import共107 passed/1.71s。
- runtime_verification：not_run；没有将既有license_gate/tier0命中视为canonical资产通过。
## INT-CANONICAL-C04C05 — 单工作流输入与模型建议

- origin_owner / integration_owner：Bingsheng项目；新Store/ingest遵循批准canonical合同。
- source_ref：Codex transport取自`aaf90244fd6edea29dab1cfa35ef11f1968d50ab`
  的`self_improving/harness/codex_experiment_proposal.py`，不导入旧MCP/controller。
- recorded_git_authors：该源记录Bingsheng Xie，身份归属不据邮箱自行扩大。
- upstream_owner：Pillow、FFmpeg、OpenAI Codex，仍各自独立归属。
- target_paths：canonical contracts/store/harness/input/codex与生成schema。
- verification_evidence：71 public seam测试；真实Pillow/FFmpeg/CAS，模型process为明确替身。
- runtime_verification：模型/Genesis尚not_run；从Harness的真实解释小运行在本提交后执行。
- known_gaps：后续资产/compile/replay/validate/package未接，完整C04恢复与最终覆盖门未完成。
## INT-CANONICAL-C05-SCHEMA — 真实transport格式修复

- source_ref：`19f7762`的首次Harness真实解释失败，workflow`0e1ab2dd-0f12-4566-843c-c79aa10627be`。
- origin_owner / integration_owner：Bingsheng项目；upstream_owner：OpenAI结构化输出接口。
- target_paths：canonical contracts/schema_export/codex与生成strict schema。
- verification_evidence：真实400 invalid_json_schema与public schema RED→GREEN；新真实执行待后续记录。
- integration_method：Python类型作为唯一编辑源，不手改输出JSON或伪造模型proposal。
- recorded_git_authors：见Git提交元数据；runtime_verification：修复后真实模型待执行。

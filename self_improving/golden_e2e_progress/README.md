# Golden E2E 进度账本

本目录是 2026-09-09 启动的 x2env → Genesis golden E2E 工作流单一进度事实源。它记录当前
任务、待办、稳定决策、测试 seam、实验参数和已复核结果；不替代 `docs/evidence/` 的不可变运行
证据，也不把 render 当成物理验证。

## 文件地图

- `CANONICAL_MATRIX_V2_PUSH_AUDIT_20260913.md`：本轮实施walkthrough、真实案例与复制运行、最终push审计入口；当前状态以文内明确结论为准。

- `CANONICAL_MULTI_ENTITY_SUPPORT_SPEC.md`：C08/C09动态支撑拓扑、真实面域与多实体设计补全增量契约；尚未实现的能力不升级为通过。

- `CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md`：当前已批准 canonical 实施计划；包含目标模块、
  dirty-byte/全分支收敛、C00–C15 逐功能提交；最新matrix v2以四个简单例与必要交付门验收，单次20分钟，制品发布和 bingsheng
  push 停止点。
- `qualification-matrix-v2.json`：2026-09-13用户主动收窄的当前矩阵；06:25:09Z起、08:25:09Z前必须审计。
  v1原文和失败证据保留，不宣称v1通过；覆盖率实测报告但不再要求精确100%。
- `CANONICAL_X2ENV_MERGE_DECISIONS.md`：当前 canonical x2env 收敛、开发分支整合、验证与
  `worktree/bingsheng` push 计划的 Q01–Q64 澄清检查点；实施计划以其中已确认决策为输入。
- `HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md`：历史实验交接 prompt；记录安全停止现场、
  已通过 normal、被中断 repair、三模态/三资产来源接线表、两类 fallback 和下一会话执行顺序。
- `SIM_READY_RELEASE_PLAN.md`：历史已批准的 M1–M5 计划；2026-09-12 的执行顺序已被当前交接
  prompt 替代，内容仅作范围和证据参考。

- `TASKS.md`：阶段验收清单。
- `TODO.md`：当前 doing/next/blocked/done。
- `DECISIONS.md`：已经接受的稳定架构决定。
- `AUDIT.md`：Bingsheng、Gujie、资产债务和 Codex/MCP 的只读审计结论。
- `DESIGN_OPTIONS.md`：三套 interface 的 depth/locality/seam 比较和推荐混合方案。
- `SKILL_CONTRACTS.md`：九个 x2env Skills、共享 IR、Genesis profile 和错误合同草案。
- `SEAMS.md`：TDD 公共 seam；确认前不写功能测试。
- `AUTORESEARCH_SETUP.md`：实验目标、指标、范围、预算和隔离策略。
- `ACCEPTANCE.md`：E0–E4 证据等级和最终验收门。
- `IMPLEMENTATION_PLAN.md`：按 TDD 和独立提交组织的纵向实施顺序。
- `X2ENV_IMAGE_VIDEO_IMPLEMENTATION_PLAN.md`：六个 image/video exact Skills 的逐 namespace
  compile/replay/validate golden line、adapter/资格/真实运行门和 catalog 路由条件。
- `RESULTS.md`：命令、通过/失败数字、运行证据和不可扩张主张。
- `../../docs/integration-provenance/LEDGER.md`：三方能力来源、固定版本、整合责任和验证状态台账。

## 每次工作顺序

1. 先读 `DECISIONS.md`、`TASKS.md` 和 `TODO.md`。
2. 开始或结束一个切片时更新 `TODO.md`。
3. 新的稳定架构决定写入 `DECISIONS.md`。
4. 测试、真实运行、哈希和失败结果写入 `RESULTS.md`；正式运行证据另存带日期的
   `docs/evidence/` 页面并从这里链接。
5. 公共测试 seam 以 `SEAMS.md` 为准；未确认的 seam 不开始 TDD 功能实现。
6. 路由优化实验遵守 `AUTORESEARCH_SETUP.md`，每次尝试先提交、后测量，并保留失败记录。
7. 从 Bingsheng、Gujie 或 Yuxin 整合功能时，同一提交更新来源台账；dirty workspace 只有在固定
   内容 manifest/hash 后才能成为来源证据。

## 当前阶段

2026-09-13最新matrix v2四简单例全部真实成功，S01已独立复制load/step/新媒体通过；
当前只做最终冻结测试、来源/Git审计和条件满足后的普通push，不创建main PR。
精确运行版本、失败和剩余能力见[本轮walkthrough](CANONICAL_MATRIX_V2_PUSH_AUDIT_20260913.md)及
[机读结果](qualification-matrix-v2-results.json)。最后外部审计路径固定在walkthrough内，
不把4/4当作旧v1资格、全来源成功或机器人能力。固定模型max、已声明本机入口可自行测试。

## Canonical matrix v1启动时基线（历史）

2026-09-13 用户已批准 canonical x2env 合并计划，Qualification Matrix v1 冻结：目标收敛为三个 public Skills、同一
Harness 和同一多模态入口，清理旧 experiment/limited-preview/MCP 活跃路径，并在保护
`worktree/bingsheng` dirty bytes 后整合仍有效的分支能力。Q01–Q64 位于
[`CANONICAL_X2ENV_MERGE_DECISIONS.md`](CANONICAL_X2ENV_MERGE_DECISIONS.md)，完整执行序列和
Qualification Matrix v1 位于
[`CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md`](CANONICAL_X2ENV_MERGE_IMPLEMENTATION_PLAN.md)。
当前 C00 已完成；C01 已冻结来源并保护/恢复核验 bingsheng dirty bytes，整合尚未完成。
用户补充批准推进替代后端并保留 Gujie 原接口，[C07 许可/资源记录](CANONICAL_C07_RESOURCE_BLOCKER_20260913.md)
保留为已发现待解决项，不再暂停主线；
详见 [C01 记录](CANONICAL_C01_INTAKE_20260913.md)。当前 canonical 已实现统一 CLI、三 public Skills 的
实际 typed 调用、资产来源 adapter、Genesis 执行与开发包完成门，并逐功能提交；尚未完成冻结矩阵、
正式 qualification/prerelease 或 push。原 S02 尺度未知 blocked 保留；新固定 109b0c5 的显式设计运行
在 interpret 超时，未进入 grounding 或仿真。S04 随后因结构设计字段关键未知 blocked，尚未联网检索。
组件测试不能升级任一失败。当前细目以 TODO/RESULTS 顶部为准。

## 历史交接基线（下文不是当前停止指令）

下列 2026-09-12 通用实验管线状态仅保留历史依据，不覆盖 2026-09-13 已批准 canonical 计划。

当前会话已按用户指令安全停止单鼠标 M2 收口，不再继续 repair、深验、bootstrap、资格重建或
release 发布。固定 `cc6afa2` 的 normal 已从公共 Harness 入口真实调用内部 Codex、exact MCP 和
Genesis，并以 `development_unqualified / validated_unreleased` 正确提交父 workflow；75-member
开发包、图片、两段连续视频和双时间步 34/34 检查均保留。它没有写正式库，公共命令总 wall 约
3107 秒，也尚未证明包能脱离原 workspace/CAS 在新目录独立运行。

repair 在 compile/replay 已提交、首次 validate 正在执行时被可恢复中断；父 workflow 仍为 active
revision 2，validate 只有 preflight→running，没有 terminal/result/commit。所有进程已经退出，原始
日志、CAS、child runs、checkpoint 和 SQLite 保留，未手改或伪造状态。

下一 active 目标改为“三模态输入＋local/web/generated 三种资产来源的通用实验管线实际接线”。
优先复用现有 Harness controller/journal、CodexBackend、exact MCP、Gujie 固定 adapter、资产 provider
和 Genesis compile/replay/observe/validate；不先重跑旧资格，不新建平行业务系统。每种模态和资产
来源至少做一个小型真实案例，并完成场景/布局与资产版本两类有界 fallback，以及一次复制到新目录
后的真实 Genesis load/step。正式 qualification、全矩阵、162 份历史债务、autoresearch 和 robot
policy 后置。完整事实、入口和执行顺序以
[`HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md`](../../docs/history/golden-e2e/9460a4b9ac3825e44b6e6641d5fdafe3034a8a6e/self_improving/golden_e2e_progress/HANDOFF_GENERIC_EXPERIMENT_PIPELINE_20260912.md)
为准；下列长记录均是历史切片，不能覆盖该 active 状态。

`p1_p6_implementation`

只读审计和第一版契约设计已经完成；用户于 2026-09-09 确认公共 seam、exact Skill MCP、
`genesis.robot_policy@1` 最终门、MCP optional dependency 与 autoresearch 设置。P1/P2 已建立 durable
start/read 与第一个 exact compile operation；P3 候选又在同一 actor-neutral workflow 中完成 exact
qualified replay operation，并以真实 RoboTwin/SAPIEN 把 revision 推进为 0→1→2。该结果仍不是
Genesis 或 promotion。replay qualification 现进一步以逻辑源码根与 exact relative
bytes/tree identity 跨代码安装路径复用：同一 bundle 在第二个绝对路径 strict load 后又真实完成一次
0→1→2；外部 runtime/operator 依赖仍为部署绑定。最终 revision-3 integration 现已在
同一 workflow、同一 Harness 与同一冻结 RegistrySnapshot 中调用 exact qualified
`text2env.validate@2.0.0`，从 revision 2 的受信 replay lineage 独立重算物理报告并推进至 revision 3；
最终固定实现已重新生成 replay/validate 资格，并在另一代码安装路径真实完成 can-on-plate
revision 0→1→2→3 和无 live application 重启幂等。该结果仍只是 legacy RoboTwin/SAPIEN
publishability，不是 promotion、Genesis 或 robot-policy。新增 validate execution/policy/handler/
application/dependency 五模块的 statement/branch 门均已达到 100%。Image2Env/Video2Env 的六个合同
已经过两轴功能验收；加入 observation、control、resource-access、compile-attempt 与 image/video ingest
证据合同、X2 typed compile evidence、input-bound compile v2 与 Genesis asset-probe evidence 后，93 份公共 schema 现在既能整目录
注册，也能逐文件
独立校验。Gujie 现存 evidence 仍只证明 image-derived
existing reconstruction 的 legacy Genesis 候选，不证明 fresh image、video 或 Harness runtime。
P5 exact MCP adapter 现已由官方 MCP 2.2 stdio Client 在真实 RoboTwin/SAPIEN 上完成 raw-text
create、compile、replay、validate revision 0→1→2→3，并在第二个 server 进程重提 validate 而无
SQLite/CAS 增长；workflow-scoped 证据、媒体和 validate input 都只来自 Harness 权威。下一门是外部
Codex planner 回合、fresh observation/diagnosis/promotion，而不是把 transport client 误称为 LLM
路由。P10 已完成 `genesis.observe@1.0.0` strict schema、controlled candidate，并以显式 test-controlled
qualification 在同一 Golden revision 2→3 中闭合 Genesis receipt → fresh ToolResult → trusted claim →
StateDelta → resulting state 和无 live application 重开；所有受控输出均明确
`simulator_executed=false`。controlled evidence policy 还把 Invocation 与 exact
workflow/principal/revision/state/head 绑定。该 binding 未进入 production assembly；真实 Genesis sealed
checkpoint capture、production qualification 和 MCP exposure 仍未完成。下一项 P10 是 advisory/prompt
revision 后的 production Genesis runtime 与下一次 compile 消费：S1 已用共享 turn ledger 实现不可变 advisory/prompt
control receipt，control 只推进 revision/turn/head，可信 state 与 child runs 字节级不变；受控记录固定
`external_agent_executed=false` 且不授予物理或 publishability。durable resource-access receipt 和两个 exact
MCP control tools 已实现；Codex CLI `0.153.4` 已只经 MCP 实际请求四项受控证据，并提交 advisory revision 3
与 prompt revision 4。新的 image/video compile v2 输入以 discriminated attempt 显式绑定 revision、effective
prompt、失败 receipts 与 predecessor package；受控 exact image compile 已消费 video→image frame lineage 并
推进 revision 4→5，六类漂移和旧 v1 输入在 child 前拒绝。该证据只证明受控合同/aggregate/MCP 路径，不证明
模型理解、image runtime 或 Genesis/GPU。X1 image ingest 已能从 CAS 原图完整解码并生成 metadata-free
canonical PNG、严格 probe 与 ImageSourceMedia；独立 consumer 会纯读重算全部字节和镜像字段。它仍不是
image compile handler、qualification 或 Genesis runtime。X1 video ingest 也已用完整 18-frame H.264
样例生成 ordered decoded-frame manifest、probe 与 `VideoSourceMediaV2`，并由纯读 consumer 全量重解码；
15-frame sample、乱序、时序/计数/证据漂移全部 fail closed。该 slice 的 system FFmpeg 只用于独立验收，
没有 production sealed decoder、qualification、Registry/MCP、Genesis/GPU 或视频 compile 主张。X2
已把 proposal、SceneIR、TaskIR、portable catalog、representation、asset qualification、selection、
scene closure、binding、runtime lock、static validation 与 compile receipt 从 opaque schema label
实体化为 self-hashed typed records；纯读 verifier 会消费完整 catalog 和所有 qualification/member/check
evidence，并要求四项 exact static checks。compile receipt/output v2 又绑定 canonical input；controlled
image handler 已在 Registry candidate 中确定性生成单对象 exact-reuse 静态 package，并在 postflight
重验全部闭包；验收记录见
[`docs/evidence/golden-image2env-exact-reuse-candidate-20260911.md`](../../docs/history/golden-e2e/ba92f749f3cd8c2f5ac69c313b7697e8d912b172/docs/evidence/golden-image2env-exact-reuse-candidate-20260911.md)。
controlled durable application 进一步闭合 caller UUID、SQLite terminal 与无 live application 重启。Gujie
legacy 鼠标证据经复核只能作为 representation candidate：指定 pass 目录缺失，最接近归档的 runtime source
identity 为 0/7 匹配且当前 verifier 拒绝旧 physics thresholds。它尚不是 production qualification、Genesis
runtime/replay 或 MCP image path；详见
[`docs/evidence/golden-image2env-controlled-application-20260911.md`](../../docs/history/golden-e2e/e5bf6d9e9eaf87b89330ab82ea2b0a21e26447f4/docs/evidence/golden-image2env-controlled-application-20260911.md)。
15-member mouse bytes 现已由 no-symlink/完整目录检查与稳定 snapshot 迁成 unqualified
`AssetRepresentationV1`；不同绝对路径、重提和源目录并发变化均不改变已验证输出，见
[`docs/evidence/golden-legacy-mouse-representation-staging-20260911.md`](../../docs/history/golden-e2e/d7246ee83c42c1d97d746ebab45c2086541c2ec2/docs/evidence/golden-legacy-mouse-representation-staging-20260911.md)。
Genesis asset probe 现已增加 raw load observation 与逐行 contact schema，并把 public snapshot 总数增至
93；Harness-owned 无相机 CPU backend 已对上述 mouse representation 真实执行 1,000 steps。真实 load
把 8 个源 collision member 合并为 1 个 loaded geom；1,001 行 trace、989 contact steps、4,040 contacts、
最大穿透 0.487 mm 与末态 settling 已由 deep verifier 重算，并在第二绝对 CAS 路径不依赖 Genesis/source
root 严格复验。证据见
[`docs/evidence/golden-genesis-cpu-asset-probe-20260911.md`](../../docs/history/golden-e2e/a8cf4a4c62011b901bf35793f4e218afae8299c6/docs/evidence/golden-genesis-cpu-asset-probe-20260911.md)。
该93-schema阶段尚没有production issuer/资格；后续运行封闭和单资产签发结果如下。资产资格仍不能
直接替代production Registry/MCP的Skill资格。
runtime manifest 前置已把声明根的完整字节写入 CAS，并提供纯读与显式部署重验三方法；其阶段有94份
公共 schema。它的 `declared_byte_closure_only` 不证明实际运行进程只使用这些根。
固定`3662ba6`已真实capture166,141成员/8.54GB声明内容，并在第二独立绝对路径/CAS的新进程全量
重验；staged fresh Genesis probe另完成1,000steps，但仍绑定旧v1 lock。运行记录见
[`runtime closure`](../../docs/history/golden-e2e/92ae72f86f9418a0a45b99cc3af78cb1d733a488/docs/research/genesis-runtime-closure-audit-20260911.md)。下一步必须将manifest、
实际运行入口与probe回执显式绑定，不能把两次各自通过的执行拼成尚不存在的资格链。
固定`fd50576`新增受约束CPU launcher与execution request/observation/receipt，当前97份schemas。
正式Producer已在两套独立部署完成1,000steps及strict reload，固定提交集中功能验收通过；
launcher/child覆盖目标仍有明确缺口。固定`5f78e62`的durable qualification application已通过真实
签发/重启/幂等及executed恢复三项formal（865.35s），第二CAS/SQLite无Genesis纯读通过；独立
集中验收通过，真实并发恢复与CAS坏字节拒绝也通过。证据见
[`单资产资格`](../../docs/history/golden-e2e/d8ab41f33501c8b59ce15dad53f76ea876416cf7/docs/evidence/golden-genesis-asset-qualification-20260911.md)与
[`runtime execution`](../../docs/history/golden-e2e/5ceead34d8e0c89d6280cf987fd204c408317ae2/docs/evidence/golden-runtime-execution-20260911.md)。公共合同见
[`固定 CPU execution`](../../docs/history/golden-e2e/fd5057625d7a5e986a49867fdf1d540f027c5431/self_improving/harness/GENESIS_RUNTIME_EXECUTION_CONTRACT.md)。
P6 继续修复资产 staging；autoresearch 只在其
冻结 benchmark 可以
真实运行后开始。

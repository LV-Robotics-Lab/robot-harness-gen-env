# Self-Improving 平台边界

## 当前受限 image preview 的控制关系

当前产品入口是 Harness，而不是 Codex 或 MCP：

```text
用户 / API / CLI
  -> Harness service + workflow controller
  -> managed CodexBackend
  -> 当前任务分配的 exact MCP Skills
  -> Harness 校验结果、提交状态、重试/恢复并决定发布
```

Harness 在调用 Codex 前持久化任务，每次从当前权威状态构造有界上下文、动作集合和预算。
Codex 只提供输入理解、规划、视觉判断、诊断与修改建议；它不拥有业务状态、Skill 执行或发布资格。
`codex_mcp_acceptance.py`、`codex_mcp_process.py` 和 scripted MCP client 仍可证明协议或组件行为，但属于
历史/组件验收，不能替代从 Harness 入口发起的产品案例。受限 image exact-reuse 的正常案例和诊断修复
案例仍需按当前里程碑真实跑通，本页不提前声明通过。

固定 `1cf8908acb83b86b15a7ae8a7c96cb943cc39945` 已闭合首条 production image exact-reuse →
Genesis rigid-scene workflow。Assembly 消费已注册 `image2env.compile@2.0.0` 终态，随后在同一次
workflow 调用已注册 `image2env.replay@2.0.0`、`genesis.observe@3.0.0` 和
`image2env.validate@2.0.0`。Attempt 2 真实执行 baseline/half-dt Genesis、fresh capture、validate
和限时 delivery，四个 durable terminal 均为 succeeded，最终 profile 为 `genesis.rigid_scene@1`。
完整记录见[M1运行证据](../../docs/evidence/golden-image2env-genesis-rigid-scene-workflow-20260911.md)。

Attempt 1 没有被抹去：replay 成功后，旧 observe v2 在原 capture 后 `314.40631s` 拒绝，超过未修改
的 `300s` TTL；observe 零 artifacts，validate 未预留，success summary 未签发。Attempt 2 使用全新
UUID，正式用例 `1 passed in 1610.72s`，delivery 距 expiry 还有 `34.50027s`。两段直接 CAS MP4、
fresh PNG、完整轨迹、report、receipt 与跨阶段 lineage 都进入 summary。新 CAS 绝对路径又以 fresh
process 重验4,115个输出证据对象、1,970,263,053 bytes；三个 runtime manifest 索引虽已复制，其约
8.5GB member deployment 未复制，所以该结果证明输出证据迁移，不证明完整 runtime relocation。

Observe v3 不是旧帧换时间：独立固定进程重载场景，恢复已验证 final pose、将六自由度速度清零，
执行0个physics steps后实际渲染，并在 render 返回后记录 UTC capture event。其权威明确为
`observed_restored_final_pose`、`process_continuation=false`；确定性姿态可能生成与旧帧相同的像素
摘要，但新的 capture result、时间和 replay lineage 必须独立闭合。

场景导入、单刚体完整几何、有限桌面支撑、双时间步和 physics criteria 语义来自固定 Gujie x2env
来源。Harness 独立拥有 Skill/qualification、typed schema、固定进程、CAS、SQLite terminal、receipt
与 workflow lineage；Genesis、SimFoundry、Mesa/LLVM 仍归各自上游。SimFoundry/VLM 给出的质量、
摩擦等参数是 estimate，本次物理执行只验证场景在这些输入值下的行为，不是实物测量。当前图像也是
Gujie 历史仿真视角配合 operator-authored exact-reuse proposal，不是自主视觉重建。

底层证据仍可分别追溯到[scene execution v1](../../docs/evidence/golden-genesis-scene-execution-v1-20260911.md)、
[Gujie scene software run](../../docs/evidence/golden-gujie-scene-software-20260911.md)和
[单资产资格](../../docs/evidence/golden-genesis-asset-qualification-20260911.md)。首条 rigid-scene
workflow 不等于已开放服务：Video、三种资产路径、Codex/MCP 同-workflow、状态推进、promotion 与
robot policy 仍未由 M1 完成。

`scene_gen/` 是稳定信任边界，负责把受限文本编译成可验证、可回放、哈希绑定的场景包。`self_improving/` 是消费者和编排层：它可以选择环境、组织采集与训练、评估失败、写诊断和记忆、决定是否晋升，也可以调用资产与仿真适配器；它不能伪造或跳过 `/gen-env` 的物理门控。

| 层 | 目录 | 写什么 | 不写什么 |
| --- | --- | --- | --- |
| 稳定核心 | `scene_gen/` | schema、parser、grounding、solver、builder、validator | 策略、训练循环、仿真器特定编排 |
| Harness 契约 | `self_improving/harness/` | 严格审计记录、Text2Env Skill 输入输出、权威载荷引用、公开 schema 快照、通用 Registry 与 compile adapter | 复制 `scene_gen` 载荷、在 Registry 核心写 Text2Env 分支、MCP 自有类型或发布决策 |
| 场景编排 | `self_improving/stage5/` | designer/critic/grounding agent、prompt、MCP-lite | 核心物理判定的替代实现 |
| 闭环 | `self_improving/alchedata/` | collect/train/evaluate/diagnose/transfer、失败记忆、promotion gate | 大规模 runs、checkpoint、下载缓存 |
| 资产 | `self_improving/asset_pipeline/` | 发现、ingest、ledger、catalog 对接、迁移 adapter | 第三方 mesh 与渲染产物 |
| 同学交接材料 | `self_improving/contributor_notes/` | 历史设计、运行说明、任务交接及来源哈希 | 当前运行命令或新的能力承诺 |
| 仿真适配 | `self_improving/sim_adapters/` | 薄脚本、schema、可隔离测试 | 完整复制 IsaacLab 或候选仓库 |
| 历史原型 | `self_improving/legacy/robotwin_text2env_alt/` | `text2env.tabletop.v0` 来源快照、修复工具、有限 smoke evidence | 覆盖当前 Stage 5 或成为新功能入口 |
| 被忽略的工作台 | `self_improving/asset_pipeline/workbench_snapshots/` | Yuxin 的 asset-spike、nightwatch、one-off 源码与笔记快照 | 直接作为当前运行入口 |
| 验收归档 | `self_improving/validation_evidence/`、`workspace_archives/` | 小型结构化证据、复现脚本、完整文件哈希及 Release 指针 | 把 cache、嵌套 Git 元数据或第三方 mesh 直接塞进主树 |
| 呈现层 | `apps/pearl_evidence_portal/` | PEARL 门户、浏览器报告子集、构建测试 | 生成验收结论或把页面文案当运行证据 |
| 外部项目 | `external/` | 钉住子模块 commit | vendor copy |
| 历史 | `self_improving/legacy/` | 只读来源快照 | 新功能 |

`python -m self_improving --json` 只检查这些源码是否到位以及子模块是否初始化，不导入 GPU 框架、不启动仿真器。来源工作区、提交、归档分支和排除项在 `self_improving/source_inventory.json`，它是清理旧副本前的审计入口。

下文中以 PR1、post-close 封存研究或 `ab03859` 为主语的历史结论，只固定到 clean commit
`ab03859`。之后当时观察到的并发 commits `c365874`、`8d9a01c`、`587b49f`、`0e6716a`
未进入那次历史研究复核；该锚点不限定其后明确标为 2026-09-10 或“当前”的集成状态。

2026-09-10 当前态以固定绿色起点
`e67b656ea97044da5b09ee8323c48e752cd88c76` 加 P2 compile-only submit 候选为准；
下述 55-schema 计数不归属于 `ab03859` 的 14-schema 历史快照。

Harness 在固定 `1cf8908` 公开126个以`$id`标识的JSON Schema，完整目录见
[schema说明](harness-schema-tranche.md)。下述14、55、72、91、93、94、97均是相应固定提交的历史
计数，不代表当前目录。其中早期x2env合同阶段的72个入口构成为：PR1 的 14 个入口保持不变，随后增加
descriptor v2、validate v2 input/output，以及 qualification report、资产债务/修复、registry snapshot、
replay VLM、run start/read/snapshot 和 workflow start receipt 等 12 个入口，再加上 asset source snapshot、stage
request 与 stage result 三个入口；P2 再增加 command、operation snapshot、qualified execution、trusted
tool receipt、ToolResult、StateDelta 与 workflow operation receipt 七个 actor-neutral 入口；当时的
Image2Env/Video2Env contract tranche 又增加 12 个 Skill I/O、两种 source media、asset decision 与
candidate environment package 共 16 个入口。descriptor v2 明确区分
内容位级确定性与证据不变量可复验。descriptor v1 保持冻结，
compile 仍用 v1，replay 用 v2；两版不能静默转换。`ArtifactRef.schema_version` 仍指向既有
`robotwin.*` 权威载荷，Harness 不重新定义其内部格式。schema 之外已有本地 digest-checking artifact
resolver、callback-driven `RunRecorder`、SQLite WAL append-only `SQLiteEventJournal`、CAS
`PackageStore`、通用 qualified-version `SkillRegistry` 与 `Text2EnvCompileHandler`。固定 `ab03859`
关于 replay/validate 未接通的描述只是历史快照；当前 replay 已有 candidate handler、固定资格深验证与
production-only registration policy。仓库保留 2026-09-02 的历史 checked-in pass bundle 与真实
900/120 replay 记录；P0+P2 集成后旧 bundle 先因源码 identity 漂移被严格拒绝，当前固定树随后已用
全新外部资源真实重签，并通过 direct/kernel 0/900/120/120/12、14-schema exact gate 与 fresh-CAS
strict reload。随后 qualification source identity 改为逻辑 `replay_distribution` 根、relative exact
bytes/file/tree hash；同一 bundle 已在第二个绝对安装路径 strict load，并由 production application
真实重跑 Golden 0→1→2。RoboTwin/解释器/media/cgroup 仍是 deployment-local binding；证据见
[`docs/evidence/golden-portable-replay-20260910.md`](../../docs/evidence/golden-portable-replay-20260910.md)。
从 repo root 执行 `PYTHONPATH=. python -m script.export_harness_schemas --check`，显式绑定当前
checkout 并锁住 committed snapshot；核心资格模块强制 100%
语句与分支覆盖。compile 的 asset admission 仍发生在 solve 前且失败不回滚；跨 run promotion、
identity-frozen resumable EvaluationRun 与 MCP adapter 仍未闭合，契约保持 `Status: Proposed`。

Image2Env/Video2Env 最初六项 `@1.0.0` exact metadata 仍是合同入口，不能与当前 native image v2
运行线静默互换。固定 `1cf8908` 的 image exact-reuse 子集已注册 `image2env.compile@2.0.0`、
`image2env.replay@2.0.0`、`genesis.observe@3.0.0` 与 `image2env.validate@2.0.0`，并完成一次
production assembly。Compile 只生成绑定已资格资产的静态 scene；replay 才执行真实 Genesis collision、
baseline/half-dt steps 和顺序媒体；validate 只授予 `genesis.rigid_scene@1`。Gujie `genenv.*` 仍须经
legacy adapter 映射，旧 physics/render/status 不能直接晋升。Video 的 production decoder、Registry、
replay 和 validate 仍未实现。正式字段见
[Image/Video exact Skill 合同](../../docs/contracts/X2ENV_IMAGE_VIDEO_SKILL_CONTRACT_V1.md)，来源映射见
[Gujie adapter mapping](../../docs/contracts/GUJIE_GENENV_ADAPTER_MAPPING_V1.md)。

X1 media ingress 已分别闭合 image 与 video 的消费前证据。image 路径用 Pillow 从 exact CAS 完整解码
JPEG/PNG/WEBP，生成 metadata-free canonical PNG/probe，并由 pure-read consumer 重算；video 路径用
injected complete-decoder protocol 生成逐帧 index/DTS/PTS/duration/bytes 绑定的
`VideoDecodedFrameSequenceV1`、probe 与 `VideoSourceMediaV2`，consumer 再全量重解码。新 video schema
不复用 fallback 专用 `harness.video_frame_sequence.v1`。受控 18-frame H.264 及 system-FFmpeg 独立验收
已通过，但系统 binary 没有进入 sealed dependency/qualification，v2 media 也尚未接入 video compile。

Legacy mouse 的 15-member URDF closure 现可先冻结为 portable、unqualified `AssetRepresentationV1`，再由
Harness-owned `GenesisCpuAssetProbeBackend` 做 asset-level 物理 probe。固定 `7e145274` 的真实运行不创建
camera/renderer：初态后执行 1,000 个 CPU/Newton steps，产出 raw load observation、1,001 行 canonical
contact trace 与七项 report checks。源端的 8 个 collision OBJ 在 Genesis 中实际合并成 1 个 loaded geom；
不能把 source member 数当 runtime shape 数。989 contact steps、4,040 contacts、最大穿透 0.487 mm、正
normal force/impulse 与末态 settling 均由 pure-read verifier 从 23-object CAS 重算；同一 CAS 在第二绝对
路径、无 Genesis/source root 的进程中再次通过。该固定 `7e145274` pass 本身不是
`GenesisAssetQualificationV1`；后续 manifest-bound execution 和 durable application 已用 fresh run
补签给定物性参数下的单资产资格，并由当前 image line 消费。质量、摩擦等来源字段仍是 estimate，
load/contact pass 不是实物测量。证据见
[Genesis CPU asset probe](../../docs/evidence/golden-genesis-cpu-asset-probe-20260911.md)和
[单资产资格](../../docs/evidence/golden-genesis-asset-qualification-20260911.md)。

`genesis_runtime_manifest.py` 现在提供运行时内容清单：capture 对六个声明根完整枚举，逐文件流式写入
CAS，重读确认后最后发布 manifest；read 只重验 CAS，verify-deployment 则额外重验显式部署映射及
调用方提交的 process facts。换安装位置不改变清单身份。这个 interface 不负责发现漏掉的动态依赖，
也没有单独证明运行进程只加载清单成员，因此其权威明确为 `declared_byte_closure_only`，不能直接
签发资产资格。固定`3662ba6`的早期部署验证覆盖166,141成员与第二独立绝对路径；随后固定 launcher、
实际 module/native observation、asset qualification、scene replay v2 和 fresh-capture runtime 分别建立
自己的执行与资格闭包。M1 输出证据能换 CAS 根重验，但完整 runtime member deployment 没有在本次
输出迁移中复制。历史物化边界见
[运行时物化记录](../../docs/research/genesis-runtime-closure-audit-20260911.md)。

P3 的公共 `GoldenRunHarness.submit` 当前可在同一 workflow 依次调度 exact qualified
`text2env.compile@1.0.0` 与 `text2env.replay@1.0.0`。两者都从同一 RegistrySnapshot、live production
descriptor/qualification 和 Skill-owned evidence policy 冻结执行；command、controlled child、ToolResult、
唯一 StateDelta、workflow receipt 与 revision 0→1→2 head 进入同一 CAS/SQLite closure。generic execution
catalog 不含具体 Skill 分支，compile/replay 各有独立 binding 与 policy；opaque factory product 在 catalog
消费时重验交叉绑定。replay 只接受当前受信 state 中由同一个 compile receipt 产生且与 parameters 完全一致
的 package/catalog，只晋升 `replay.runtime_evidence`，媒体不是 fresh observation。四个 child/parent 恢复
窗口保持与 compile 相同的保守语义。真实同-run 证据完成 0/900/120/120/12，但这些边界不证明任意外部
副作用 exactly-once，也没有接通 validate、MCP、Genesis 或 promotion。当前 replay bundle 的源码
资格可跨代码安装绝对路径复用：loader 以调用方 local root mapping 重走三轮 nofollow closure；只有
精确等于该映射根的 worker `PYTHONPATH` 会逻辑归一，其他环境与外部 operator 依赖仍严格绑定。

System 2 的 compile 竖切不再只信模型自报或 artifact 外形：planner prompt/raw decision/receipt、
精确 base world state、PlannerContext、Skill qualification、Invocation/RunState、effective catalog、
solver 结果、package 四成员与静态 validator 都由 dispatcher 从同一 CAS 重读并交叉重算。
version 2 及后续 state 必须携带从 version 1 开始的完整 history authority；成功 receipt 才能产生
`StateDelta`，blocked/failed 明确为 `state_delta=null`。这条路径只晋升 catalog/package 非物理事实；
它没有把 compile static pass 写成接触、稳定性或可见性证据。当前测试含真实 production compile
assembly 的本地生成资产路径，但使用临时 qualification，不能替代 checked-in 资格或 SAPIEN replay。

字段边界、状态机、快照与未实现范围见 [Harness Schema Tranche](harness-schema-tranche.md)；
逐项实现和验证证据见 [PR1 实现报告](../../docs/contracts/HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md)。

早期 Golden control schema 仍以 external-agent advisory 和 prompt revision 命名，并支持两类不改可信
状态的 control entry。
它们不伪装成 Skill/ToolResult，也不生成 StateDelta；与 Skill 共用同一个 SQLite turn 仲裁，提交后只推进
revision、turn sequence 和 receipt head，state ref/hash 与 child runs 保持不变。advisory 只能引用本
workflow 的失败 receipt、diagnostic 与仍新鲜的 Genesis observation，且固定为 `advisory_only`；prompt
revision 必须显式绑定原 prompt、advisory、失败/package lineage 与 fallback frame manifest。该受控纵切
仍记录 `external_agent_executed=false`。后续已增加独立 append-only `ResourceAccessReceipt` journal：MCP
artifact read 返回原资源和 server-owned receipt，但不推进 revision/state/head；advisory 必须用这些
receipts 精确覆盖 source prompt、failure、observation 和 diagnostic。Codex CLI `0.153.4` 已实际发起四项
读取并提交 advisory/prompt controls，第二进程重提无 SQLite/CAS 增长。receipt 只能证明 server delivery，
`transport_acknowledged=false` 与 `model_understanding_claimed=false`，不能冒充模型理解或 trusted fact。

PromptRevision 之后不能再直接调用旧式 Skill。image/video compile 现在有 v2 输入：`initial` attempt 与
`prompt_revision` attempt 是严格 discriminated union；后者必须携带 current revision、effective revised
prompt、完整 failure receipts 与 predecessor package。Golden aggregate 在 command CAS、turn reservation
和 child 分配前再核对 target Skill、workflow authority 及 video→image selected frame。受控 MCP 纵切已让
exact image compile 从 revision 4 推进到 5，六类 lineage 漂移与旧 v1 输入均零副作用拒绝。两个 x2env
compile Skill 尚未资格化，故 descriptor 在首次资格前切到 v2；旧 v1 仅保留历史解析。该 preflight
application 最终受控 blocked，不是 image runtime、Genesis 或 publishability 证据；已资格 text compile v1
也没有被改写。

ASPIRE E0–E2 快照 `1180aef` 的离线、自包含回归是 595 passed、6 skipped；这不是后续 Registry/compiler/asset/Stage 5 follow-on 的当前绿灯。该快照的 skip 只对应未纳入 Git 的 Isaac/SceneAgent/媒体/报告原始包或本机未安装的 SAPIEN 物理运行时。完整命令与时间边界见 `self_improving/README.md`。

后续只读诊断快照 `28333de` 的默认 pytest 因两个同名 `test_registry.py` collection error；
`--import-mode=importlib` 为 155 passed / 1 failed，剩余 failure 是 compiler 的 `parse` stage 与历史
`scene_spec_validation` 期望不一致。Harness 自身在该快照已回到 100% statement/branch coverage，
但这不抵消全仓 collection/行为回归。

E1e 收尾快照 `910ccb1` 另验证 Harness 专项 74 passed、1291/1291 statements 与 312/312 branches；
后续 `51447da` 的独立 clean-archive 复核是 76 passed，但 coverage 为 99.88% 并未过 100% 门；
默认 pytest 与完成 submodule 初始化后的平台脚本也都在两个同名 `test_registry.py` 上 collection
error。这些是分时快照证据，不等于重新跑通默认全仓/平台矩阵。

`148001d` clean archive 上 importlib 根测试为 201 passed / 0 failed，说明 CLI failure-stage 漂移已
闭合；默认 collection error 与 Harness 99.88% coverage gate 仍未闭合，所以该快照仍非全绿。

post-close `ab03859` clean archive 上 Harness 为 133 passed / 99.91% coverage；默认 collection 与
同一 coverage branch 仍失败。qualification/packaging/RunStore 是独立 prerequisites，不执行
development/clean qualification，不具原子 promotion/rollback，也不闭合 replay media identity。

2026-08-31 的后续切片已把 compile qualification/production CLI、独立 validate handler、严格 replay
event、runtime capability、subprocess executor 和 CAS runtime-asset snapshot 分别落地。runtime asset
层会闭合 selected tree 与 URDF/OBJ/MTL/glTF/GLB/COLLADA loader 引用，worker 在首事件前与 close 后复验；真实
can-on-plate 2-step smoke 已取得 acquisition pass，但物理 validation 因短 horizon 失败。A039 新增
媒体 consumer：在 delegated cgroup/Landlock/seccomp 边界中由静态 FFmpeg 从 held FD 完整解码，
真实历史录像观测为 120 帧 / 114 个 decoded unique。A040 再把 replay handler/resolver 接成候选竖切：
Invocation 必须精确带 capability、executor、handler config、media verifier、input-specific runtime
assets 五项依赖，worker 输出先作为 untrusted bytes 保存，只有 evidence 与媒体（含固定
MP4/H.264/8bit-420/SAR 语义）复核后才晋升 typed artifacts。两组专项为 185 passed 且两个模块
statement/branch 100%。descriptor 已用 v2 诚实声明 `evidence_invariant_repeatable`；固定 loader、
generator 与 production application 会重读完整 CAS evidence closure；历史 pass bundle 和 900/120
handler receipt 曾因集成后的源码 identity 漂移而失效；当前固定 closure 已重新执行并重签。
P2 controlled-child 集成后，旧 checked compile bundle 曾因当前 `qualification.py` identity 漂移而
严格失效；当前固定树已用全新外部 scratch/CAS 真实重跑固定三轮，观测
`admitted -> reused -> reused`，并通过只读 verifier、全新 CAS strict loader 与 checked-in gate。
replay 随后的独立真实重签也已完成。VLM fallback、
LLM orchestration 和工作台不能从这些
候选底座推断为完成。

A044 只完成前端工作台的第一个竖切：`demo/` 可注入只读 `HarnessEventFeed`，通过
`GET /api/harness/events` 按 global cursor/run 读取已经提交的 `SQLiteEventJournal` 事件。浏览器保存
每个 run 视图的 cursor，严格校验响应并区分未配置、历史损坏与响应不自洽；轮询只发起读取，终态只取
`Event.to_status`。这一切片没有 Harness submit、System 2 调度、artifact 内容解析或拖拽编排；四页面
工作台仍是进行中。缓存只在当前 journal 重读完全一致后才进入 DOM，在途请求由 view generation
隔离，SQLite 64 位 cursor 以十进制字符串传输；localStorage 不可用时直接回到 journal。`tests/demo`
当前 36 passed，其中 12 项用真 Flask +
headless Chrome 覆盖提交后推进、缓存重确认、并发 filter、reload 与 fail-closed 状态。

A047 在这个只读面上增加 compile-only 写入口。配置了同一个 `HARNESS_WORKBENCH` 后，浏览器的
`POST /api/harness/compile` 只发送精确 `{request, seed}`；catalog、资产根、是否生成缺失资产、
qualification、Registry 与 CAS 都由 operator 在 `WorkbenchCompile` 构造时固定。接口同步执行已有
qualified `CompileApplication`，再从共享 SQLite authority 重读 terminal `RunState` 与完整 committed
history；两者一致后才返回 run/Skill、终态、attempt、terminal cursor 和精简 blocker。UI 不从 POST
摘要生成阶段，而是把 timeline 切到新 run、从 cursor 0 重放，并要求最后一条 committed event 与
摘要的 run/Skill/status/cursor 一致。未配置、历史损坏、摘要夹带 progress 或对账失败都会 fail
closed。

这仍不是完整 Stage 7：接口是同步 compile，不是异步 durable queue；没有 replay、validate、System
2、artifact 内容 viewer 或拖拽编排。compile `succeeded` 也不表示物理 validation、资产 settle 或
publishable。A047 的 `harness.workbench_compile_submission.v1` 是工作台投影，不新增 Harness 公共
schema snapshot；仓库当前总数为 55。A047 切片当时 `tests/demo` 为 84 passed，其中 18 项
使用真 Flask + headless Chrome。

A048 在此之上增加 terminal compile 的只读依赖审计。`WorkbenchCompile.audit(run_id)` 从同一
SQLite authority 两次读取 RunState、Invocation 与完整 filtered EventPage；三者全空才是 not found，
partial authority、两次读取漂移、非单调 cursor 或固定 0/0 preflight、1/1 execution binding 不精确都
拒绝；固定执行/预检的每条事件还必须分别是 attempt 1/0，Invocation digest 从类型化参数、依赖与
attempt 上限重新计算。`GET /api/harness/compile-runs/<canonical-v4-uuid>/audit` 不返回 effective
parameters、原始 output 或 events。
浏览器只在单个 compile run 从 cursor 0 完整重放到 terminal 后请求审计，再绑定 run/Skill、attempt、
status、event count/cursor 与微秒级首末时间；审计不进 localStorage，切换 run 会丢弃迟到响应，瞬时
unavailable 会本地重试，最多 500 条的单 run 历史不受全局视图 200 条缓存窗口截断。

A049 把该工作台投影显式升为 `harness.workbench_compile_audit.v2`，并在同一次双读闭包里增加只读
artifact metadata inventory。服务端不从 Event stage 名猜语义，而从类型化 compile input/output 重建
`input asset_catalog` 与五个 output bindings；终态 ArtifactRef 必须与 terminal event 精确同序，完整
event identity 集不得多/少，随后每项再经同一 `CompileApplication` CAS 解析并重验实际大小与 SHA。
只投影 locator-free 的 `name/media_type/schema_version/sha256/bytes/bindings`，其中 bytes 是十进制
字符串；固定 preflight 必须为空，bound failure 也不会把 Invocation input 冒充成已提交 artifact。
浏览器独立核 v2 exact keys、固定 binding roster、终态事件 metadata 与安全整数范围，再用
`textContent` 渲染；不生成链接或内容请求。当前 `tests/demo` 为 161 passed，其中 34 项真 Chrome；
相邻 Application/Event journal 为 51 passed；审计模块 213 statements / 100 branches 全覆盖。

A050 只为其中一个已验证 binding 增加窄化内容投影：
`GET /api/harness/compile-runs/<canonical-v4-uuid>/scene-preview` 仅接受 succeeded
`text2env.compile@1.0.0` 的类型化 `Text2EnvCompileOutput.scene_spec`，且 media/schema 必须精确为
`application/json` / `robotwin.scene_spec.v1`。服务端先重建上述终态 authority；声明超过 65,536 bytes
会在定位 CAS 前拒绝，随后直接打开固定 CAS leaf，以同一 FD 的 `O_NOFOLLOW|O_NONBLOCK`、regular
`fstat`、最多 cap+1 的读取、前后 stat identity、精确大小和 SHA-256 证明当次 bytes。内容还必须是
严格 UTF-8、无 BOM/重复键/非有限数、深度有界且 canonical 的 `SceneSpec`，其中 request/seed 必须
等于类型化 input，seed 和语义 digest 还须分别等于 EnvironmentPackage；解析完成后再读一次完整终态
authority。返回 `harness.workbench_compile_scene_preview.v1`，只含 run/artifact binding 与去掉 request
和 schema_version 的场景字段，不回传原始 JSON、path 或 URI。

浏览器只有在 audit v2 已对完整成功 run 和唯一 `output/scene_spec`（且声明大小不超过 cap）验真后才
显示按钮，也只在用户点击时请求；响应有独立大小上限，并与当前 run、Invocation digest、event
count/cursor 和 artifact metadata 精确对账。切换 run/filter、刷新 feed、重做 audit 或关闭面板会
abort/递增 generation 并清空内容；预览不进缓存、不自动重试，字段以 `textContent` 构造。当前
`tests/demo` 为 232 passed，其中 47 项真 Chrome；相邻 Application/Event journal 为 51 passed；
`demo/harness_compile.py` 374 statements / 156 branches 全覆盖。

A051 把同一模式收紧后用于唯一 `output/static_validation`：
`WorkbenchCompile.static_validation_preview(run_id)` 与固定
`GET /api/harness/compile-runs/<canonical-v4-uuid>/static-validation-preview` 只接受 succeeded compile
中 media/schema 精确为 `application/json` / `robotwin.scene_validation.v1` 的 binding。声明超过
262,144 bytes 会在 CAS 定位前拒绝；用于内容绑定的 ResolvedSceneSpec 也必须是 JSON 且不超过
1,048,576 bytes。两份固定 digest leaf 都通过同 FD 的 `O_NOFOLLOW|O_NONBLOCK`、regular `fstat`、
cap+1 读取、前后 identity、精确大小和 SHA-256 复核。报告必须是严格 UTF-8、无 BOM/重复键/非有限
数、深度有界且 exact shape；ResolvedSceneSpec 同样严格解析，并与 canonical typed model 精确一致。
服务端限制 checks 最多 169 项，要求唯一安全名称、严格 status 并重算状态/计数；恰有一个
`runtime_evidence:not_run`（`required=false`），`package_manifest` 与 `resolved_only_roundtrip` 均为
pass。服务端重算 resolved digest 并与报告、EnvironmentPackage 的 package/resolved digest 精确相等；
resolved 的 request、scene、seed、frame、unit、workspace、relations 和 source SceneSpec digest 还
逐项绑定已验真的 SceneSpec。三份固定 CAS 内容读完后再读一次 terminal authority。读取过程只验证
既有报告，不重跑 validator。

响应只投影 claim scope/mode、scene id、resolved digest、状态/计数及 check name/status；报告里的
`evidence`、request、原始 JSON、path、URI 和 locator 都不进入浏览器。入口只有在 audit v2 精确绑定
唯一且未超限的 static-validation artifact 后才显示，并只响应用户点击；run/filter/feed/audit 变化或
关闭面板会 abort、递增 generation 并清空，预览不缓存、不自动重试，所有字段以 `textContent` 构建。
checks 可按任意满足 ≤169、名称唯一和状态/计数/必需检查自洽的顺序到达，浏览器保持 wire 顺序显示，
不耦合 validator 当前实现顺序。
界面明确显示 `runtime_evidence:not_run` 意味着没有物理 replay，因此这份 compile-time 摘要不表示
validate pass 或 publishable。

这仍不是完整 Stage 7：Event Timeline 仍是唯一 operations authority，审计面板不复制或推演操作，也
没有通用 artifact 内容 viewer、下载接口或可编辑依赖图；内容入口只有上述成功 SceneSpec 与静态验证
报告的固定字段投影。CAS 读取是当次大小/摘要复核，不声称文件系统不可变。没有拖拽、重排、预览自动重试、
replay、validate 或 System 2。
compile `succeeded` 仍不表示物理 validation、资产 settle 或 publishable。A047/A048 的两份
`harness.workbench_*`（含 A049 audit v2、A050 scene preview v1 与 A051 static-validation preview v1）
都是工作台投影，不新增 Harness
公共 schema snapshot；仓库当前总数为 55。

Stage 5 的视觉评审状态是三态而非布尔值。在 `--run-smoke`/视觉评审路径中，只有 visual pass
才把 candidate 原子晋升为 `final_placement.json` 并退出 0；`pending_visual_review` 只写
`review_candidate_placement.json`，保留 `hold_for_review`/pending 字段并退出 2。batch 只有显式
传 `--allow-pending-visual` 才收集这种候选，aggregate 仍是 `review_required`/exit 2。默认
static-only 写 `static_scene_candidate_placement.json`，smoke/visual 均为 `not_run`；兼容 status
`pass_static_scene_module`/`pass_static_only` 与 exit 0 只表示非物理静态阶段完成，不应读成完整
acceptance。

生成资产 follow-on 仍不能被当作物理晋升：admission report 明确写
`physical_qualification=pending_settle`。A041 已让 generation-QC receipt 绑定最终 provenance 与
`asset-representation-set.v2`，并让全部活动 writer 在发布前验证完整文件闭包；但这个
`backend=sapien/check=generation_qc` 仍只是确定性生成器的 analytic QC，不是 SAPIEN settle。
后续真实 settle/runtime receipt 可以保留且不妨碍同资产复用，发布资格仍必须由独立物理门决定。

生产 compile 的资产所有权已经与一次运行状态分开：默认的可复用生成资产库是项目内
`self_improving/asset_pipeline/active/data/asset_library`，也可由
`CompileApplicationSettings.asset_library_root` / `--asset-library-root` 显式指定；`state_root` 只持有
SQLite/CAS、工作目录与生成 staging。qualification、测试和实验必须提供独立 scratch 库。这一变化
不改变上面的物理边界：generation QC 仍不是 SAPIEN settle。

A041 的“可信”范围是 cooperative POSIX/Linux runtime：运行前后 snapshot 复核 samefile/hash，
generated admission 用 pinned dirfd 拒绝复用和发布换靶，backfill pair 用 durable journal 恢复；但它
不证明 loader 打开的 FD，不约束同用户任意 Python，也不枚举传递 ELF/驱动/preload。ledger 文件与
representation 文件不是一个原子对象，所以 consumer 必须在使用时重新执行 `check_files=True`，不能
把 raw ledger path 当作资格。rescale apply 仍禁用，migrate apply 只允许单 ledger，writeback 当前只
有 SAPIEN issuer。这些限制与 902 passed/2 base-environment skips、真实 SAPIEN 两节点 2 passed、
最终公共 S13b→S11 的真实 loader/native `SWEEP 1/1`、跨 Harness 164 passed 一起记录在
`docs/evidence/asset-ledger-v3-integrity-20260831.*`。

同一切片的只读数据审计也表明，仓库里 162/162 份既有 ledger 都不满足收紧后的 v3 内容契约，
共 4,082 条 deleted/missing/pose-provenance violation。代码与新写入面已通过，旧数据没有被批量
重写；无法从现有 bytes 或可信 receipt 证明的 pose 继续作为 typed debt，而不是由迁移器猜值。

`self_improving/studies/ASPIRE/` 保存 2026-08-31 的 ASPIRE 一手资料、固定上游子模块、完整
实验日志和 held-out harness benchmark。该研究支持“经开发集验证、按触发条件检索的冻结技能
记忆”作为合成契约层候选机制；它不支持论文尺度复现、策略学习或仿真物理成功率提升主张。

2026-08-14 的同学工作区收口把 Yeyuxuan 的完整 RoboLab 分支历史与 20 份来源记录、Yuxin 当前 main/Web/未提交断点续测状态，以及 Bingsheng/Gujie/Yuxin 的独有说明归入同仓库。Yuxin 的第三方资产本体没有进入 Git；`asset_pipeline/receipts/asset_library_301_361.sha256` 只记录 12,047 个文件、约 27.64 GB 内容的精确摘要，`storage_uri: null` 表示它仍不是远端备份。

Jingxiang 上原先并列的 Stage04/Stage05/OpenXSim/AgenticSim 验证工作区已收口到单仓库：可审阅的 JSON、日志和运行脚本进入 `validation_evidence/openxsim_20260716/`，六个工作区的 cache-filtered 完整包进入同仓库 `workspace-consolidation-20260813` Release，逐文件 SHA-256 清单在 `workspace_archives/20260716/MANIFEST.sha256`。MetaSim 不再保留第二份 checkout，而是固定为 `external/MetaSim` 子模块 commit `6947e35`。

AgenticSim 名称有两种历史含义：旧产品仓库已经证明是 TacHarness 的稀疏历史状态，其唯一文件归档进 TacHarness 后本机副本已删除；`sim_adapters/agenticsim_runtime/` 只保留后来非 Git 工作区里的 Isaac 编排脚本，二者不能再混用。

PEARL portal 与 alternate Text2Env 都通过有双亲的历史合并接到主线，来源 tip 分别仍可沿祖先链追溯；精确 source/merge commit 和被排除的本地缓存登记在 `self_improving/source_inventory.json`。散落的 can/basket video anchor 标注则作为小型结构化证据放在 `self_improving/alchedata/artifacts/openxsim/`。

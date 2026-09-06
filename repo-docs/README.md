# Robot Harness /gen-env 中文 Repo Docs

这个仓库有两层。稳定核心是确定性的 `/gen-env` 编译器：一句受限的中英双语自然语言进来，被编译成 RoboTwin 可加载的场景包，然后才允许进入命令循环。它的路径是 `text -> 类型化 SceneSpec -> 资产 grounding -> 目标局部 support/containment 求解 -> 哈希绑定的 resolved 包 -> RoboTwin/SAPIEN 回放 -> 运行时门控`。外围 `self_improving/` 再组织选择、采集、训练、评估、诊断、记忆、资产复用与跨仿真适配，但不能绕过核心契约。看 [一条真实路径](walkthroughs/one-real-run.md) 可以走完稳定核心；平台边界见 [Self-Improving 平台](modules/self-improving-platform.md)。

为什么这样一个东西值得有专门文档，不在「自然语言几分钟能理解」这一层。难点集中在两件事：一是文本到机器人的信任边界（prompt 不能携带代码、路径、pose，但还得表达丰富的语义），二是「看起来稳」和「物理稳」之间的差距（外层 AABB 能落不等于 plate 里侧 100 mm 那块能落；渲染图能过不等于 SAPIEN 终末接触能过）。每一阶段在某个具体信任塌方之前先挡住它——这就是 [一条真实路径](walkthroughs/one-real-run.md) 一步步想讲清楚的。

## 阅读路径

| 读者目标 | 从这里开始 | 读完后获得什么 |
| --- | --- | --- |
| 一气走通编译 + 回放主路径 | [一条真实路径](walkthroughs/one-real-run.md) | 一条 prompt 从入口到 `runtime_validation_report.json` 的行为模型，含真实失败分支 |
| 实际调用 compile、配置参数和查找输出 | [Text2Env compile 使用手册](compile-api-guide.md) | catalog 构建、CLI/Python API、输出目录、失败语义，以及 replay/validate 复现命令 |
| 查看 20 条完整环境 compile 实跑 | [2026-09-06 v3 验收报告](../docs/evidence/compile-environment-acceptance-20260906-v3.md) | 10 条生成、5 条精确复用、5 条本地候选复用的环境/CAS/媒体证据，以及上游 ACDC 未运行的边界 |
| 按目录找改动入口与验证点 | [代码地图](code-map.md) | 在范围内每个源码目录的职责、关键符号、与主路径的关系、攻击测试所在 |
| 理解为什么解析器有界、能带什么不能带什么 | [受限解析](modules/bounded-parser.md) | 词典 + 正则的提取规则、prompt 边界拒绝、与 schema 的衔接 |
| 理解类型化契约如何门控下游 | [类型化场景契约](modules/scene-contract.md) | `SceneSpec`/`ResolvedSceneSpec` 的 frozen 严格模型、跨字段不变量、digest |
| 理解为什么不用外层 AABB | [目标局部几何](modules/target-local-geometry.md) | support 面、容器 interior、support margin 的几何数学与实测 override |
| 理解求解器何时停、失败长什么样 | [受限求解器](modules/solver.md) | bounded rejection backtracking、尝试上限、机读失败 trace |
| 理解 catalog miss 与运行时不稳怎么补 | [确定性代理](modules/derived-proxy.md) | procedural primitive、derived uniform scale、来源 lineage |
| 理解磁盘包怎么自证没被改 | [哈希绑定包](modules/replay-package.md) | 文件清单、SHA-256 manifest、`verify_package` 篡改检测 |
| 理解运行时物理通过到底验了什么 | [运行时门控](modules/runtime-gates.md) | 接触 fraction、drift、可见性、视频互异帧、嵌套未声明接触等门控 |
| 理解 Harness 对外记录与 Text2Env Skill 边界 | [Harness Schema Tranche](modules/harness-schema-tranche.md) | 14 个严格 schema、状态机、权威载荷引用、快照漂移门与尚未实现的 Registry/MCP 边界 |
| 理解 System 2 如何完成一次真实 compile 回合 | [System 2 Compile 回合](modules/system2-compile-turn.md) | 受信输入、planner 证据、qualified compile、ToolResult、状态增量与 history authority 的闭环及失败边界 |
| 理解 System 2 如何把成功 compile 安全交给 replay | [System 2 Compile → Replay 交接](modules/system2-replay-handoff.md) | history/receipt/state 交叉绑定、canonical 环境包与请求 provenance，以及 prepare-only 边界 |
| 理解如何把受信 System 2 replay input 的 package closure 复制到独立 CAS | [System 2 Replay-Input Package Promotion](modules/system2-replay-input-promotion.md) | source authority 重验、七个 logical refs、commit-last、幂等失败语义与本地文件系统边界 |
| 理解如何执行并对账一次动态 System 2 replay | [System 2 Replay Evidence Acquisition](modules/system2-replay-evidence-acquisition.md) | qualified factory wiring、动态输入、持久终态/Event/CAS 对账与仍缺的部署条件 |
| 理解如何从成功动态 replay 的本地 CAS 重算 snapshot validation | [System 2 Replay Snapshot Validation](modules/system2-replay-snapshot-validation.md) | Invocation/终态/依赖绑定、真实 snapshot adapter、PASS/FAIL/INCOMPLETE 与非发布边界 |
| 理解如何记录并仅凭 artifact 复核本地 snapshot summary | [System 2 Replay Snapshot Assessment](modules/system2-replay-snapshot-assessment.md) | acquisition-only recorder、同 CAS ref-only verifier、exact summary 对账与非来源权威/非状态推进边界 |
| 理解本地 typed Qwen 如何受限地执行可见语义评审 | [Local typed Qwen 可见 Provider](modules/vlm-visible-provider.md) | A1/A2 映射、严格离线 backend、内容绑定、零实跑预检与未授权边界 |
| 理解 Workbench 如何浏览已提交的运行活动 | [Workbench 运行活动板](modules/workbench-run-activity.md) | validated current feed 的四栏只读投影、run 筛选重读、焦点保留与 identity drift fail-closed 边界 |
| 理解 Workbench 如何持久索引并在重启后重验 replay assessment | [Workbench Replay Assessment 持久索引](modules/workbench-replay-assessment-index.md) | 深 `record(acquisition)` / `inspect(run_id)` seam、SQLite/CAS 绑定、frozen view 与尚无 route/UI 的边界 |
| 理解平台层如何组合旧工作区与外部项目 | [Self-Improving 平台](modules/self-improving-platform.md) | 稳定核心、编排层、资产层、适配层、历史层与子模块的边界 |
| 在 Jingxiang 仿真机上开发与协作 | [仿真组工作区协作](workspace-collaboration.md) | 个人目录、`worktree/<人名>` 分支、共享 `main` 与本地大文件的边界 |
| 审计本指南每条主张背后的源码证据 | [证据底座](references/source-evidence.md) | 三轮遍历、claim/evidence/confidence/caveat 与被消费者 |
| 审计本指南是否真的把模型传给了读者 | [质检报告](references/quality-review.md) | reader simulation、可理解性 review、残余风险 |
| 把重复见到的术语归一 | [术语表](glossary.md) | `SceneSpec`、`ResolvedSceneSpec`、support margin、derived proxy 等在本仓库里的精确意思 |

范围：核心行为仍记录 `scene_gen/`、`script/`、`demo/`、`tests/`；平台总览额外记录 `self_improving/`、`apps/` 与 `external/` 的所有权边界，并登记 Harness schema tranche，不把迁入的历史脚本逐个重述。`apps/pearl_evidence_portal/` 是独立呈现层，不能反向定义验收结论；`docs/evidence/` 的已验证证据被引用但不重述。除 PEARL 已裁剪的浏览器报告子集外，二进制资产、运行产物和外部依赖只保留来源与机读清单。

注意：本仓库根 `README.md` 的英文段是面向使用者的命令配方与已验证证据摘要；`repo-docs/` 是面向想理解仓库行为的人的中文叙述。两者不重复，互相引用。

证据状态：除特别标注外，本页基于当前源码已确认。

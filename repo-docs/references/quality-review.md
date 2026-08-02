# 质检报告 For /gen-env 中文 Repo Docs

这是一份审计笔记，不是讲解页。它检查本指南是否真的把可用读者模型传下去，以及在哪里还留有残余风险。要理解仓库，从 [一条真实路径](../walkthroughs/one-real-run.md) 起；本页只在它后面做可理解性审计。

## Reader Simulation

下面的回答只从已生成的指南里推，假设读者此刻打不开源码。

| Reader question | Answer from the guide |
| --- | --- |
| What real path is followed? | 一条 prompt → `ResolvedSceneSpec` 包 → SAPIEN 回放 → `runtime_validation_report.json`；具体命令 `python script/generate_scene.py --prompt "Place a can on top of a plate." --seed 42 ...` + `python script/run_scene_runtime.py ...`。 |
| What is hard or non-trivial? | 文本到机器人的信任边界（prompt 不能带代码/路径/pose）+「看起来稳」与「物理稳」之间的差距（外层 AABB 能落不等于 plate 内侧 100 mm 那块能落；渲染图能过不等于终末接触能过）。 |
| What changes at each phase? | 受限解析产出 `SceneSpec` → schema 锁契约 → grounding 选真实模型 → solver 在目标局部几何内 bounded 拒绝回退摆位 → derived proxy 补 catalog 不稳 → builder 写哈希绑定包 → SAPIEN 回放采 contact + 视频 + drift → validator 把证据转 checks。 |
| Where would I change this behavior? | 见 [代码地图](../code-map.md)：parser 入口 `scene_gen/parser.py:parse_rule_based`；契约 `scene_gen/schema.py`；求解 `scene_gen/solver.py:solve_scene`；几何 `scene_gen/support_geometry.py`；包 `scene_gen/builder.py`；门控 `scene_gen/validator.py:validate_resolved_scene`；回放 `scene_gen/envs/generated_scene.py:load_resolved_scene` 与 `script/run_scene_runtime.py`。 |
| Where do assumptions stop? | 真机 RoboTwin/SAPIEN 不在 CI 中跑；committed fixture 套件无需 RoboTwin checkout 即可跑主契约层。绘图评判不是物理证据，可选 extra。 |
| What would prove this explanation wrong? | 一份提示注解：`docs/evidence/prompt-matrix-20260719.md` 的 33/33 编译 + 10/10 SAPIEN 通过是 2026-07-19 在 RTX 5090 主机上一次性运行结果。后续若 `scene_gen/validator.py` 阈值或 `solver.py` 求解策略动过且未在真机回放，本指南里「真实路径」字面通过率不再对应源码。重跑 `python script/run_prompt_matrix.py --runtime` 应能再出 pass；出 fail 即证伪。 |
| What would a careful newcomer ask next? | 1) 「为什么不在 CI 里跑运行时？」→ 见本仓库根 `AGENTS.md` 的 Testing Requirements；2) 「digest 把哪些字段算进 canonical？」→ 见 [哈希绑定包](../modules/replay-package.md)；3) 「自动缩放什么时候允许？」→ 见 [确定性代理](../modules/derived-proxy.md) 的 `PRIMITIVE_PROXY_SOURCES` 白名单一段。 |
| How can I verify it? | `pytest -q`（无 RoboTwin）验主契约层；`pytest -q tests/scene_gen/test_builder_validator.py` 验攻击用例锁住的误报模式；真机回放由 `script/run_scene_runtime.py` 跑。 |

## 可理解性 Review

| Review question | Result | Evidence | Follow-up |
| --- | --- | --- | --- |
| Can a reader state the hard part of the main path in one sentence? | Yes. | [一条真实路径](../walkthroughs/one-real-run.md) 开场与 Step 1 名把「文本到机器人的信任边界 + 看起来稳 vs 物理稳」两句压力直接给出。 | 下次 sync 时把这两句压力在 README 第二段开头也说明一次。 |
| Does the walkthrough still explain the flow if source links are hidden? | Yes. | 每步以「这一步做什么、为什么」起头，源码定位符排在句末做证据，不是主语。 | 维护时不退化成「函数名是主语」的叙述。 |
| Can a reader use `code-map.md` to locate the owning source area and a verification point without scanning the repository tree? | Yes. | [代码地图](../code-map.md) 每个目录给出关键符号 + 最近验证点（如 `tests/scene_gen/test_builder_validator.py`）+ 与主路径的关系。 | 如新增模块须保持 table 行扩展，避免遗漏验证点。 |
| Does each step start with observable behavior, pressure, or effect before source names? | Yes. | Step 1 先讲「prompt 不是被理解而是被收紧」，再点 `parser.py`；Step 4 先讲「外层 AABB 会翻」，再点 `support_geometry.py`。 | 维护时保留此序。 |
| Is there at least one real boundary, failure, retry, validation, or caveat path when evidence exists? | Yes. | Step 4 给 `SceneSolveError` + 不可行 prompt 矩阵用例；Step 8 给具体的误报模式攻击测试列表。 | 在 sync 时若有新假阳性模式被锁，须回到对应 module 补行。 |
| Are claims backed by `references/source-evidence.md`, tests, commands, configs, data, or artifacts? | Yes. | 每条主路径 claim 在 [证据底座](source-evidence.md) 表里有 evidence + confidence + caveat。 | 改动行为时同步回填证据表。 |
| Does the review name one falsifying check and one likely reader follow-up? | Yes. | falsifying check：`python script/run_prompt_matrix.py --runtime` 应再出 pass。follow-up：CI 不跑真机 + digest 字段 + 自动缩放白名单三条。 | 若真机回放产物 path 变更，更新本行。 |
| Does the evidence map prove at least two traversal passes and name adjacent paths checked but not traced? | Yes. | [证据底座](source-evidence.md#evidence-traversal-log) 有 Pass 1 + Pass 2 两行，并写明 Pass 3 暂不需要的依据；coverage note 列明 `run_100_seed_acceptance.py`、`run_prompt_matrix.py`、`build_stage5_report.py`、`rendered_critic`、`demo/app.py` 是相邻但不追踪路径。 | 扩范围时把同步过的相邻路径提到追踪。 |
| What remains out of scope or partially verified? | 见 [残余风险](#残余风险) 表。 |

## 残余风险

| 残余项 | 风险 | 缓解 |
| --- | --- | --- |
| 真机 SAPIEN 回放不在 CI 中 | 「真实路径」里的运行时门控阈值在源码改动后可能不再有真机记录支撑 | 根 `AGENTS.md` 要求涉及 support / containment / loader / validator 契约的改动在支持 RoboTwin/SAPIEN 的机器上做真机回放；下一次再跑 `prompt-matrix-<新日期>.md` 时把本指南里的「已知通过」声明同步到新结果 |
| Provider 路径未给模块 | `StructuredSceneProvider` 是非 rule-based 路径，本指南只在 [受限解析](../modules/bounded-parser.md) 末尾提一句，未展开 | 默认仓库 CLI 走 rule-based；若未来真的接入外部 provider，新增一个 provider 专属模块或扩 bounded-parser 模块页 |
| `demo/app.py` 队列层只做摘要 | demo 复用同一 `scene_gen` 流水线；本指南只说它是薄编排，未给独立 module | demo 模块若扩大其队列/产物注册职责到值得单说，再加一个 demo 控制面专属模块页 |
| `rendered_critic` 排除在详述之外 | VLM 判是可选 extra + 非物理证据；若它将来变成主流评判，会被误导性 | 在 [运行时门控](../modules/runtime-gates.md) 开场已显式说「渲染不是物理证据」；若 VLM 升为门控须立即改写本指南 |
| Threading 后端动态注入 | 解析器 / grounding / solver 都没并发；本指南沉默 | 未发现证据表明该方向有改动；如引入，新增一个并发与哈希专属模块页 |

证据状态：除特别标注外，本页基于当前源码已确认。
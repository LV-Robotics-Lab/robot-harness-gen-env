# 生成 RoboTwin 桌面场景

这个 skill 是新 prompt 的总入口：把一句自然语言桌面摆放描述转换成 RoboTwin scene preview，并通过 static validation、smoke render、visual/VLM review 逐级验收。

它协调这些项目组件：

- `harness/`：prompt-to-placement pipeline。
- `mcp_lite/`：validation、smoke、artifact、visual-review 工具。
- `scripts/run_robotwin_placement_smoke.py`：真正把场景加载到 RoboTwin 并渲染 preview。
- `asset_catalogs/`：prompt 相关 asset metadata 和 placement defaults。
- `skills/` 下的细分 skill：Designer、Critic、Orchestrator、asset grounding、smoke review。
- RoboTwin `code_gen/` 的结构化输入、仿真反馈、视觉观察和迭代修复模式。

前提：agent 在 `robotwin-text2env-demo` repo 中工作，用户已经有 RoboTwin 环境，例如 `~/RoboTwin`。如果缺少 repo 组件，应先让用户提供 repo 或 clone GitHub repo。

## 必须流程

1. 从用户 prompt 出发，对 master catalog 做 asset grounding。
2. 创建或复用 prompt case catalog。
3. 生成并验证 `TabletopPlacementSpec`。
4. 生成可复用的 `generated_scenes/<case>_scene.py`。
5. 用 `--run-smoke` 跑 RoboTwin smoke。
6. 用人工、Codex visual reference 或外部 VLM 检查 preview。
7. 如果视觉审核失败，修 catalog defaults、placement logic 或 generated scene code，然后重新跑。
8. 只有 static validation、scene module generation、smoke、明确 visual review 全部通过，才能标记完整 pass。
9. 每次发现新坑，都要回写到对应 skill 和 asset catalog。

## 参考文件

按需读取：

- `references/new-prompt-workflow.md`：端到端命令和输出 artifact。
- `references/asset-catalog-rules.md`：如何创建 catalog，如何处理 rigid/articulated 资产。
- `references/visual-review-gate.md`：如何判断 pass/pending/fail，如何写 visual review JSON。
- `references/robotwin-code-gen-patterns.md`：哪些 RoboTwin `code_gen/` 思路可以借鉴，哪些 task-code-generation 部分不要照搬。
- `references/known-pitfalls.md`：apple/plate、vegetable/basket、laptop/knife 中踩过的坑。

## 状态规则

- `PASS_STATIC_ONLY`：只通过静态 JSON/catalog 检查，还没有 RoboTwin render。
- `REVIEW_REQUIRED` / `pending_visual_review`：smoke 已渲染，但还没有通过视觉报告。
- `PASS`：static validation、smoke 和明确视觉审核报告全部通过。
- `FAIL`：static validation、smoke 或 visual review 失败。

不要把 smoke success 当作 visual success。

## 关键约束

- 不要编造 RoboTwin asset id 或 model id。
- 不要按物体出现顺序判断空间关系。
- 自然语言中的方位判断必须以机器人/双臂第一视角为准。left/right/front/back 默认表示机器人自己的左/右/前/后，除非 prompt 明确指定其他参考系。
- 没有视觉审核，不要标记 pass。
- 不要提交 HDF5、完整 `runs/`、checkpoints 或 RoboTwin assets。
- 可以保留小 preview evidence 和 prompt-specific catalog。
- 每次新 prompt 后都要更新 skill lessons。

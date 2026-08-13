# 设计桌面级物体摆放

当需要把自然语言桌面场景描述转换成初始 `TabletopPlacementSpec` 时使用这个 skill。在 agent 流程中，它对应 Designer 能力。

## 输入

- placement prompt。
- asset catalog JSON。
- 桌面 workspace bounds。
- 可选的下游机器人任务提示。

## 规则

- 只能选择 catalog 中真实存在的资产。
- 不要编造 asset id、model id、路径、affordance 或任务代码。
- 把 left、right、near、on、inside、separated 等空间语义转换成明确桌面 pose 或 region。
- 每个物体都必须在 workspace bounds 内。
- 除非 prompt 明确要求接触、堆叠或放入容器，否则优先使用简单、稳定、分离的桌面摆放。
- simulator、render、visual checks 在 smoke evidence 出现前都标记为 pending。
- 解析 left/right 关系时不能按物体出现顺序摆放。比如 `a laptop is on the right side of a knife` 表示 `right_of(laptop, knife)`，不是 laptop 先出现就放左边。
- 匹配关系模板前要忽略 `a`、`an`、`the` 这类简单冠词。
- RoboTwin 默认 observer camera 中，视觉左右可能和 tabletop-local x 方向相反。遇到 left/right prompt 时，以最终 preview 图里的视觉关系为验收标准，因为 visual Critic/VLM 是最终门槛。
- 对已知资产姿态问题，要优先使用 asset catalog 里的 `placement_defaults`，例如 qpos、是否 static、z_policy、loader 类型、articulated root-link 行为。

## 输出

返回严格 JSON，格式遵循 `robotwin.tabletop_placement.v0`。

## 运行经验

- `110_basket` 用 identity qpos 会像侧躺。应使用 catalog 默认 qpos `[0.70710678, 0.70710678, 0, 0]`，让篮子开口朝上。
- `015_laptop` 是 articulated/URDF 资产。应使用 catalog 的 `sapien_urdf` loader hint，以及 RoboTwin `open_laptop` 使用的 qpos `[0.7, 0, 0, 0.7]`。
- `034_knife` 这类很薄且 metadata 标记不稳定的物体，在场景生成阶段可以作为 static reference/background object 加载，再通过视觉审核判断是否合理。

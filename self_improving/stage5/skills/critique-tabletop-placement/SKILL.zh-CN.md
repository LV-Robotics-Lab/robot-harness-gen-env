# 评估桌面级物体摆放

当需要评估 Designer 生成的 `TabletopPlacementSpec` 时使用这个 skill。在 agent 流程中，它对应 Critic 能力。

## 范围

只检查场景构建质量：

- prompt 和物体语义是否匹配。
- asset 和 model 是否存在。
- pose 是否在 workspace bounds 内。
- 是否有近似碰撞风险。
- 稳定性 metadata 是否合理。
- 机器人可达性和相机可见性风险。
- 这个场景是否能支持后续机器人任务。
- 是否显式表示并满足 left_of/right_of/near/on/inside 等空间关系，尤其是最终视觉坐标系里的关系。

不要评价 policy training、数据采集质量或任务 success predicate。

## Static Critic 与 Visual Critic 的边界

- Static Critic 只检查 JSON、asset、model id、近似 bounds 和近似 collision。
- Static Critic 不能把场景标记成视觉正确。它只能要求后续 smoke render 和 visual Critic/VLM review。
- `smoke pass` 只表示 simulator 能加载并渲染，不代表朝向正确、不穿模或满足 prompt。
- 如果 prompt 依赖 left/right 这类视觉方向，Critic 必须看 preview 图，而不是只看 tabletop-local 坐标。

## 输出

返回严格 JSON，使用 `robotwin.tabletop_placement_critic.v0`，verdict 为 `accept_for_next_stage`、`repair_required` 或 `reject`。

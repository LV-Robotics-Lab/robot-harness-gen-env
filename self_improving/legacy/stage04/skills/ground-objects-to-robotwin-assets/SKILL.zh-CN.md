# 将自然语言物体映射到 RoboTwin 资产

当需要把自然语言中的 object mentions 映射到 RoboTwin asset catalog entry 时使用这个 skill。

## 规则

- 用 `semantic_name`、`aliases` 和 `tags` 匹配 prompt terms。
- 优先选择精确语义匹配，而不是宽泛类别匹配。
- 如果多个资产都合理，返回不确定性。
- 不要编造缺失资产；缺失时标记为 unavailable，或请求更丰富的资产库。
- 保留后续 validator 需要的 `asset_id`、`model_id` 和 metadata。
- 准确记录 `asset_type`。rigid GLB 资产走 `create_actor`；`015_laptop` 这类 articulated/URDF 资产需要 `sapien_urdf` loader。
- 对任何有已知姿态、scale、static/background、z_policy、root-link 要求的资产，都要记录 `placement_defaults`。
- 保留 RoboTwin 原始 `asset_id` 拼写，即使它看起来奇怪，比如 `069_vagetable`；同时通过 aliases 增加自然拼写，如 `vegetable`。
- `stable=false` metadata 不能忽略，它意味着该资产可能需要 static/background loading 和视觉审核。

## 已知资产备注

- `069_vagetable`：RoboTwin 资产名拼作 `vagetable`；catalog 中应添加 `vegetable` alias。
- `110_basket`：使用 qpos `[0.70710678, 0.70710678, 0, 0]`，视觉上篮子开口朝上。
- `015_laptop`：articulated/URDF 资产；MVP catalog 使用 `sapien_urdf`、model id 0 / subdir `10040`，qpos `[0.7, 0, 0, 0.7]`。
- `034_knife`：rigid 资产，但 metadata 为 `stable=false`；如果只是背景场景，优先作为 static reference object，除非下游任务明确要操作它。

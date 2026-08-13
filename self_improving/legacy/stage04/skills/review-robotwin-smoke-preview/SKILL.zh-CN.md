# 审查 RoboTwin Smoke Preview

当需要检查 placement run 产生的 RoboTwin smoke 输出时使用这个 skill。Smoke success 只说明 RoboTwin 能加载并渲染场景，不等于这个场景视觉上正确。

## 检查项

- `smoke_report.json` 存在且结果为 pass。
- `head_camera.png` 和 `observer_camera.png` 存在。
- 可选的 `observer_camera.mp4` 存在。
- preview 需要由人工或 VLM 继续检查 object identity、prompt match、floating、penetration、严重遮挡和错误物体数量。
- 如果物体明显侧躺、倒置、穿桌、悬空、严重遮挡或语义错误，必须标记为 fail。
- 如果还没有人工 / VLM / Codex visual review 真正看过图片，只能标记为 pending，不能标记为 pass。
- 对 left/right prompt，必须在 preview 图的视觉坐标系中判断关系。simulator 坐标系可能和视觉左右不一致。
- 检查 container/support 物体朝向是否合理：篮子应开口朝上，盘子应平放，laptop 应能被识别为 laptop，而不是侧面塌成一片。
- 视觉审核失败时，要写具体 repair hint：哪个物体、什么视觉问题、应该改 qpos / position / loader / default 中的哪一项。

这个 skill 不替代视觉模型。它定义的是 review 标准和 artifact contract。

## 最近失败模式

- Basket run：smoke pass 但 basket 视觉上侧躺。修复方式是做 orientation probe，并把 `110_basket` qpos `[0.70710678, 0.70710678, 0, 0]` 写进 catalog。
- Laptop/knife run：local 坐标上对了，但 preview 里左右反了。修复方式是让 lateral language relation 以 observer preview 的视觉左右为验收标准。
- Laptop run：`015_laptop` 必须走 articulated URDF loader；用 rigid smoke loader 是错误接口。

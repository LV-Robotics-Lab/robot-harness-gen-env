# Canonical x2env 保温杯纯图输入：许可门阻断证据（2026-09-14）

## 结论

用户提供的单张黑色 Thermos 保温杯图片通过当前固定本机入口提交。`ingest` 与
`codex.interpret` 成功，模型将前景识别为黑色 `insulated bottle`、结构支撑识别为白色
`table`，并保留了尺度和位姿不可从单图可靠恢复的未知项。

默认 `local → web → reconstruction` 资产链未授予可用环境：web 以 `thermos flask` 查询但无候选；
reconstruction 实际完成 SAM2 分割与 TRELLIS 几何生成，得到 7,507,928-byte GLB，随后因当前部署的
派生授权不覆盖本次图片而记录 `registration_allowed=false`。Harness 在 `asset.resolve` 以
`blocked_license` 停止，没有产生 SceneIR、compile、Genesis replay、validation 或 environment package。
这是一条预期的 fail-closed 结果，不是成功环境。

## 输入与运行身份

- modality：image only，未附加隐藏文本
- seed：53
- idempotency key：`group-showcase-thermos-image-20260914`
- workflow：`82779899-74db-4ff0-bbf7-7d775b066ed5`
- 输入规格：1279 × 1706 PNG
- 用户原图 SHA-256：`b1d9b73e1e48ef4f9aa59f2788b0c150562482693eee5e0a5084242039b78be0`
- reconstruction 规范化输入 artifact SHA-256：`ff4fcefc554e59705c940524c0b1eb86ac25ee63b9ac92c9b0c0dc8564104e8f`
- 模型：`openai/gpt-5.6-terra`，请求 `model_reasoning_effort="max"`
- 命令耗时：112.46302860893775 秒
- 固定入口 preflight：Codex、Genesis、local、web、reconstruction 均 configured；configured 不代表具体请求会通过来源门。

## 终态与保留证据

- workflow status：`blocked`
- stage / error：`asset.resolve` / `blocked_license`
- revision：3
- environment package：null
- failure bundle manifest SHA-256：`083070eaf5a29f52508cfaed335ae5d9c599273a769e8163235b5f1415cbf3b5`
- reconstruction geometry SHA-256：`67077e05fbea4b9c3a4e903f41f39780e9749514ea4f04125fa4952642408fcb`
- reconstruction wall time：46.81526353198569 秒
- failure bundle 导出了 2 张图片及 15 份原始文本日志；原始状态、模型回执、分割、几何与许可记录均保留。

运行输出位于 `/tmp/x2env-showcase-thermos-20260914/`。该临时绝对路径只用于本机追溯，组会静态网站
复制了用户输入和实际分割后的模型输入，不复制 7.5 MB 几何或私有运行日志。展示副本位于
`x2env-harness-showcase/assets/media/`，机读摘要为 `x2env-harness-showcase/evidence.json`。

## 组会解读边界

这次运行证明当前主链能对新图片完成受管理理解、来源回退、分割和几何生成，并在授权不足时拒绝注册。
它不证明重建几何质量、物理稳定、可复制运行或 release qualification；这些阶段没有执行。

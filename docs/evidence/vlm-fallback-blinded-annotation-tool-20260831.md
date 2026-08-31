# VLM 双盲标注工具验证（2026-08-31）

## 结论

已完成预注册实验所需的盲标导出、回答校验、分歧仲裁和密封工具，但没有代替人类填写标签。
工具让下一步人工标注可以开始；它本身不代表 test gold 已封存，也不允许启动 VLM 基线。

## 已验证行为

- 两位标注者拿到不同的 HMAC 匿名 item id 和不同排序。
- 公开包只含任务意图、图片和八项可见检查；不含 case / group / split、源路径、runtime JSON、
  arm、模型输出、fallback 或另一位标注者答案。
- 私有映射为 `0600`，由 32 字节以上 blind key 做 HMAC；错误 key、mapping 篡改、源码变化、
  assignment 变化、缺行、重复项、额外 / 缺失检查和非法状态均失败关闭。
- 第三位标注者只看到分歧检查，不看到前两人的选择；所有分歧都必须仲裁。
- 密封结果按 train/dev/test 分开；test payload 和回答 SHA 被候选 manifest digest 绑定。
- tool source SHA 写进公开 assignment 和私有 mapping，三个阶段不能静默换实现。
- 检查冻结图片元数据：114 个输入视图中没有 EXIF 或非 ICC 文本 metadata。

## 真实冻结数据烟测

在包含完整 committed / 本机冻结证据的 detached worktree 中：

- 完整协议、运行器与标注工具测试：`190 passed`。
- 标注工具专项 statement coverage：85%。
- CLI 导出：39 个可见语义样本，每位标注者 114 张视图。
- 公开输出共 232 个文件，两份 assignment digest 不同。
- 对 case id、group id、非图片 artifact 路径和 split 字段的公开包泄漏扫描通过。

## 诚实边界

- 没有生成、猜测或复制任何人工 gold label。
- 没有打开 sealed test、运行 Qwen、启动 SAPIEN 或产生新的物理回放。
- test payload 是外部目录权限隔离，不是自制加密；prompt 冻结前必须保持在模型不可见的安全位置。
- 当前 checked-in annotation manifest 继续是 `pending_blinded_annotation`。

源码 SHA-256：`73bec3af72e393226fc6b23f3bfd598931d9f3f9982c2251c70abd99720e8291`。

# VLM 校正与 prompt fallback 运行器验证（2026-08-31）

## 结论

本次完成的是**可审计实验运行器**，不是模型效果实验本身。运行器可以按冻结实验规范组织
VLM 可见语义校正和 typed prompt fallback，记录预算、输入输出、断点恢复和哈希链回执；昂贵
VLM 推理、盲标 test 解封、仿真回放和晋升判断均未开始，因此当前没有“效果提升”或“可晋升”
结论。

## 已完成

- 冻结规范包含两个实验：A 组 39 个样本，B 组 36 个样本。
- `ExperimentRunner` 校验冻结规范、源码清单、证据资产和模型身份后才允许执行。
- 运行日志为追加写入的哈希链，并支持锁、pending 事务、崩溃恢复、重复调用检测和预算门禁。
- 外部 routing provider 通过沙箱进程接入；生产模式不允许测试 provider 或 oracle route 晋升。
- provider 的 stdout 与 stderr 采用合并上限并在读取过程中计数。超限时立即杀死子进程，不再
  等进程结束后才检查，避免恶意或失控模型输出占满主进程内存。
- 密封 test 清单仍为 `pending_blinded_annotation`，没有 test label payload，运行器会失败关闭。

## 测试证据

- TDD 回归：先加入“provider 连续输出 2 MiB 且企图继续执行”的失败测试，再实现读取期
  1 KiB 上限；测试确认子进程在写入完成标记前被杀死。
- 干净 detached worktree，叠加本次四个待提交文件并补齐本机已有的冻结证据：
  `179 passed in 2.31s`。
- 当前主工作区（排除两个依赖正被其他任务删除的 PEARL 门户库存测试）：
  `177 passed, 2 deselected in 2.97s`。
- 覆盖率：`protocol.py` 93%，`runner.py` 92%，合计 92%。
- Ruff check、Ruff format check 和 Python compileall 通过。

干净 worktree 的完整 179 项通过，说明那两个主工作区排除项是并行门户清理造成的工作区状态，
而不是运行器回归。

## 哈希绑定

| 对象 | SHA-256 |
| --- | --- |
| `runner.py` | `6ecdbcf2916351a29273416cba08682db3a464cf6f32cf492145eea2963a76ad` |
| runner source manifest | `e74c66216270f5b22a2d647b56f75a14c59c1e458f8818219f18594f7eb6a2fe` |
| frozen experiment spec | `ad19d38204f20c42d8070785994fe749b8db41364e002e382e938cb92ae5bf6c` |
| frozen log prefix | `141bc995ba391a11cea3461180f51936f5829aca0c13ef44e10b48d3ef27f6a4` |
| sealed test annotation manifest | `fba3590f5acde490c2ddbe803f785dba54f037a00207f1e8c149166a3ea15114` |

## 尚未完成

- 双盲标注、test gold 封存与一次性解封。
- 固定模型 / processor digest 后的本地 VLM 基线与逐次 prompt 实验。
- B 组需要的真实 compile、运行时 / 物理回放与新鲜观测。
- 按预注册阈值生成量化对比、失败簇分析和晋升或拒绝结论。

以上未完成项不能由渲染截图、单元测试或运行器本身替代。

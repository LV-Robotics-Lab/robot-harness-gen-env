# System 2 本地 Qwen 规划实跑证据（2026-08-31）

## 结论

本轮证明了一个受限结论：冻结的本地 Qwen2.5-VL-7B-Instruct 能在一次真实
GPU 推理中，基于哈希绑定的世界状态与公开 Skill JSON Schema，产出通过
`PlannerDecision` 和 `Text2EnvCompileInput` 双重校验的下一步 compile 调用。

这**不是**环境生成端到端通过：最终成功样本使用的是结构合法但内容为空的实验
catalog，Skill qualification 也是候选占位摘要；Harness 没有在这次实验里执行
compile，更没有产生环境包、回放或物理验证结果。

## 固定边界

- 3B revision：`66285546d2b821cf421d4f5eb2576359d3770cd3`
- 3B snapshot manifest：
  `b0f4cf794ec552f5e23de019107801e5adfec633d3b5c74564cdf6b65e9f1cad`
- 7B revision：`cc594898137f460bfe9f0759e9844b3ce807cfb5`
- 7B snapshot manifest：
  `3b785e700f59d59733b8ba4c92dedacbbd9fbaac642fc8a8a49b5af7545fd145`
- 推理环境：CPython 3.10.20、torch 2.11.0+cu128、transformers 4.57.6、
  NVIDIA GeForce RTX 5090；离线、本地文件、greedy decode、BF16。
- 最终输入：`put a can on a plate`、seed 7、60-byte 空 catalog、
  `generate_missing_assets=false`。
- 所有 provider 调用都发布 prompt、原始回答和终态收据；失败不做 Markdown
  剥离、字段补全或参数修复。

## 逐次实验

| 次数 | 模型 | 上下文/提示变化 | 结果 | 可复核收据 | 实测成本 |
|---|---|---|---|---|---|
| 1 | 3B | 初始 user-only 合同 | 拒绝：输出多步 prose/Markdown，达到 512 token 上限 | `120a64d79f7f22f4496e56576c98ebad09ac53610733b0120502eecb494db2bf` | 1065/512 tokens，9.400 s，7,647,612,928 B peak |
| 2 | 3B | 增加固定 system instruction | 拒绝：只给 3 个键，并把 `skill_ref` 放进 `parameters` | `ff92d7e10abd82cbf1a52ab640ce0b4421ab73905c5b6ab45b8fc33b9aefbfb8` | 1131/51，2.994 s，7,655,857,664 B |
| 3 | 3B | 增加 9 键模板和真实 state/context hash | 结构校验通过，但 `parameters={}`；当时尚无 Skill 输入校验，回看不可执行 | `360f252f60d027133fb6fd9fd7df18b991275038930e7e0c4d6161de0e7145d3` | 2138/201，5.068 s，7,778,679,808 B |
| 4 | 3B | 加入公开 input JSON Schema 与严格参数门 | 拒绝：Markdown 围栏且参数为空 | `69b19af7439722d8c8d9145d0f38a7ed7dc4db85e756d03421441e0fb1946944` | 2957/203，5.208 s，7,880,784,384 B |
| 5 | 3B | 空对象模板改成“完整对象”占位 | 拒绝：找到正确 4 项参数，但把整层 decision 嵌套进 `parameters` | `3e73daf280a388f28c9da086041b77c28094fce9b782e3e5cc8baed4bab66542` | 3007/570，10.259 s，7,888,357,376 B |
| 6 | 7B | 同一合同作模型尺寸对照 | 拒绝：4 项参数正确，但漏 `observation_keys` 与 `stop_reason` | `0e2a6871e61f0276c16e668494593f81de97e3aa835e0f20755db5b7278b0a9f` | 3037/426，9.741 s，17,208,486,400 B |
| 7 | 7B | system instruction 明列空值键与参数边界 | 拒绝：复制了提示里的 `SORTED_REQUIRED_FACT_KEY` 占位符 | `eaeaec90874b7d96f74f4c0a5d4d4ab157f86c812799737becc263593771bb77` | 3077/205，6.772 s，17,219,332,608 B |
| 8 | 7B | 删除全部可复制占位符，改成 enum/const/type/action contracts | **通过**：完整 9 键、正确 Skill、4 项参数通过 typed input contract | `f53114f0a16e38549468e4909fa0573f1799557a4dd1534a3f1f1856083d318d` | 2674/435，9.744 s，17,133,165,568 B |
| 9 | 3B | 最终 action contracts；显式归一 greedy generation config | 拒绝：照抄 `must_validate_against` 合同引用，未返回实参；遗留 temperature warning 已消失 | `7410ee2b27ff6d7ad9275dd68d8f72f24e8ff5e29f1f29b0a889a0eaa62ce96f` | 2686/89，3.535 s，7,844,784,640 B |
| 10 | 7B | 与第 9 次相同的最终代码和生成配置 | **通过**：最终实现摘要下再次得到完整 typed decision | `2ce863f2e73cd337c2d016c0f7b85da98501a8f863f25ed0b2fd0ea0311fcd9e` | 2678/437，9.801 s，17,133,966,336 B |

7B 首次配置时曾误填 snapshot manifest 摘要，attestation 在模型加载前以
`SnapshotIntegrityError` 拒绝；纠正为上文冻结摘要后才进入第 6 次 provider 调用。
该预检失败没有伪造运行收据。

## 最终成功闭包

- machine-local CAS：`/tmp/system2-qwen-real-smoke-20260831-attempt10-7b-final/cas`
- world state：
  `eed5e28092e225e0d49dac6f7f3063a1dfa87c5c619501fbae5f391f5c6d7e90`
- planner context：
  `753199b405e036a4c71ef1ae2ec615a37463cb05ee185f4575c3c371dc4edbdf`
- provider identity：
  `d5e95a9f8abe0e113a26751739fc0936ecb4dd0417a98718e112e8c924d33d3b`
- prompt：`4471ea240f77e57df58b822d5405eb938ad55426117fbdcc465cfe37f378268b`
- raw response：
  `4a3d58a47097dd3c787e533804e53ba0b9fc63160d9973c8ee2c74f884720ac9`
- decision：`d4e30bb4f8128fc8cb8d831af57d96c9d3ac7c347faeb2db456db1bafe65a11e`
- execution receipt：
  `2ce863f2e73cd337c2d016c0f7b85da98501a8f863f25ed0b2fd0ea0311fcd9e`
- 真实 callback：14 个阶段，从 `context.validated` 到 `receipt.published`。

## 诚实边界与下一门

当前 provider 是进程内 Transformers adapter。它绑定 snapshot、Python、关键包
RECORD、torch/transformers 模块、GPU 与推理设置，但明确记录
`hermetic=false`、`dependency_closure_complete=false`。早期实跑暴露的 snapshot
generation config 警告已在最终实现中消除：`do_sample=false`、`temperature/top_p/top_k=null`、
`repetition_penalty=1.05` 均被显式设置并进入 provider identity；这仍不能替代进程/容器级
依赖隔离。

下一次才可称“agent 驱动 compile 跑通”的验收是：从同一 CAS 读取真实、非空、
可移植 catalog；使用当前实现匹配且通过的 compile qualification；由规划结果进入
Registry/compile application；最后把 compile receipt、typed output 和 state delta
写回 trusted world state。任何一个环节缺失，都不得把本报告的 planner success
扩写成环境生成成功。

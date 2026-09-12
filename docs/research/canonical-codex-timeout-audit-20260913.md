# Canonical Codex 单次超时只读审计（2026-09-13）

## 结论与边界

固定 `1a0aefa4adfd32f0302c86d463bf326921efc416` 的真实工作流
`09c540fd-2cd8-4185-b414-46ef6bfdbc65` 已在 `codex.interpret` 以
`model_timeout` 终止。接收输入成功；没有 SceneIR、候选预览、资产解析、compile、replay
或物理 assessment。用户请求耗时 **600.1778141299728 秒**，不是 Genesis 耗时。

**尚未定位上游根因。** 与成功调用相比，二进制、模型、命令选项、文本输出 schema 和
共同提示前缀一致。超时调用只留下本地 `thread.started`、`turn.started`；这不证明请求已被
模型服务接受，更不能证明服务排队、模型推理耗尽、网络故障或权限等待中的哪一种发生。

本轮按 `diagnosing-bugs` 做证据对照。授权禁止新增模型调用，因此没有建立服务超时的
可重复反馈环，不能称因果诊断或修复验证完成。没有重试、扩大超时、简化案例、修改认证、
修改模型输出或项目业务源码。OpenAI Docs 仅用于核验 CLI 配置语义。

## 固定证据

- 失败根目录：
  `/home/jingxiang/bingsheng/canonical-workflow-local-box-20260913.262WUk`。
  [summary.json](/home/jingxiang/bingsheng/canonical-workflow-local-box-20260913.262WUk/summary.json)
  SHA-256 `365dd7c48e607884f62b74ff119d18cf3d11ae62a76149b24572e5e6bc7f4202`；
  [snapshot.json](/home/jingxiang/bingsheng/canonical-workflow-local-box-20260913.262WUk/snapshot.json)
  SHA-256 `3542a8bf6a6454a0532ac3970812b911a31d649a23a9396e9a32e6f26390d7bf`。
- 失败 attempt 相对路径：
  `state/attempts/09c540fd-2cd8-4185-b414-46ef6bfdbc65/bcd3d5b0-1017-45f6-8ab0-dfaf303aec77`。
- 成功 interpret 根目录：
  `/home/jingxiang/bingsheng/canonical-managed-interpret-v3-20260913.A4bXu1`，
  固定源码 `4fddb173958ef3ff5f40882899753fa2dedf4973`；attempt：
  `state/attempts/0d9dd28c-c82a-4b81-b6b9-0641bc2e7cd4/99b13415-9bd9-4311-bb06-7d92706be9f5`。
- 成功候选视觉根目录：
  `/home/jingxiang/bingsheng/canonical-asset-preview-20260913.OG1lLA/visual`；
  `call-0`、`call-1` 的 invocation/process/stdout/stderr/proposal，以及 `assessment.json`。
  这是旧候选视觉组件成功；其来源闭包债务不因本次引用而消失。

## 逐项对照

| 观察项 | 成功 interpret v3 | 本次 interpret 超时 | 成功候选视觉 |
| --- | --- | --- | --- |
| 实际模型 | `gpt-6-astra` | 相同 | 相同 |
| 可执行 SHA | `56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da` | 相同 | 相同 |
| CLI 实测版本 | 同 SHA 的当前二进制 `codex-cli 0.153.4` | 相同 | 相同 |
| schema 字节数 | 6702 | 6702 | 每次 891 |
| prompt 字节数 | 1537 | 1599 | 855 / 500 |
| 事件类型 | started → agent_message → completed | 只有 thread.started、turn.started | 两次均 completed |
| proposal | 5570 字节 | 文件不存在 | 130 / 129 字节 |
| stderr | 0 字节 | 0 字节 | 每次 29 字节：stdin 提示 |
| 时间 | 根结果 60.739505243 秒 | resume 600.176161420 秒 | 两次模型评估合计 12.583110182 秒 |

此前“约 75 秒 preview”不是纯模型延迟：视觉 assessment 合计只有上述 12.583 秒，不能将
Genesis 候选预览、模型评估和外围封装混成同一口径。成功 interpret 的输入 token 数为
18,942、输出 1,874；候选视觉分别为输入 18,099/18,013、输出 87/46。
失败没有 usage 事件，因此不能给它填入 token 数或推断模型是否实际开始推理。

两次 interpret 的 schema SHA 均为
`d4e5d67ea093cffc4fd1b2bb9415c31b6d81cf0bef2bddbb6acc47f1c4ed3e33`。
失败 stdout 101 字节，SHA
`4a70b0ec97cde985fbacc96abe21956868520dcd604439a20ee093906963c442`；
没有工具、审批、错误或完成事件。成功与失败 prompt 第一行完全一致，第二行只有请求内容、
seed 11→19、输入 hash/size 改变。两个不同输入不是受控复现实验；不能仅凭字节差很小就
证明模型工作量相同。

## 命令、配置与权限

各调用实测共享以下选项（证据为各自 `invocation.json`）：

```text
codex-real exec --json --ephemeral --ignore-user-config --ignore-rules
  --skip-git-repo-check --sandbox read-only --model gpt-6-astra
  --output-schema <attempt/schema>
  -c features.shell_tool=false -c features.multi_agent=false -c mcp_servers={}
  --output-last-message <attempt/proposal.json> -
```

候选视觉另外传入真实 PNG；文本两次没有图像。`codex.py` 从旧实现抽出了 `_invoke` 并新增
候选评估，但本次实际文本启动参数、stdin 管道、独立进程组和 timeout 处理与成功路径一致。
没有证据表明 timeout 是新增 resolver/Genesis 造成：它们尚未执行。

本机只读 `--version`、`exec --help` 与官方说明一致：`--ignore-user-config` 不加载
`$CODEX_HOME/config.toml`，但认证仍使用 `CODEX_HOME`。
`--ignore-rules` 只表示忽略 execpolicy `.rules`，不能把它扩大解释为清除所有系统指令或
所有配置层。[官方 CLI 说明](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

调用没有显式 `model_reasoning_effort` 或审批策略参数，也没有保存解析后的配置快照。
当前用户配置可见 model/effort 与被执行模型不同，但该用户配置被 CLI 选项排除；不能用
“当前配置 high”声称这次模型实际采用 high，更不能以它解释性能差异。官方有独立
`model_reasoning_effort` 配置字段；文档不提供此次请求的实际生效值。
[官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)

`subprocess.Popen` 未传显式 `env`，所以会继承父环境，但历史 attempt 未记录环境白名单摘要。
本次审计只检查当前环境变量是否存在，不读取或输出凭据值：当前 `CODEX_HOME`、
`CODEX_THREAD_ID` 存在；`OPENAI_API_KEY`、`OPENAI_BASE_URL`、常见代理变量、`RUST_LOG`
未设置。这是**审计时的环境**，不能倒推另外三个历史调用的实际环境。

没有出现审批或工具调用事件，因此“正在等用户批准”没有正面证据；同样，空 stderr
不能严格排除 CLI 内部尚未暴露的等待。禁止把改成危险权限或关闭认证作为试探性修复。

## 退出与公共网络检查

- 失败准确 PID/PGID `1462621` 已不存在。扫描 `/proc/*/stat` 的进程组字段，失败组以及
  三个历史成功组 `1406867`、`1438561`、`1439379` 均无存活成员；没有向旧 PID 发信号。
- `_invoke` timeout 分支先向自己新建的组发 SIGINT，最多等待 5 秒，再在必要时发 SIGTERM。
  本次从 budget 到工作流终态约 0.17 秒，外层命令正常结束，未执行 SIGKILL。
  现有 `process.json` 只写启动身份，没有 signal/exit 终态记录；具体发送的信号由源码路径
  推断，而不是由本次独立终态信号回执证明。
- 不含认证的公共小检查：`curl --max-time 15` 请求
  `https://developers.openai.com/codex/cli/reference/`，HTTP 308；TCP 0.008936 秒、
  TLS 0.021458 秒、总 0.034521 秒。它仅证明审计时公共文档站可达，**不验证模型服务、
  历史网络、认证、配额或实际请求 endpoint**。没有新增模型/API 推理调用。

## 有证据支持的通用改进候选

这些是下一步可实现的可观测性/可重复性修复，不是已验证的超时根因修复：

1. **记录时间化进度及终态进程回执。** 当前 stdout 只在退出后整体解析，started 事件没有
   本地接收时间，process.json 没有退出或信号。因此应记录单次调用的 spawn、stdin 完成、
   首事件、末事件、终态、信号和 reap 时间；保留同一个 600 秒截止时间，区分 transport
   未就绪、启动后无进展和已有输出但未完成。任何请求 ID/状态字段只在真实上游提供时写入。
2. **显式记录有效部署参数，不从用户配置猜测。** 部署固定模型、受限审批策略、effort 和
   provider/transport 身份；记录白名单环境的存在/非秘密指纹及来源，而非导出完整环境或
   auth 文件。这里不是建议降低 effort 以绕过案例，而是让随后对照具备可重复条件。
3. **补 transport 停滞与终态审计的攻击测试。** 用子进程边界 double 覆盖“仅 started 后挂起”、
   “出现 proposal 但无完成事件”、中断期间退出和 orphan 清理；必须保留原错误、部分产物与
   已消费预算。这样的测试只证明 controller/adapter 的处理，不证明远端模型超时已修复。
4. **下次获授权的真实诊断保留请求级遥测。** 若既有 CLI 支持安全的事件/连接状态遥测，先
   在相同部署、相同案例、相同预算内启用经脱敏的阶段记录，再做单变量对照。缺少上游回执时，
   不能将本次 `model_timeout` 自动分类为可重试网络故障。

本次未实施上述修改。原失败、输入、来源版本和所有成功历史保持不变；没有把尚未执行的
category 匹配、布局高度或新鲜观测问题写成本次失败原因。

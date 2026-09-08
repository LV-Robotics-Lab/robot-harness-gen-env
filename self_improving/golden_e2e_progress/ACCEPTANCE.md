# Golden E2E 验收合同草案

状态：`pending_user_confirmation`

## 证据等级

| 等级 | 名称 | 可以声称 | 不可以声称 |
| --- | --- | --- | --- |
| E0 | contract-tested | schema/纯逻辑/攻击测试通过 | Codex、Genesis 或物理已运行 |
| E1 | package-verified | portable closure、static validation、asset decision 完整 | replay 或 sim-ready |
| E2 | Genesis rigid replay | 原生 load/build、collision、非零 step、物理与连续媒体门通过 | robot policy-ready |
| E3 | Genesis robot-policy ready | reset/action/observation/termination/trajectory 真实 probe 通过 | 训练 policy 成功或真机能力 |
| E4 | promoted golden | E3 + validate publishable + promotion receipt + portable restart/replay | 未测试模态/任务/机器泛化 |

最终 golden case 必须达到 E4。E2 只是接入 Gujie 物理底座的中间结果。

## 每条 standalone modality golden line

text/image/video 各自必须至少覆盖：

- exact asset reuse；
- task-affordance digital cousin reuse；
- fresh asset generation；
- environment/package reuse；
- static validation 与完整 asset/CAS closure；
- portable materialization 到新路径；
- Genesis rigid replay；
- robot-policy probe 与 trajectory/data manifest；
- fresh image/video observation；
- Codex visual assessment；
- 至少一个诊断 + prompt revision 或显式 cross-modal fallback case；
- validate publishable 与 ephemeral promotion；
- 任何失败都保留 typed blocker/receipt，不得被最终成功覆盖或删除。

固定 deterministic golden cases 必须 100% 通过。随机输入是从 sealed case pool 通过记录 seed 的确定性
采样，不是运行后挑成功样本；全部选中 case 均计入分母。

## 综合 Codex 路由

一个 workflow 输入可只有 text，也可含任意 text/image/video 组合。外部 Codex 必须：

1. 创建/读取 workflow context；
2. 选择正确 exact qualified compile façade；
3. 等待并读取 ToolResult；
4. 调用 replay 和 fresh observation；
5. 对失败或视觉冲突提交引用充分的 diagnosis/prompt revision；
6. 必要时显式 fallback，并保留 predecessor；
7. 调用 matching validate；
8. 仅在 publishable 时请求 promotion；
9. 返回可由 Harness 独立重验的 workflow/evidence refs。

真实 Codex 评分门见 `AUTORESEARCH_SETUP.md`：route >= 0.95、closed loop >= 0.90、每模态保护门、
unsafe publication/evidence binding/unlogged failure 全为 0。

## 物理和 robot-policy 门

- Genesis runtime/adapter/source/dependencies 有精确身份与 capability attestation。
- scene build 启用 collision；所有动态对象非 static，除明确 support/ground/robot base。
- 非零 physics steps；支持、containment、穿透、稳定性使用 target-local 完整 footprint。
- baseline/half-dt 或同等扰动重放满足冻结一致性门；intervention 后状态不能冒充自由 pass。
- robot reset 可重复；bounded 非零 action 能引起符合 action contract 的 state change。
- observation shape/dtype/time/frame 与执行 step 绑定；contact 不是从截图推断。
- trajectory 逐 step 保存 action/observation/termination/hash；dataset manifest 可重读。
- 请求视频保存实际连续帧、总/唯一帧数、FPS、容器/帧摘要；start/end 图不够。

## Portable replay 门

- package 只含 CAS refs 与逻辑相对路径；绝对路径扫描为 0。
- 在新的随机 materialization root 可加载、replay、validate。
- 关闭进程并重启后，从 workflow repository/event/receipt/CAS 恢复相同权威状态。
- 所有 required artifact 可重读并复算 SHA/bytes/schema；缺一项 fail closed。
- deployment-specific runtime locator 只进入 materialization/runtime receipt，不进入 package identity。

## 资产门

- exact reuse 的 bytes/closure/representation digest 和 Genesis qualification 全部匹配。
- digital cousin 明确记录 exact 差异、任务相关等价维度和验证阈值。
- fresh generation 先 staging；generation QC 或 render 不允许写权威 asset library。
- collision provenance、scale、mass/inertia、stable pose/settle 与当前 representation digest 绑定。
- promotion 原子写入；crash 不留下半发布 ledger/package。

## MCP/Codex 门

- 使用官方 MCP client/server 完成真实协议协商、tools/list/call 和 resources read。
- tools catalog 只来自 frozen qualified RegistrySnapshot；schema/tool digest committed。
- stdio stdout 只有协议 frame，日志走 stderr；timeout/cancel/断线恢复有测试。
- 至少一个真实 `codex exec` run 的 JSONL 能与 MCP server call receipts 对账。
- ExternalAgentReceipt 只声明可证明的 client/version/config/transcript/tool-call digests，不伪造模型权重
  snapshot 或隐藏推理。
- Codex 自报 physics pass/publishable、引用 stale media 或未读完整视频均被拒绝。

## 代码与文档门

- clean committed checkout 的 `pytest -q` 全绿。
- 新 workflow/MCP/IR/asset-repair core statement + branch coverage 100%；真实 adapter另有 integration
  test 和 runtime evidence。
- schema exporter、ruff、format/compile、`git diff --check` 通过。
- 每个 feature 独立提交，测试与证据同提交；不夹带现有用户修改。
- walkthrough 的每一步链接 spec、主要代码、测试和运行证据；root `AGENTS.md` 链接 walkthrough 与
  progress directory。
- 最终 limitation 明列：未运行真机；未运行的 Genesis backend/profile、输入 family、机器人任务和
  external asset generator 均不作完成主张。

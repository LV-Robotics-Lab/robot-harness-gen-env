# Replay → VLM 最小闭环

可以把这条链路理解成“物理回放完成后，把已经封存的监控画面交给一个只读检查员”。检查员可以说
画面像不像任务描述、有没有明显悬空或穿透，也可以说看不清；它不能改写物理回放的通过/失败。

入口 `ReplayVlmAssessmentApplication.assess` 只接收一个已有 replay run ID。程序先从 SQLite 恢复
该 replay 的 Invocation 与 terminal RunState，要求它是成功的 `text2env.replay@1.0.0`；再从绑定的
package manifest 找到 `resolved_scene.json`，复算 scene digest，并核对 runtime evidence 指向同一
scene。

随后程序固定选取 `observer_start`、`observer_mid`、`observer_end` 与 `preview_head`。这四个引用必须
同时出现在 replay output、terminal artifacts 与同一 CAS 中，实际 bytes 和 SHA-256 都一致后才会
启动模型。CAS 缺图、文件被改或输出/终态不一致都会在 GPU 调用前阻断。

Qwen 在单独 Python 进程中运行。进程固定 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，模型
revision、snapshot manifest、content manifest、roster、prompt 与执行源码都进入 Invocation dependency
闭包。成功调用后，原始回答和 `harness.replay_vlm_assessment.v1` 收据进入 CAS；事件流依次记录证据
绑定、推理启动、收据发布和完成。

当前版本是 `text2env.replay_vlm@0.1.0` qualification candidate，不是 production Skill。输出字段
`claims_physical_pass` 被固定为 false；即使模型返回 “pass”，它也只是视觉建议。2026-09-06 的真实
can-on-plate 调用完成了一次离线 3B 推理，但模型输出带 Markdown fence 且 checks 形状不合约，系统
如实保存为 `format_invalid`。这证明接线和失败留痕已通，同时说明 prompt fallback 仍是下一项工作。

实跑编号、CAS 路径、模型收据、资源用量和完整命令见
[`docs/evidence/replay-vlm-minimal-loop-20260906.md`](../../docs/evidence/replay-vlm-minimal-loop-20260906.md)。

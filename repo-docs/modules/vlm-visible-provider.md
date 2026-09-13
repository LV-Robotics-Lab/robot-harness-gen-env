# Local typed Qwen 可见语义 Provider

`self_improving/studies/vlm_fallback_prompt_optimization/visible_qwen_provider.py` 是预注册研究的
本地 provider **候选**。它把 runner 已定义的 visible request 接到内容绑定的 Qwen backend，但当前
只完成代码与合同预检：没有加载真实模型、没有推理、没有使用 GPU 或网络，也没有取得执行或生产授权。

## 只接哪两个 arm

| arm | 固定 model role | 当前语义 |
| --- | --- | --- |
| `A1_typed_abstaining_critic_3b` | `primary_local_vlm` | typed/abstaining 主实验候选 |
| `A2_typed_abstaining_critic_7b` | `confirmatory_model_size_ceiling` | 只作模型尺寸上界确认 |

`A0_current_critic_3b` 不会借用这条 typed 路径。provider 和 concrete backend 都会在加载模型前
失败关闭，并要求独立的 frozen baseline provider；否则 A0 与 A1 共用实现会破坏预注册对照。

## 一次调用信任什么

`LocalQwenVisibleProvider` 先核对 arm、model role、revision、snapshot manifest、model-content
manifest、roster、local snapshot、processor 配置、provider 源码和 backend identity。每张图片再按
runner 声明的 artifact 顺序绑定，核 canonical relative path、稳定 bytes、大小、SHA-256、格式、尺寸
和 decoded pixels；上限是 6 张、单张 32 MiB、合计 64 MiB。raw response 上限是 1 MiB。

实际 Transformers adapter 还有四道边界：

- dependency discovery/import 前以及每次 generate 前都要求 `HF_HUB_OFFLINE=1` 和
  `TRANSFORMERS_OFFLINE=1`；
- model/processor 都使用 `local_files_only=True`、`trust_remote_code=False`；
- Python、torch/transformers/accelerate、入口源码与指定 CUDA device 的 selected facts 会在
  identity seam 实时复核；这是 partial fingerprint，`dependency_closure_complete=false`；
- CUDA event 和 peak memory 只在指定 device/current stream 内计量。代码没有据此取得 bitwise GPU
  parity 资格，也不会把 request seed 外推成 deterministic-kernel 证明。

provider 无论得到 typed result、abstain 还是 format-invalid，都保持
`claims_physical_pass=false`。它只提供 `visible_semantics_advisory`；接触、support、containment、drift
与 articulation 仍由独立 static/runtime gate 判定。

## 这次预检证明到哪里

冻结候选的专项测试为 `143 passed`，`571` statements + `200` branches 全部覆盖；相邻 runner 为
`230 passed`。显式排除两个 frozen-inventory 检查后，study 是 `665 passed, 2 deselected`。

未过滤 study **不是全绿**：结果是 `665 passed, 2 failed`。两个失败都在 protocol 的 frozen
inventory 检查中遇到当前工作树缺失的 protected PEARL images。这个外部证据缺口不能被 deselect
结果覆盖，也不是 provider 效果结论。

完整命令、源码/测试摘要与边界见
[2026-09-02 provider preflight](../../docs/evidence/vlm-visible-provider-preflight-20260902.md)；机读记录见
[companion JSON](../../docs/evidence/vlm-visible-provider-preflight-20260902.json)。

## 仍未跨过的门

当前 `model_loaded=false`、`inference_count=0`、`GPU_used=false`、`network=false`、
`production_eligible=false`、`execution_authorized=false`。双盲标注仍是
`pending_blinded_annotation`；本 slice 没有修改 amendment、runner、experiment spec、
runner source manifest、run log 或 sealed annotation manifest。因此这里没有质量/效果主张，也没有
完整依赖闭包或 GPU 位级一致性结论。后续只有在独立完成标注、显式冻结研究 authority 并授权执行后，
才能用真实运行 receipt 回答模型效果问题。

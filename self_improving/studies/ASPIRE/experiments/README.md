# ASPIRE-style held-out Harness 机制基准

`heldout_harness_benchmark.py` 是一个完全离线、确定性的项目原生基准。它回答一个很窄、可证伪的
问题：在同一个一次动作预算内，从开发 trace 晋升出来的、经过验证的 trigger-specific memory，
是否比 frozen harness 和“只看当前 trace、无跨 run 记忆”的 reactive harness 更能修复新的契约
失败，同时不放松权威 validator。

它不是 ASPIRE 论文结果复现，也不模拟 LLM 代码生成质量。它只验证“trace → 候选动作 → 开发集
验证 → 冻结 skill memory → held-out 检验”这一机制能否安全接到当前项目。

## 固定协议

- 六个失败家族：target-local support、containment、nested dynamic contact、runtime evidence
  completeness/video/visibility/motion/drop/penetration、runtime scene identity/hash binding、workspace
  feasibility proxy。
- 每类默认 2 个开发样例、20 个 held-out 变体；开发和 held-out seed/ID 空间分离。因此默认主评测
  是 120 个故障样例，另有 120 个 held-out clean controls。
- 三个 arm 都最多调用一个候选动作、总共最多两次 validator：
  - `frozen_baseline`：原样重试；
  - `reactive_trace_no_memory`：只根据当前失败 trace，runtime 类失败统一重新 replay，不保留跨 run
    状态；
  - `validated_trigger_memory`：在 reactive 基础上检索开发集验证过且 clean regression 为零的
    trigger → action 记录。
- memory 晋升只读取开发 case；记录以 case receipt 哈希保存，不包含 held-out outcome。held-out 前后
  会比较 library SHA-256，若发生改变直接报错。
- policy 只能看到 opaque case ID、当前 validation status、失败 check 与归一化 trigger、剩余预算。
  它看不到 family、split、variant、clean reference、expected outcome/action 或 held-out 结果。
- 物理/发布 gate 始终是 `scene_gen.validator.validate_resolved_scene` 与公共
  `Text2EnvValidateOutput` schema。基准没有 render，也不会把图像当物理证据。

主指标是六类 held-out completion rate 的 macro average；同时报告 worst-family rate、unsafe publish、
clean regression、integrity/hash-binding accuracy、动作/验证预算与每类明细。

H-ASPIRE 与 B1 的比较还会输出：

- 精确 paired improved / regressed / tied 数；
- 每个 arm 的精简 `fault_case_outcomes`（opaque case ID、family、动作、completion、unsafe publish、
  实际/最大预算）；
- 固定 seed、默认 10,000 次重采样的 family-stratified paired bootstrap。每次只在同一 family 内对
  paired case delta 做有放回抽样，再按 mHRC 定义对六类等权平均；
- 预注册 E2 keep rule 的逐项 machine-readable verdict。

Keep verdict 只允许写成“保留 synthetic harness mechanism candidate”。即使所有 gate 通过，输出仍会
把 paper-scale ASPIRE 与 simulator physics improvement 明确标成 `false`。
Bootstrap CI 只条件化在这组固定的 synthetic family/case 上；若同一 family 的 paired delta 完全
一致，区间可能退化为单点。它不估计真实 simulator、任务分布或部署分布的不确定性。

## 运行

从仓库根目录运行完整默认实验：

```bash
python self_improving/studies/ASPIRE/experiments/heldout_harness_benchmark.py \
  --out self_improving/studies/ASPIRE/artifacts/heldout_harness_benchmark.json
```

默认 bootstrap 参数已冻结为 `--bootstrap-seed 260700272` 和
`--bootstrap-resamples 10000`；参数会完整写入结果，若为诊断而覆盖，不能与预注册默认 run 混报。

运行自测：

```bash
pytest -q \
  self_improving/studies/ASPIRE/experiments/test_heldout_harness_benchmark.py
```

缩小样本做快速检查：

```bash
python self_improving/studies/ASPIRE/experiments/heldout_harness_benchmark.py \
  --development-per-family 1 \
  --heldout-per-family 2
```

同一代码可在 runtime evidence 绑定修复前后运行。若生产 validator 尚未拒绝 missing/wrong digest 或
wrong scene ID，完整性攻击会被计为 `unsafe_publish`，而不会被伪装成成功；gate 补齐后，同一用例
会先失败，再由有预算的 replay action 产生正确绑定证据。

## 明确限制

- runtime evidence 是确定性合成的契约 fixture，不是 RoboTwin/SAPIEN rollout；不能据此声称物理
  robustness、policy success 或 sim-to-real 改善。
- `replay_runtime` 在这里是一个不计 wall-clock/GPU 成本的抽象动作；论文尺度的成本结论必须用
  真实服务和 rollout 重测。
- 三个 policy 是控制流机制代理，不是 Claude/ASPIRE actor；该基准只隔离 memory/validation 机制，
  不评价语言模型推理能力。
- feasibility 只覆盖当前 validator 可观测的 workspace 投影 proxy；不可解任务、资产缺失和真实
  动力学可行性需要独立的 simulator benchmark。

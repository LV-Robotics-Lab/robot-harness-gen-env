# VLM 可见校正与 typed fallback prompt 优化实验预注册

冻结时间：2026-08-31 02:23:10 UTC  
研究 ID：`vlm-fallback-prompt-optimization-2026-08-31`  
状态：**预注册完成；VLM 推理、仿真和 fallback 实验均未开始。**

## 1. 研究问题与不可逾越的边界

这项研究只回答两个问题：

1. 一个固定版本的本地 VLM，能否比当前 critic 更可靠地发现**图片中确实看得见**的对象、
   朝向、悬浮、穿插、遮挡和语义关系问题，并给出结构化校正建议？
2. 把 VLM 的可见问题与编译器、资产库、求解器、回放、证据绑定等 typed failure 合并后，
   能否比“原样重试”或“泛化地改一下 prompt”更安全地选择 fallback，并提升可恢复任务的
   端到端完成率？

研究不允许回答“图片看起来合理，所以物理通过”。`ResolvedSceneSpec`、资产入库、fresh replay、
完整 runtime evidence、哈希绑定以及确定性 validator 才是物理结论。现有 ASPIRE 研究也已把
robust completion 定义为 authoritative validator pass、exact identity binding 和无越权发布的
合取，而不是 agent 文本或渲染分数（`self_improving/studies/ASPIRE/experiment_protocol.md:7-40`）。
USG 研究同样把真实 SAPIEN/PhysX label 与未发布权重、随机权重 tensor probe 分开
（`self_improving/studies/usg_env_quality_reproduction/README.md:11-22`）。本研究继承这两个边界。

因此：

- VLM 只拥有 `visible_semantics_advisory` 权限；
- VLM 的 `pass` 不能覆盖 static/runtime fail、incomplete、missing evidence 或 asset admission fail；
- 看不清必须 `abstain`，不能把不确定性转写成 pass；
- prompt fallback 不得删掉、放宽或替换原任务中的对象、数量、颜色、关系、区域或 articulation；
- 缺依赖、缺 capture、不可行几何、身份不匹配分别走 typed route，不能靠改措辞掩盖；
- 任一 unsafe publication 使该 case 失败，并阻止候选晋升。

## 2. 现有基线事实

### 2.1 当前 critic 接口

当前 `scene_gen.rendered_critic` 已有五项固定检查：object presence、support relation、
penetration/floating、articulation state、overall prompt match
（`scene_gen/rendered_critic.py:15-22`）。prompt 明确只用 visible evidence，并声明 static/runtime
另行处理（`scene_gen/rendered_critic.py:44-91`）。输出缺 check 时会逐项补问
（`scene_gen/rendered_critic.py:224-254`）；resolved scene 与每张输入图已经分别保留 digest
（`scene_gen/rendered_critic.py:189-210`）。这些是 A0，不会为了实验事后重定义。

本地 Qwen 路径已经强制 Hugging Face/Transformers offline、`local_files_only=True`、greedy decode、
`max_new_tokens=768`（`scene_gen/rendered_critic.py:143-186`）。CLI 只暴露 `qwen_local`，默认 3B
（`script/run_rendered_critic.py:14-30`）。当前不足是：调用只接收 model name，未冻结 revision 与
完整 snapshot manifest；没有显式 abstain；没有统一 prompt lineage、时延、显存、token 和 raw/parsed
digest receipt；也没有把 visible failure 与非视觉 typed failure 分流。

### 2.2 本机可执行模型，不假设远程 key

`experiment_spec.json` 冻结了本机已存在的两个 snapshot：

| 用途 | 模型 | HF revision | snapshot-manifest SHA-256 | 大小 |
| --- | --- | --- | --- | ---: |
| 主实验 | Qwen2.5-VL-3B-Instruct | `66285546d2b821cf421d4f5eb2576359d3770cd3` | `b0f4cf794ec552f5e23de019107801e5adfec633d3b5c74564cdf6b65e9f1cad` | 7,520,892,432 B |
| 模型尺寸上界 | Qwen2.5-VL-7B-Instruct | `cc594898137f460bfe9f0759e9844b3ce807cfb5` | `3b785e700f59d59733b8ba4c92dedacbbd9fbaac642fc8a8a49b5af7545fd145` | 16,595,981,281 B |

manifest digest 是排序后的 `{path, resolved HF blob id, size_bytes}` canonical JSON 的 SHA-256；
weight blob id 本身是 HF LFS SHA-256。运行环境固定为现有 `robotwin-5090`，其已安装 torch
2.11.0+cu128、transformers 4.57.6、qwen-vl-utils 0.0.14、accelerate 1.14.0；不安装新依赖，
不访问网络，不假设任何 API key。项目声明的 VLM 依赖范围见 `pyproject.toml:25-32` 与
`requirements-vlm.txt:1-4`。

### 2.3 可冻结样本，而不是口头声称“有数据”

机器可读 spec 逐 case 记录 exact artifacts 和 bundle SHA-256：

- 实验 A：39 个逻辑 render unit，213 个总 artifact binding 中 A 占 144 个。来源为 13 个
  asset probe、4 个 USG render comparison、1 个 ASPIRE 真 replay、15 个 SceneAgent unit 和
  6 个 OpenXSim Text2Env unit。
- 实验 B：36 条已有记录，来源为 11 个 prompt × 3 seeds 的 prompt matrix（33）与 3 条已记录
  fallback/blocker。历史矩阵确实是 seeds 7/31/73、10 个 positive prompt 真 replay、一个
  infeasible prompt 的三次 expected rejection（`docs/evidence/prompt-matrix-20260719.md:13-43`）。

历史 Stage 5 文档提到过四例 VLM 结果，但对应 `rendered_critic.json` 当前不在工作树中，因而不能
当 gold 或 baseline response。物理 report 也不冒充图片语义 gold。

## 3. 样本冻结与防泄漏

### 实验 A：39 个 render unit

| split | 数量 | 用途 |
| --- | ---: | --- |
| train | 13 | asset probe；只允许设计 schema/prompt，不训练模型权重 |
| dev | 12 | 11 个 SceneAgent + 1 个 ASPIRE 真 replay；按预定字典序选择唯一候选 prompt |
| test | 14 | 4 SceneAgent、6 OpenXSim、4 USG；候选 prompt SHA 写入日志前不得打开 gold |

同一资产的 900/2700、同一 prompt 的不同 seed、OpenXSim 六个 can/basket 变体、USG 四个变体
使用共同 `group_id`。bootstrap 以 group 为采样单位，禁止把近重复截图当独立样本扩大显著性。

### 实验 B：36 条 route/fallback 记录

| split | 数量 | 用途 |
| --- | ---: | --- |
| train | 12 | 四个英语 prompt family × 三 seeds |
| dev | 10 | 三个新 relation family × 三 seeds + 一个 task-API blocker |
| test | 14 | infeasible、articulated、两个 bilingual family × 三 seeds + 两个 generation blocker |

同一 prompt 的三个 seeds 永远在同一 split 与 cluster。33 条矩阵记录中，30 条是 clean compile
controls，三条是同一 infeasible family 的正确拒绝；另外三条是 task API、forge capture、material
multiview 的真实 blocker。它们主要检验“不应乱改 prompt”的安全边界。若 fresh baseline 没有至少
8 个独立、可恢复的失败 group，则只报告 route accuracy 和 exact outcomes，**不作 completion
提升的优越性声明**。

## 4. Gold 标注协议

在任何 arm 输出前，两名标注者独立查看冻结的图片与任务意图，但看不到：arm、模型输出、
runtime pass/fail、fallback 结果。每项标注为 `pass`、`fail`、`not_applicable` 或
`insufficient_view`。两人不一致时由第三人仲裁；报告 raw agreement、逐 check Cohen's kappa 和
全部 disagreement 数量。

需要标注八项：对象存在、对象身份、数量、朝向、table contact、可见穿插/悬浮、空间关系、
整体 prompt match。`insufficient_view` 不参与该 gold check 的准确率分母，但对应模型必须
`abstain`；若模型在不可观察项上给 pass，则记录为 overclaim。物理 report 只在独立 gate 阶段使用。

## 5. 实验 A：VLM 可见语义校正

### 5.1 假设

在同一 3B snapshot 上，带 typed schema 与显式 abstain 的 critic 相比当前五项 prompt，能把
sealed test 的 visible-failure macro F1 提高至少 0.10，同时不产生 unsafe visible pass。7B 只用于
估计模型尺寸上界，不默认替换 3B。

### 5.2 Arms

- **A0 current critic 3B**：冻结源提交中的 `build_critic_prompt` + `review_rendered_scene`。
- **A1 typed/abstaining critic 3B**：最多三次 train-only prompt revision。dev 选择顺序固定为：
  先零 unsafe pass，再最高 macro F1，再在 coverage ≥0.80 中最高 selective accuracy，再低时延，
  最后更短 prompt。选定后先把完整 prompt bytes 与 SHA-256 写日志，才解封 test gold。
- **A2 typed/abstaining critic 7B**：prompt bytes、处理器参数、输入、解码完全等于 A1，仅换固定 7B。

每次只允许一次 format-only repair；repair 不得补充新的事实、换图或改变任务。缺图、遮挡、
视角参照不清、分辨率不足、需要隐藏物理状态、一次 repair 后仍不合 schema 都返回
`review_required/abstain`，不触发 prompt 修改，也不产生 pass。

### 5.3 指标、统计与成功门槛

主指标是按 failure check 计算、再按 group 加权的 macro F1。次指标为 coverage、selective accuracy、
逐 check precision/recall/F1、schema-valid rate、unsafe visible pass、盲审 typed correction precision、
时延和 peak VRAM。A1-A0 使用 paired group-cluster percentile bootstrap，10,000 次，seed
20260831，95% CI。

候选仅在以下全部满足时通过：

- macro F1 增量 ≥0.10 且 95% CI 下界 >0；
- coverage ≥0.80；
- unsafe visible pass =0；
- schema-valid rate ≥0.98；
- typed correction precision ≥0.80；
- 没有将 VLM 结果写成物理 pass。

A2 只有在同样全部通过、相对 A1 另增 ≥0.05 macro F1、平均时延不超过 2× 时才可替代 A1；
否则只报告为 ceiling。

### 5.4 预算

最多 3 个 train prompt revision、118 次 base VLM invocation、每次至多 1 次 format repair、最多
6 张图、768 output tokens、3 GPU-hours、30,000 MiB peak VRAM、0 次付费远程调用。超预算的 arm
按 preregistered failure 记录，不能临时扩大预算。

## 6. 实验 B：typed failure routing 与 prompt fallback

### 6.1 第一性原理路由

只有 `visible_semantic_mismatch` 与 `unsupported_language_surface` 允许改 prompt。其他 failure 必须
作用在真正失效的层：

| failure | 允许动作 | 禁止动作 |
| --- | --- | --- |
| visible semantic mismatch | 保持 typed intent，加入具体可见校正 | 凭图片声称物理通过 |
| unsupported language surface | canonicalize 为同义 typed request | 删除难约束 |
| asset catalog miss | reuse-first；确实 miss 才 generate-on-miss | 用相似类别冒充 |
| infeasible geometry | typed abstain / 请求新约束 | 放宽区域后称原任务成功 |
| runtime support/containment/contact | 修 placement、asset 或 runtime 参数并 fresh replay | 只换说法 |
| evidence identity/completeness | rematerialize/replay | 沿用旧 receipt |
| missing task API/capture/multiview | 精确 blocker | 反复调用 VLM/LLM |

### 6.2 Arms 与 matched budget

- **B0 unchanged retry**：收到 failure code 后，把原 prompt 原样重试一次。
- **B1 generic repair**：收到不带类型的 failure summary，生成一次泛化的 preserve-intent prompt。
- **B2 typed route**：只看 trusted state、typed failure、资产可用性，以及适用时 A1 的 visible report；
  从冻结 route vocabulary 选择一项，并至多输出一个 revised prompt。看不到 gold route。
- **B3 oracle ceiling**：看到 gold route，只作描述性上界，永不部署、永不参与 promotion。

所有 deployable arms 每 case 最多两次总尝试、一次 prompt rewrite；同 seed、同 compiler/runtime 版本、
同 tool contract。任何 revised prompt 都须 canonicalize 后与原 typed intent 的对象、数量、颜色、关系、
articulation 与 region constraints 完全一致，否则记 unsafe publication。

### 6.3 Outcome、统计与成功门槛

`robust_completion=1` 只有在 intent 未变、compile 与 admission 成功、fresh replay 绑定 exact resolved
scene、authoritative static/physical validator pass 且无隐藏 blocker/abstain/unsafe publication 时成立。
不可恢复 case 的正确结果是 exact typed abstain，不计 completion。

主指标为 36 条记录的 group-weighted route accuracy：先在每个 `group_id` 内算 exact-route accuracy，
再对 group 等权平均。B2 vs B1 的 paired exact McNemar 每个 group 只产生一个严格二元值；只有该
group 的每条记录都命中 exact gold route 才算 correct。条件主指标是可恢复 baseline failures 的
robust-completion delta；每个 cluster 贡献组内平均差，只有 ≥8 个独立失败 group 时才使用 10,000 次
paired group bootstrap（seed 20260831，95% CI）作优越性检验。

晋升要求同时满足：route accuracy 增量 ≥0.15 且 McNemar p<0.05；有足够失败 group 时 completion
增量 ≥0.10 且 CI 下界 >0；clean controls 100% 不回归；不可恢复项 100% safe abstain；intent 100%
保持；unsafe publication=0；同一 typed input 三次的 route 与 prompt SHA 3/3 一致。

预算：每 case/arm 最多 2 attempts、1 rewrite；全研究最多 216 compile attempts、72 fresh physical
replays、每 replay 900 steps + 120-step contact window、72 次 visible VLM、12 GPU-hours、0 次远程付费。

## 7. 日志、receipt 与失败处理

`run_log.jsonl` 是 append-only。每个 decision、model invocation、tool call、gate、artifact 都写独立
event，schema 固定为 `vlm_fallback.run_event.v1`。必需字段与 prompt/model/image/resolved scene、时延、
显存、token、raw/parsed output digest receipt 已在 `experiment_spec.json.logging_contract` 冻结。

日志中不写 API key、token、cookie、完整环境变量或私人消息。失败也保留 raw-response digest、解析错误、
资源 receipt 与 partial output，但这些一律 untrusted。OOM、timeout、invalid JSON、missing image、
missing dependency、budget exceeded 都是可分析 outcome，不能删除后重跑到“成功”为止。若确需修复
runner bug，先追加 deviation event，冻结修复 commit，并从头重跑受影响的全部 matched arms；原记录保留。

## 8. 运行顺序与停止规则

1. 验证 spec、模型缓存和 213 个 artifact binding；不推理。
2. 完成 A 的双盲标注并封存 test gold。
3. 运行 A0 与 train-only A1 prompt revisions；按冻结规则在 dev 选一个 A1。
4. 记录 A1 prompt SHA 与完整 model/processor receipt，再一次性运行 A sealed test 与 A2 ceiling。
5. 先在 B 的 36 条 frozen records 上运行 route-only matched arms。
6. 只对实际可恢复 failure 做 fresh compile/admission/replay；不可恢复项验证 exact blocker。
7. 用 `protocol.py` 的冻结指标实现分析；任何阈值未过都保留结果并拒绝 promotion。

以下任一条件停止昂贵执行：artifact/model digest 不符；gold 在 prompt 冻结前泄漏；network 被访问；
预算到顶；物理 gate 被 VLM 覆盖；arm 输入/attempt budget 不匹配。停止不等于结果失败被“修好”，而是
作为 protocol violation 报告。

## 9. 当前可执行但不昂贵的基线检查

本预注册阶段只允许：

```bash
python self_improving/studies/vlm_fallback_prompt_optimization/protocol.py \
  validate self_improving/studies/vlm_fallback_prompt_optimization/experiment_spec.json \
  --repo-root .

python self_improving/studies/vlm_fallback_prompt_optimization/protocol.py \
  inventory self_improving/studies/vlm_fallback_prompt_optimization/experiment_spec.json

python -m pytest -q \
  self_improving/studies/vlm_fallback_prompt_optimization/test_protocol.py
```

它们只做解析、哈希与指标单测，不加载 Qwen、不启动 SAPIEN、不修改 harness。真正 A/B run 必须在
后续 feature 中实现 callback/receipt 接线后，严格按本预注册和 append-only 日志执行。

## 10. 预先声明的限制

- 39 个 render unit 的来源异质，部分只有单视图；abstention 可能高，这是待测事实，不补造视图。
- 36 条 B 记录多数是 clean controls，天然 recoverable failure headroom 可能不足；不足就不作提升声明。
- 历史 runtime success 与本次模型能力无关；B 的成功必须 fresh evidence。
- 固定本地 Qwen 的结论不外推到其他 VLM、真机、任意资产或开放语言。
- prompt optimization 是上下文/协议优化，不是模型权重训练；“train”只是 prompt-development split。
- 即使 A/B 都过门槛，也只支持这一冻结分布上的候选 promotion，仍需回归门禁与真实 replay 才能接入
  production harness。

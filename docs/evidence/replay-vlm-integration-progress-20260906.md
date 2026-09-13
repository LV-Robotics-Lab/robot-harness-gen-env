# Replay 接入 VLM 进度审计（2026-09-06）

## 一句话结论

当前 `text2env.replay@1.0.0` 已经能在固定部署中完成真实 RoboTwin/SAPIEN 回放，并留下
经过解码、哈希绑定和资格核验的物理与媒体证据；但是**正式 Harness replay 尚未调用 VLM**。
仓库里确实还有一条旧 Demo 路径，会在 runtime 之后调用本地 Qwen 看四张图片，但它没有进入
新版 ReplayApplication、Registry、CAS、qualification 或 replay receipt，因此只能算历史旁路和
能力烟测，不能算“replay 已正式接入 VLM”。

新的 VLM 校正与 prompt fallback 方向已经完成预注册、实验运行器、双盲标注工具、模型内容绑定和
离线 Qwen provider 候选；但正式研究推理次数仍为 0，实验数据尚未产生，production qualification、
量化提升结论和晋升都没有完成。

## 用一个简单心智模型看当前状态

可以把系统看成三段：

```text
可信 replay              VLM 观察员                  fallback / 晋升
真实仿真、图片、视频  ->  看可信帧并给类型化建议  ->  重试、改 prompt、量化过门
        已完成                    尚未接线                    只有合同，没有结果
```

VLM 在这里应当是“观察员”，不是“物理裁判”。它可以指出少物体、外观不对、明显悬空或画面看不清，
但不能推翻接触、支撑、穿透、漂移等真实仿真门禁。这个边界已写入 Harness 实施日志和 VLM provider
候选：渲染/VLM 不能替代连续运行时证据，provider 也始终声明 `claims_physical_pass=false`
（[`IMPLEMENTATION_LOG.md`](../../self_improving/harness/IMPLEMENTATION_LOG.md#L8-L11)，
[`visible_qwen_provider.md`](../../repo-docs/modules/vlm-visible-provider.md#L34-L36)）。

## 分项进度

| 小目标 | 当前状态 | 实际证据 | 还差什么 |
| --- | --- | --- | --- |
| Replay schema 与执行链 | 已完成 | Replay 输入是环境包和 runtime 配置，输出只有 runtime evidence 与 replay artifacts；production application 经 Registry 调用固定 `text2env.replay@1.0.0`（[`text2env.py`](../../self_improving/harness/schemas/text2env.py#L132-L152)，[`replay_application.py`](../../self_improving/harness/replay_application.py#L89-L129)） | 不缺 VLM 也能独立完成物理回放，这是有意的信任边界 |
| Replay 固定资格 | 已完成 | 固定 can-on-plate 资格执行两次真实 900-step / 120-frame 回放，随后 production application 又执行一次；物理报告均 pass（[`replay-production-qualification-20260902.md`](replay-production-qualification-20260902.md#L18-L42)） | 资格是部署内、固定案例，不代表任意输入或可移植安装 |
| 安装后 replay CLI | 已完成固定案例 | 真实 CLI run `0000f972-15e7-4ada-aec6-707e8973acde` 成功，900 步、120 帧、100 个 source-unique frames、validation pass（[`replay-fixed-qualified-cli-20260902.md`](replay-fixed-qualified-cli-20260902.md#L49-L66)） | 还不是通用 Workbench 动态 replay，也不含 VLM |
| Replay 媒体可信化 | 已完成 | PNG/MP4 先作为不受信任 bytes，再由受限 FFmpeg 完整解码；合格后才晋升媒体类型（[`replay-handler-integration-20260831.md`](replay-handler-integration-20260831.md#L27-L38)） | 下一步 VLM 必须只读这些已验真的 CAS 媒体，不能直接读任意路径 |
| 正式 replay → VLM 调用 | **未实现** | Replay handler 的依赖只有 capability、executor、handler config、media verifier、runtime assets；ReplayOutput 没有 VLM report/ref。production factory 也只组装 runtime executor 与 media verifier（[`replay-handler-integration-20260831.md`](replay-handler-integration-20260831.md#L10-L25)，[`text2env.py`](../../self_improving/harness/schemas/text2env.py#L139-L152)，[`replay_application.py`](../../self_improving/harness/replay_application.py#L21-L51)） | 新增 replay evidence consumer、VLM assessment schema、CAS 回执和资格 |
| 旧 Demo 的 runtime → Qwen | 历史上跑通过，但属于旁路 | Demo 先执行 `run_scene_runtime.py`，再把三张 preview 和 `observer_start.png` 交给 `run_rendered_critic.py`（[`demo/app.py`](../../demo/app.py#L251-L304)）；历史记录称一次 can-on-plate Demo 完成 local Qwen critic（[`physics-acceptance-20260717.md`](physics-acceptance-20260717.md#L41-L52)） | 它只看四张静态图；不走新版 Registry/CAS/qualification，当前历史 `rendered_critic.json` bytes 也不在工作树中 |
| 旧 VLM critic 实现 | 有可调用实现 | `review_rendered_scene` 绑定 resolved digest 和图片 SHA，要求五项可见检查；漏项会逐项补问（[`rendered_critic.py`](../../scene_gen/rendered_critic.py#L15-L22)，[`rendered_critic.py`](../../scene_gen/rendered_critic.py#L189-L254)） | 这里的“repair”只是补齐模型遗漏的 JSON check，不是修改场景或 prompt 后重新 replay |
| System 2 动态 replay | 合同完成，部署实跑未完成 | Acquirer 能把 promoted input 交给 ReplayApplication 并重读持久状态/CAS；但证据明确测试替换了 application factory，真实 deployed dynamic replay 未运行（[`system2_replay_evidence_acquisition.py`](../../self_improving/system2_replay_evidence_acquisition.py#L83-L173)，[`system2-replay-evidence-acquisition-20260902.md`](system2-replay-evidence-acquisition-20260902.md#L35-L48)） | 还需在完整外部 CAS、runtime、RoboTwin 资产和 delegated cgroup 上执行动态案例；本层也没有 VLM |
| System 2 replay snapshot assessment | 已完成确定性重算，不是 VLM | Recorder 内部调用 `System2ReplaySnapshotValidator`，后者再调用 `ValidateV2SnapshotAdapter` 重算报告；模块声明它不是 fresh observation、物理成功或发布决定（[`system2_replay_snapshot_assessment.py`](../../self_improving/system2_replay_snapshot_assessment.py#L1-L14)，[`system2_replay_snapshot_validation.py`](../../self_improving/system2_replay_snapshot_validation.py#L76-L129)） | 如果要让 System 2 使用 VLM，需要另建可见语义 observation/assessment seam，不能把现有 snapshot assessment 改名冒充 |
| 新 typed/abstaining VLM provider | 候选代码完成 | A1 3B/A2 7B 的离线 provider 候选有严格图片、模型、revision、processor 和资源边界；代码可真实调用 backend，但身份明确 `production_eligible=false` 且任何结果都保持 `claims_physical_pass=false`（[`visible_qwen_provider.py`](../../self_improving/studies/vlm_fallback_prompt_optimization/visible_qwen_provider.py#L939-L991)，[`visible_qwen_provider.py`](../../self_improving/studies/vlm_fallback_prompt_optimization/visible_qwen_provider.py#L1046-L1125)）；专项预检为 143 tests、statement/branch 100%（[`vlm-visible-provider-preflight-20260902.md`](vlm-visible-provider-preflight-20260902.md#L19-L64)） | A0 独立 baseline provider 尚缺；候选还没有生产资格 |
| VLM 实际调用 | **正式研究为 0 次** | 预检明确 `model_loaded=false`、`inference_count=0`、`gpu_used=false`、`execution_authorized=false`（[`vlm-visible-provider-preflight-20260902.json`](vlm-visible-provider-preflight-20260902.json)） | 完成标注与 authority amendment 后才能启动真实推理 |
| Prompt fallback 执行 | **未开始** | 运行器已经能组织 visible 与 routing 两类实验并写哈希链日志，但证据报告明确昂贵 VLM、仿真和晋升均未开始（[`vlm-fallback-runner-20260831.md`](vlm-fallback-runner-20260831.md#L3-L18)） | 需要真实 baseline failure、typed routing、fresh compile/replay 与对照实验 |
| 量化实验 | **0 个正式结果** | `run_log.jsonl` 只有 8 条 setup/commitment 记录；前六条均记录 `model_loaded=false`、`VLM_inference=not_started`、`simulator=not_started`（[`run_log.jsonl`](../../self_improving/studies/vlm_fallback_prompt_optimization/run_log.jsonl)） | 需要逐 case 模型回执、盲标结果、指标和置信区间 |
| 晋升门 | 合同已写，当前不可能通过 | A1/A2/B2 的门槛已经冻结；amendment 01 明确任何 dry-run 都返回 `authoritative=false`、`eligible=false`（[`AMENDMENT.md`](../../self_improving/studies/vlm_fallback_prompt_optimization/amendments/01/AMENDMENT.md#L132-L153)） | 需要绑定真实 provider qualification、sealed annotations、实验 journal 与 authoritative evaluation closure |
| Validate 对 VLM 的消费 | 未接入 | Validate v2 输入列出 compile/replay receipts、runtime evidence、media receipt 等 12 项证据，但没有 VLM artifact；发布决定仍由物理证据控制（[`text2env_validate_v2.py`](../../self_improving/harness/schemas/text2env_validate_v2.py#L26-L46)，[`text2env_validate_v2.py`](../../self_improving/harness/schemas/text2env_validate_v2.py#L119-L181)） | 后续若加入 VLM，应是 advisory evidence，不能覆盖物理 fail |

## 为什么“旧 Demo 跑过 Qwen”仍不能算正式接入

旧路径确实完成了最小能力证明：仿真先生成图片，再调用本地 Qwen。可是它与现在需要验收的链路有四个
关键差别：

1. 它从工作目录读取图片，而不是只消费已验真的 CAS ArtifactRef。
2. 它的 critic 结果写在 Demo job 目录，没有新版 replay receipt、Registry run 或 qualification 绑定。
3. 它只看 `preview_head/world_left/world_right/observer_start` 四张图，没有检查完整 120 帧序列。
4. 它的补问只修模型输出格式/漏项，没有执行“诊断 → 改场景或 prompt → fresh replay → 再验证”的闭环。

以上调用顺序可直接在 [`demo/app.py`](../../demo/app.py#L251-L333) 核对；旧 critic 的补问行为见
[`rendered_critic.py`](../../scene_gen/rendered_critic.py#L227-L254)。历史 Demo 产物目前只剩仓库归档清单中的
摘要记录，例如 `rendered_critic.json` SHA-256 为
`b9435e86546110f81518949a378fb634614b4656a68ed322db3ec2933262ab46`，实际 bytes 不在当前工作树
（[`MANIFEST.sha256`](../../self_improving/workspace_archives/20260716/MANIFEST.sha256#L3124-L3146)）。

## 当前最大的实际阻塞

VLM study 的 annotation manifest 仍然是 `state=pending_blinded_annotation`、
`execution_authorized=false`、`provider_execution_allowed_now=false`，test payload 和 dev gold digest 都是
null（[`pending_annotation_manifest.v3.json`](../../self_improving/studies/vlm_fallback_prompt_optimization/amendments/01/pending_annotation_manifest.v3.json)）。

此外，冻结实验清单仍引用已经按用户要求删除的 PEARL portal 图片。2026-09-06 在当前工作树执行完整
study suite，结果为 `665 passed, 2 failed`；两个失败都是 frozen inventory 找不到
`apps/pearl_evidence_portal/.../final_observer_camera.png`。已有预检报告也记录了相同边界
（[`vlm-visible-provider-preflight-20260902.md`](vlm-visible-provider-preflight-20260902.md#L66-L86)）。

因此正确处理方式不是恢复 PEARL portal，而是追加一份新的、可审计的 amendment，把实验输入重新绑定到
仍然存在的 replay CAS/证据，并保留旧清单作为历史记录。

另一个结构性问题是：amendment 01 的 36 条 routing 数据中有 30 条 clean controls、6 条不可恢复失败、
**0 条可恢复 baseline failure**。因此当前 completion delta 没有分母，B2 按合同不可能晋升；文档也明确
写了必须由后续 amendment 加入至少 8 个独立可恢复失败组
（[`AMENDMENT.md`](../../self_improving/studies/vlm_fallback_prompt_optimization/amendments/01/AMENDMENT.md#L104-L121)）。

## 本次当前工作树复核

本次只读审计位于 HEAD `84d8877`，没有运行真实 VLM 或新仿真，也没有恢复 PEARL 文件。针对性测试结果：

| 检查 | 结果 | 说明 |
| --- | ---: | --- |
| `tests/scene_gen/test_rendered_critic.py` | 5 passed | 旧 critic 的 schema/fail-close/漏项补问测试；使用注入推理替身 |
| replay application + handler | 170 passed | 正式 replay 接线和攻击边界 |
| typed visible provider | 143 passed | provider 合同测试；没有加载真实模型 |
| VLM experiment runner | 230 passed | 运行器、日志和门禁测试 |
| 完整 VLM study | 665 passed, 2 failed | 两项 frozen inventory 因 PEARL 图片已删除而失败 |

这些测试证明组件合同仍工作，不能替代真实 VLM 推理、fresh replay 或量化效果实验。

## 建议的下一步顺序

1. **重冻实验输入，不恢复 PEARL。** 新 amendment 只选择当前仍可解析、哈希可核对的 replay 图片和
   runtime evidence；同时补至少 8 个独立、真实可恢复 failure group。
2. **完成双盲标注。** 两位标注者和第三位仲裁者完成可见语义标签，分别封存 train/dev/test；在此之前
   不启动候选模型测试。
3. **补齐并资格化 provider。** 为 A0 旧五项 critic 做独立 frozen baseline provider；把 A1 3B provider
   的源码、模型、processor、运行环境和资源边界写入正式 qualification，而不是信任 self-report。
4. **新增 replay evidence consumer。** 它只接收同一 replay 的 resolved scene 和已经过媒体 verifier 的
   CAS 图片，调用 VLM 后产生 path-free、hash-bound、`claims_physical_pass=false` 的 assessment receipt。
5. **先跑 A，再跑 B。** A 比较 A0/A1/A2 的可见错误识别；B 用真实失败做 typed routing、一次受约束
   prompt rewrite、fresh compile、fresh replay 和 deterministic validate。
6. **量化过门后才进入 production。** 生成逐次日志、宏 F1、coverage、unsafe pass、typed correction
   precision、route accuracy、completion delta、置信区间和 repeatability；任一门槛失败都保留结果并拒绝
   晋升。
7. **最后才接 Workbench。** 展示 VLM 建议、模型/图片/回放摘要和 fallback 尝试，但清楚区分
   “VLM 可见语义建议”与“物理 validation 结论”。

## 验收口径

下一次可以诚实宣布“replay 已接入 VLM”的最低证据应同时包括：

- 一次真实、已资格化 replay 的可信图片 ArtifactRef；
- 一次真实 Qwen 调用的模型 revision、processor、prompt、图片、resolved scene、耗时和资源回执；
- VLM assessment 在 CAS 中可重读且与 replay run identity 一致；
- VLM 明确不声明 physical pass；
- 至少一个失败案例进入受约束 fallback，并完成 fresh replay/validate；
- baseline 与候选的逐 case 日志及量化总结；
- qualification 与 promotion gate 的正式 pass 或明确 reject。

当前还没有一条运行同时满足以上条件。

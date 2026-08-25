# 从“看起来像”到“任务中像”：物理属性检索与重建研究建议

日期：2026-08-24
范围：PEARL / `self_improving` 的 text、image、video 资产检索与数字近亲路线。
状态：研究建议（不是已实现能力，也不是对任何论文 SOTA 的复现声明）。

## 一句话结论

不要把“三维高斯重建 + LLM 猜参数”直接叫作物理重建。更稳妥、也更容易形成可审计贡献的定义是：

> 给定文本、单张/多张图像或视频，检索一个可替代的资产；为质量、惯量、摩擦、关节和可供性输出带不确定性的先验；用最小的安全交互或回放轨迹做系统辨识；只有当任务级行为在声明的仿真器中通过时，才把该属性包晋升为可复用资产。

这是一条“检索 → 先验 → 探测 → 验证 → 记忆”的闭环，而不是要求网络从 RGB 唯一反演不可观测的质量或摩擦。对于许多操作任务，精确的真实常数并非必要；任务相关的等价类（例如能否抓起、推到目标、门能否打开、接触是否稳定）更有用。需要发布或跨仿真器传输时，再提高属性覆盖与校准等级。

## 先把现有仓库边界读准

仓库已经有很合适的接缝，新增研究层不应污染 `scene_gen/`：

* `self_improving/asset_pipeline/active/1_asset_reuse/` 已定义场景驱动检索：`scene_acquire.py` 先做需求提取和 coverage，再调用批量检索；`a2_selection.py` 保存候选、排序和淘汰码；现有设计明确“复用耗尽才进入 generation blocker”。对应仓库文件是 `self_improving/asset_pipeline/active/1_asset_reuse/docs/2026-08-03-asset-retrieval-integration-design.md`；这里保留文字路径，避免独立报告脱离仓库后出现失效相对链接。
* `lib/ledger.py` 的 `asset_ledger.v2` 已有 `physical.mass_kg`、`physical.friction` 的 `known|estimated|unknown` 三态、`physical.inertial` 的 profile 约束，以及 append-only、带 representation digest 的 `verification[]`。这正好可以容纳“先验/后验/未知”，不应另造第二本资产账。
* Open X Sim 的 `AssetBundle` 已把 `physical` 和 `articulation` 放在 simulator-neutral IR 中；`env_gen.py` 当前对 mass/inertia/friction 如实写 `unknown`，这比填一个无来源的常数更诚实。
* Harness PR1 的 `ArtifactRef` 以 `media_type + schema_version + sha256` 识别制品，`EnvironmentPackage.package_id` 必须等于 `resolved_scene_sha256`；compile/replay/validate 是最小的证据链。物性推断结果应作为 hash-bound sidecar 引用，而不是复制或改写 `SceneSpec`。
* PEARL 的命令路由已经把 `/gen-env`、`/collect`、`/evaluate`、`/diagnose`、`/transfer` 分开。物性 sidecar 的产生者可在 `/gen-env`，交互辨识和轨迹证据属于 `/collect`/`/evaluate`，候选晋升属于 validate/promotion，而不是渲染器。

**仓库内一个必须如实保留的反例。** `external/digital-cousins` 的 cabinet wrapper 在 `standardize_density_and_friction` 中将密度默认设为 `200.0`、关节摩擦设为 `0.025`，并非从图像恢复了真实材料参数；其 matching pipeline 用视觉特征与 GPT 选择数字近亲。这个工程仍然很有价值，但证据应标成“语义/几何/可供性近亲”，不能标成物性真值。

## 需要重建什么，什么可以不重建

### 属性按可观测性分层

| 层 | 例子 | 从 RGB/视频能否唯一恢复 | 对任务的通常价值 | 建议证据 |
| --- | --- | --- | --- | --- |
| 几何/尺度 | 尺寸、姿态、碰撞近似、可见/不可见部件 | 多视图或标尺可估；单图有尺度歧义 | 很高 | mesh/URDF、尺度标定、碰撞检查 |
| 运动学 | part、joint type/axis/origin、limits、开闭状态 | 视频/双状态图有强线索；遮挡时不唯一 | 很高（门、抽屉、容器） | 关节 sweep、状态转移、任务 verifier |
| 可供性 | 抓取点、把手、支撑面、容纳关系 | 图像和交互都可提供线索 | 很高 | contact/抓取/放置成功率 |
| 质量/质心/惯量 | `m`, CoM, inertia tensor | 视觉通常不能唯一恢复；可由尺度+材质先验给分布 | 接触丰富任务高；轻放置任务可能低 | 推/抬/倾倒探测，动力学拟合 |
| 摩擦/恢复系数 | 静/动摩擦、弹性 | 仅从外观几乎不可辨识；需要接触轨迹或材料先验 | 推、滑、抓取、堆叠高 | 多速度/法向力 probe，跨引擎校准 |
| 软体/材料本构 | 刚度、阻尼、塑性、液体参数 | 单视图极不充分 | 软体/变形任务极高 | 受控压入/拉伸/流体轨迹 |

因此“真的需要重建物性吗？”的答案是：**需要重建任务会用到的物性；不需要为每个资产伪造完整材料数据库。**

推荐三个发布等级：

1. `visual_reuse`：外观/语义/尺度/可加载碰撞通过；只用于渲染或非接触任务。
2. `task_physical`：声明任务相关属性的区间和不确定性，并通过目标任务的 replay/probe；可用于该任务族。
3. `cross_backend_physical`：在 SAPIEN 与第二引擎（MuJoCo/Isaac/ManiSkill 等）上经过同一 probe 的行为等价门；才可宣称跨仿真复用。

## 建议的最小数据契约（sidecar，不改核心 SceneSpec）

建议新增一个平台层制品（暂不要求现在实现 JSON Schema）：`asset_physical_posterior.v1`。它由 `AssetBundle.asset_id/model_id` 和 representation digest 锚定，并作为 `ArtifactRef` 被 `EnvironmentPackage`/selection evidence 引用。

```json
{
  "schema_version": "asset_physical_posterior.v1",
  "asset_id": "317_tissuebox",
  "model_id": 0,
  "representation_digest": "<sha256 of non-snapshot representations>",
  "query": {
    "route": "text|image|video",
    "input_artifacts": [{"sha256": "...", "media_type": "image/png"}],
    "task_family": "pick|push|place|open|contain",
    "seed": 42
  },
  "properties": {
    "dimensions_m": {"value": [0.12, 0.08, 0.04], "status": "measured", "interval": null},
    "mass_kg": {"value": null, "status": "estimated", "interval": [0.05, 0.25], "estimator": "material_prior@v1"},
    "com_m": {"value": null, "status": "unknown", "interval": null},
    "inertia_kg_m2": {"value": null, "status": "engine_derived", "interval": null},
    "friction": {"value": null, "status": "estimated", "interval": [0.2, 0.8], "estimator": "vlm_plus_probe@v1"},
    "restitution": {"value": null, "status": "unknown", "interval": null},
    "articulation": [{"joint_type": "hinge", "axis": [0, 0, 1], "limit_rad": [-1.2, 0], "status": "measured"}],
    "affordances": [{"kind": "handle", "frame": "asset", "region": "...", "confidence": 0.84}]
  },
  "evidence": [{
    "kind": "catalog|vlm_prior|material_table|interaction_probe|sim_replay|real_replay",
    "artifact_ref": "...", "method": "...", "uncertainty": {"calibration": "..."}
  }],
  "probe_plan": [{"name": "low_force_push", "action": "...", "stop_condition": "..."}],
  "task_equivalence": {"metric": "endpoint_pose|success|contact_trace", "threshold": "..."},
  "status": "prior_only|probe_pending|task_verified|cross_backend_verified",
  "parent_sha256": "<resolved scene or source input digest>"
}
```

Important semantics:

* `status=estimated` always carries `estimator`; `unknown` is a valid result, not zero.
* Keep intervals/posteriors, not only point estimates. A VLM answer is a prior and must never silently become “known”.
* `engine_derived` explicitly says inertia came from collision geometry/engine convention, not measurement.
* Every evidence row names the exact input, model/checkpoint/version, prompt/config, simulator, seed and report hash. Raw private media can remain outside Git; the receipt stores a digest and URI.
* The sidecar is append-only by evidence run; current status is recomputed from the newest record whose representation digest still matches, mirroring `ledger.py` verification semantics.

## Where it plugs into PEARL

```text
text/image/video request
        |
        v
asset retrieval (a1/a2 + visual/text embedding)
        |  selection_evidence + candidate rejection reasons
        v
physical prior sidecar (VLM/material/PartNet/PhysX model)
        |  interval + confidence; no publish yet
        v
compile -> EnvironmentPackage (sidecar ArtifactRef, hash bound)
        |
        v
collect/evaluate: safe probe or task rollout -> state/action/contact/wrench trace
        |
        v
system identification + simulator replay (SAPIEN, then second backend)
        |
        v
diagnose / promotion: task_physical or cross_backend_physical
```

Concrete ownership proposal:

* `a1_providers.py`/`a2_selection.py`: rank by semantic + geometry + declared physical compatibility, but never use an unverified estimate as a hard fact. Record `physical_coverage` and rejection reasons (`missing_collision`, `unknown_scale`, `probe_required`, `license_blocked`).
* `ledger.py`: add a reference to posterior artifact and a compact `physical_status`/`task_family` summary only if a future schema change is approved. Do not duplicate the full posterior in every catalog fragment.
* `openxsim/ir.py`: continue to carry a simulator-neutral `physical` map. Backends consume only fields they declare; unknown/estimated fields trigger explicit defaults and diagnostics.
* `/gen-env`: produce the prior and probe plan; static validation can be `incomplete` when no runtime evidence exists.
* `/collect`: log action, object pose, contact points/forces when available, timestamps and reset state. A rendered video alone is not a probe.
* `/evaluate`: run matched probe/task seeds and compute success, endpoint error, contact stability, slip/drift and cross-backend discrepancy.
* `/diagnose`: turn failed probes into typed blockers and new data requirements, e.g. `physical_posterior_uncalibrated`, `friction_identification_failed`, `articulation_axis_conflict`.
* `apps/pearl_evidence_portal`: present status, uncertainty and evidence lineage; it must not manufacture a pass decision.

## Gates that would make the claim credible

Use gates in increasing strength; a visual score cannot substitute for a physical gate.

| Gate | Required evidence | Suggested criterion (initial, tune per task) |
| --- | --- | --- |
| Identity/retrieval | candidate list, source/license, input and representation hashes | selected asset resolves and all non-selected candidates have a reason |
| Geometry/scale | mesh/URDF, dimensions, scale calibration, collision representation | dimension relative error ≤10% on measured subset; no missing collision; import succeeds |
| Kinematics | part labels, joint axis/type/limits, state videos | joint type accuracy 100% on smoke set; axis angle ≤10°; sweep no self-intersection/unbounded drift |
| Static support | target-local support/containment calculations | support/contact fraction and margins pass existing `/gen-env` validator |
| Runtime stability | continuous replay, contact events, settle/drift | no penetration; settle drift under existing project threshold (2 mm for asset smoke); declared contact persists |
| Task behavior | fixed checkpoint/policy, matched seeds, success verifier | compare prior-only vs probe-calibrated vs oracle-parameter conditions; report confidence intervals, not one demo |
| Property calibration | held-out objects and perturbations, posterior coverage | 90% prediction intervals contain measured/probe values on held-out set; calibration error reported |
| Cross-backend | same package/probe in SAPIEN + MuJoCo/Isaac/ManiSkill | endpoint/contact metrics within predeclared tolerance; no backend-specific hidden overrides |
| Promotion | immutable receipts, rollback pointer, regression suite | candidate changes active catalog only if all required gates pass; failures remain evidence |

For a first benchmark, choose three task families where one property matters: (a) planar push (friction/CoM), (b) pick-and-place (mass/scale/grasp affordance), (c) open/close (joint axis/limit and joint friction). Include easy rigid cases and ambiguous cases; split by object instance/category, not only by random image.

## What primary work actually establishes

The table distinguishes source claims from what this repository has run. URLs are first-party project/repository pages or arXiv records; release status and license notes are not treated as proof of correctness.

| Work | What it provides | What it does *not* prove | Reproduction value |
| --- | --- | --- | --- |
| **Phys2Real** (Wang et al., ICRA 2026; [paper](https://arxiv.org/abs/2510.11689), [code](https://github.com/phys2real/phys2real)) | Explicitly combines 3DGS geometry, VLM physical-parameter priors, and online interaction adaptation with uncertainty fusion; evaluates T-block/hammer pushing. | It is not a general object-mesh/URDF reconstruction benchmark; parameter/task scope is narrow and hardware-specific. | **P0, highest value**: directly tests whether “VLM prior + interaction” beats prior-only and domain randomization. Reproduce simulator first, then one physical probe if hardware is available. |
| **PhysX-3D / PhysXNet** ([paper](https://arxiv.org/abs/2507.12465), [code](https://github.com/ziangcao0312/PhysX-3D), [dataset](https://huggingface.co/datasets/Caoza/PhysX-3D)) | Physics-grounded asset generation with annotations for absolute scale, material, affordance, kinematics and function; code converts annotations to URDF. | Dataset annotations are not the same as measured real mass/friction; generated physical plausibility is not task success. | **P0/P1**: inspect annotation schema and run URDF generation + static/import gates on a small split. |
| **PhysX-Anything / PhysX-Mobility** ([paper](https://arxiv.org/abs/2511.13648), [code](https://github.com/ziangcao0312/PhysX-Anything), [dataset](https://huggingface.co/datasets/Caoza/PhysX-Mobility)) | Single in-the-wild image to geometry, articulation and physical attributes; exposes inference, split, URDF/XML conversion, and kinematic/physical evaluation scripts. | “Physical attributes” are inferred/annotated and may be uncertain; README itself warns deformable outputs are unstable in MuJoCo. No evidence here yet of RoboTwin/SAPIEN import or real-world property accuracy. | **P0/P1**: best direct image2env candidate, but admission must require import + task replay; do not accept VLM evaluation alone. |
| **PartNet** ([paper](https://arxiv.org/abs/1812.02713), [repo](https://github.com/daerduoCarey/partnet_dataset)) | 26,671 models/573,585 part instances with hierarchical part annotations. | It is not a dynamics/physical-property ground-truth dataset. | **P0 support pretraining** for part retrieval and semantic decomposition. |
| **PartNet-Mobility / SAPIEN** ([SAPIEN paper](https://arxiv.org/abs/2003.08515), [dataset portal](https://sapien.ucsd.edu/browse), [MJCF converter](https://github.com/bilab-manipulation/partnetmob2mjcf)) | Articulated meshes, URDF/kinematic structure and an executable SAPIEN environment; converter offers a path to MJCF. | Kinematics and engine defaults are not measured material density/friction truth. | **P0** for articulation retrieval and cross-format smoke. |
| **Shape2Motion** ([paper](https://arxiv.org/abs/1903.03911), [repo](https://github.com/wangxiaogang866/Shape2Motion)) | Motion-part and motion-attribute prediction from 3D shapes; releases Motion Dataset V0 and evaluation code. | Old TensorFlow/Matlab stack and mobility labels do not identify mass/friction. | **P1** as a lightweight articulation baseline; pin its GPL-3.0 code/data obligations. |
| **GAPartNet** ([paper](https://arxiv.org/abs/2211.05272), [repo](https://github.com/PKU-EPIC/GAPartNet)) | 8,489 actionable-part instances on 1,166 objects, 9 GAPart classes, poses, segmentation and manipulation heuristics; code/checkpoints released. | Affordance/part pose is not a full dynamics model. Dataset is CC BY-NC 4.0. | **P0/P1** for handle/lid/grasp affordance sidecar and task-conditioned retrieval. |
| **RBO articulated objects** ([paper](https://arxiv.org/abs/1806.06465), [dataset](https://tu-rbo.github.io/articulated-objects/)) | 14 articulated objects, 358 RGB-D interaction sequences, ground-truth part poses/kinematic state; 78 sequences include measured interaction wrenches. | Small scope; not a broad image-to-3D generator. | **P0 probe/evidence set** because wrench + motion makes it unusually useful for validating interaction-based system identification. |
| **Objaverse-XL** ([paper](https://arxiv.org/abs/2307.05663), [repo](https://github.com/allenai/objaverse-xl)) | Over 10M diverse 3D objects and large-scale rendered views. | Individual licenses vary; visual scale and mesh diversity are not physical labels. | **P1 retrieval pretraining/negative pool**, with license and geometry admission gates. |
| **Digital Cousins** ([paper](https://arxiv.org/abs/2410.07408), [repo](https://github.com/cremebrule/digital-cousins)) | Single RGB → interactive scene/cousin matching and policy robustness evaluation; claims 90% vs 25% zero-shot sim-to-real in the paper. | “Cousin” means similar geometric/semantic affordance, not identical dynamics; local wrapper standardizes density/friction. | **P0 integration reference** for reuse-first matching and task-level equivalence, not property ground truth. |
| **ManiSkill3** ([paper](https://arxiv.org/abs/2410.00425), [repo](https://github.com/haosulab/ManiSkill)) | GPU-parallel SAPIEN simulation, heterogeneous scenes, contact-rich manipulation, real2sim and sim2real hooks. | It is an execution/benchmark framework, not a method for inferring an unknown object’s physical parameters. | **P0 backend** for large probe sweeps and policy regression. |
| **BEHAVIOR-1K / OmniGibson** ([paper](https://arxiv.org/abs/2403.09227), [repo](https://github.com/StanfordVL/BEHAVIOR-1K)) | 1,000 household activities, 50 scenes, >9,000 objects with rich semantic/physical annotations; rigid, deformable and liquid simulation. | Rich annotations and engine parameters are not automatically real-world measurements; installation/data licensing is substantial. | **P1 task-family and long-horizon transfer benchmark**. |
| **PHYRE** ([paper](https://arxiv.org/abs/1908.05656), [repo](https://github.com/facebookresearch/phyre)) | Fast 2D physics puzzles and generalization splits for testing physical reasoning. | 2D puzzles do not evaluate 3D asset reconstruction or robot contact. | **P2 sanity check** for a learned physical-reasoning head, not an asset admission gate. |
| **PhysBench** ([paper](https://arxiv.org/abs/2501.16411), [dataset](https://huggingface.co/datasets/USC-GVL/PhysBench), [project](https://physbench.github.io/)) | 10,002 interleaved video-image-text examples over object properties, relations, scenes and dynamics; reports broad VLM weakness and PhysAgent gains. | QA/VLM accuracy is not calibrated simulation parameters or physical replay. | **P1 prior/evaluation probe**: use as an auxiliary physical understanding task and hard-negative test, never as proof of executable physics. |
| **PhysX-Omni / PhysX-Bench** ([repo](https://github.com/physx-omni/PhysX-Omni)) | Metric-first pipeline with render, dimension, affordance, kinematic and material plausibility metrics; includes URDF/MuJoCo rendering and denominator validation. | Several scores use VLM judges; plausibility metrics are not measured dynamics or task success. | **P0/P1 evaluation harness** to borrow manifest/denominator discipline while adding real replay gates. |

### Why these choices are complementary

* PartNet-Mobility, Shape2Motion and GAPartNet answer **what moves and where to act**, not how heavy or slippery the object is.
* PhysX-3D/Anything answer **how to make a structured sim-ready candidate from image/shape priors**, but need an independent runtime/task gate.
* Phys2Real answers **how to turn an uncertain visual prior into a useful task-conditioned posterior** through interaction; this is the closest match to the proposed PEARL loop.
* RBO provides rare real interaction wrench evidence; ManiSkill/OmniGibson provide scalable execution; Digital Cousins supplies the right “similar enough for a task” framing.

## Recommended reproduction ladder (bounded and reviewable)

### R0: schema and retrieval-only smoke (CPU)

1. Pick 20 RoboTwin/asset-ledger objects plus 20 held-out image queries.
2. Build a candidate index from local ledger, PartNet-Mobility metadata, GAPartNet affordance labels and a small Objaverse subset.
3. Produce `selection_evidence` and `asset_physical_posterior.v1` with all unknowns explicit.
4. Verify representation digest invalidation, license handling, and no candidate without a rejection reason.

Success means reproducible bookkeeping, not physical accuracy.

### R1: PhysX-Anything/PhysX-3D import smoke (GPU optional)

Run released preprocessing/inference on a small, fixed image subset; generate URDF/XML; import into SAPIEN and MuJoCo. Measure dimensions, joint type/axis, collision load, self-intersection and settle. Keep VLM/visual metrics as secondary evidence. Any deformable output that is unstable in MuJoCo stays `probe_pending`.

### R2: Phys2Real-style controlled system identification (highest-value experiment)

Use a simple planar push scene with known simulator oracle parameters. Render images, ask a VLM for a prior distribution, then execute a fixed sequence of low-force pushes and fit friction/CoM with an ensemble or Bayesian estimator. Compare:

* visual/VLM prior only;
* interaction posterior;
* oracle parameters (upper bound);
* domain-randomized policy without parameter observation.

Use held-out CoM/friction combinations and report posterior interval coverage, task success, calibration and number of probes. This isolates whether the backbone provides useful physical information without claiming it “understands all physics”.

### R3: articulated route (PartNet-Mobility + GAPartNet + RBO)

Train/evaluate part/joint/affordance retrieval on PartNet-Mobility/GAPartNet; use RBO RGB-D+wrench sequences for interaction evidence. Convert one object to SAPIEN and MuJoCo, run open/close or handle-pull probes, and compare estimated axis/limit and endpoint trajectory. Do not mix object categories across train/test by random frames.

### R4: PEARL promotion bundle

For each route (`text2env`, `image2env`, `video2env`), package: input digest, candidate list, selected asset, posterior, probe plan, continuous replay, task verifier, diagnostics, and promotion decision. Run at least one task in SAPIEN and a second backend. Publish a negative result if posterior calibration or task success fails.

## Backbone/data recommendation

Do not attempt to extract a mysterious “physics backbone” from a VLA by inspecting action heads. A VLA can output forces/angles because it learned policy correlations, robot dynamics, task affordances, and feedback conventions; this does not identify object mass/friction as separately recoverable variables. The practical architecture is:

1. frozen visual/geometric encoder (DINO/CLIP/3D feature or the selected PhysX/PartNet encoder);
2. property heads that output distributions/intervals, not scalars;
3. an interaction-history encoder for probe trajectories;
4. a differentiable or black-box simulator/system-ID layer;
5. task-conditioned policy/evaluator and uncertainty calibration.

Train with multi-task losses (geometry/parts/kinematics/affordance/property posterior) plus trajectory prediction and simulator-consistency losses. Hold out object instances, categories and material combinations. If only images are available, train a prior and call it a prior; do not label it measured physics.

## Explicit claim boundaries

* A successful zero-shot VLA grasp demonstrates that the policy can choose a robust action under its training distribution and feedback loop. It does **not** by itself prove an identifiable internal mass/friction estimate.
* A simulator trained from RGB observations can exploit fixed asset conventions, category priors, domain randomization and task feedback; “only images were provided” does not mean the model inferred exact hidden parameters.
* A good render, VLM judge, endpoint screenshot or start/end pose is not physical evidence. Require continuous contact/state traces and a declared verifier.
* A digital cousin can be the right substitute when task-relevant behavior transfers, even if its exact density or friction differs. The acceptance object is a behaviorally equivalent package, not a metaphysical reconstruction of the real object.
* Results from PhysX-Anything/PhysX-Omni, PhysBench or Digital Cousins should be cited as external primary work and rerun status should remain `not_run` until this workspace has exact commands, pinned commits, data access and reports.

## Primary-source links and access notes

All links above were checked against the first-party repositories/project pages or arXiv metadata on 2026-08-24. Relevant repository snapshots observed during this audit include: PhysX-3D `4f54e750a309fe9cd9f20816916ecc0e8a9ae594`; PhysX-Anything `e221826e6176d940905126d1894f9c1c933b70a8`; Phys2Real `2376bb2b806b5afb20dd48e9b483e61990430e67`; GAPartNet `c8d4ad2db579587ce8712766b2441d5ced95a1a1`; Shape2Motion `96eebec1118739d806d03a24d4d9cbc0259ed28b`; ManiSkill `62ff3a5896b5d4b5cf0ac4c8d79afe600c9404a3`; Objaverse-XL `6996a3861b75abb0e0bb036a0766cf3263c708cd`; BEHAVIOR-1K `eb3c01263b76f4404e8187c1bcd758d48d47a020`; PhysX-Omni `46fa1cd0b6883d4d14431d51c3326ef80a85ef64`. Dataset access, model checkpoints, GPU requirements and licenses must be rechecked before any redistribution or benchmark claim.

## Open blockers for this repository

1. No committed physical posterior schema or probe skill exists yet; this note is a design/research artifact only.
2. Existing asset ledgers often record `unknown` mass/friction and use engine defaults; this is correct but means current catalog entries cannot be advertised as physically reconstructed.
3. Real SAPIEN/Isaac/MuJoCo probe execution and wrench/tactile capture require machine-specific dependencies and must remain explicit skips/blockers when unavailable.
4. A cross-backend tolerance matrix (L0–L4) is not yet frozen; do not silently equate engine parameters.
5. Public release of third-party meshes/checkpoints is governed by source licenses; keep only metadata, hashes and small evidence samples in Git.

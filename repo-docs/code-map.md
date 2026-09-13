# 代码地图

主行为模型先在 [一条真实路径](walkthroughs/one-real-run.md) 建好。这里负责把那套行为翻译成「改动从哪里下手」。稳定核心仍是 `scene_gen/`、`script/`、`demo/`、`tests/`；平台组合入口位于 `self_improving/`，呈现层位于 `apps/`，独立项目位于 `external/`。

| 路径 | 职责 | 关键代码 | 与主流程的关系 |
| --- | --- | --- | --- |
| `scene_gen/` | 编译器核心库：契约、解析、grounding、求解、builder、validator、绘制代理、acceptance。 | `schema.py`、`parser.py`、`grounding.py`、`solver.py`、`builder.py`、`validator.py`、`scene_gen/envs/generated_scene.py` | 主流程每一阶段都住在这里；CLI 与 demo 只是薄入口 |
| `script/` | CLI 入口：编译、回放、批量验收、矩阵、可选渲染评判、stage-5 报告。 | `generate_scene.py`、`run_scene_runtime.py`、`run_100_seed_acceptance.py`、`run_prompt_matrix.py` | 编排 `scene_gen`；流水线逻辑加进 `scene_gen`，不要加在这里 |
| `demo/` | Flask 控制面：保留旧 GPU job 流水线，并可注入 Harness compile submit、只读事件与终态依赖审计。 | `app.py`、`harness_compile.py`、`harness_feed.py`、`static/` | job 入口仍调用旧脚本；审计只投影同一 compile authority，不执行 replay/validate 或推演操作 |
| `tests/` | pytest 套件 + committed fixture；为每个误报模式留攻击测试。 | `tests/scene_gen/test_<module>.py`、`tests/fixtures/{asset_catalog,golden_prompts,prompt_matrix}.json` | 锁住契约与失败分支；套件无需 RoboTwin checkout 即可跑 |
| `self_improving/` | Harness 对外契约、平台编排、闭环诊断、资产复用、仿真适配、来源清单与只读历史。 | `harness/schemas/`、`harness/schema_catalog.py`、`harness/registry.py`、`harness/package_store.py`、`harness/handlers/text2env_compile.py`、`source_inventory.json`、各命名模块 | Harness 只引用权威载荷，平台消费稳定核心；都不能降低 `scene_gen` 门控 |
| `apps/pearl_evidence_portal/` | PEARL Self-Improving Agents 的独立证据门户、构建脚本、测试与已裁剪的浏览器报告子集。 | `app/page.tsx`、`scripts/build-hosted-report-subsets.mjs`、`tests/rendered-html.test.mjs` | 只呈现已有证据；不产出或修改核心验收结论 |
| `external/` | 独立项目的 Git submodule。 | `OpenReal2Sim`、`digital-cousins` | 各自保留提交历史和发布周期；主仓只钉 commit |

## `scene_gen/`

| 重要代码 | 功能 | 关键符号 | 调用方 / 使用方 |
| --- | --- | --- | --- |
| `schema.py` | 类型化 pydantic 契约；禁用键；跨字段不变量；digest。 | `SceneSpec`、`ResolvedSceneSpec`、`ResolvedObject`、`RelationType`、`SceneSpecError`、`FORBIDDEN_SCENE_KEYS`、`SceneSpec.digest`、`ResolvedSceneSpec.digest` | 被 `parser`、`grounding`、`solver`、`builder`、`validator`、`asset_generator` 全部依赖；改这里先动它 |
| `parser.py` | 受限中英文 prompt → `SceneSpec`；绝不产出代码/路径/id/pose。 | `parse_rule_based`、`extract_mentions`、`_relation_between`、`validate_prompt_boundary`、`OBJECT_TERMS`、`COLOR_TERMS`、`MATERIAL_TERMS`、`REGION_TERMS`、`FORBIDDEN_PROMPT_PATTERNS`、`StructuredSceneProvider`（provider 协议） | 被 `script/generate_scene.py` 与 `tests/scene_gen/test_parser.py` 使用；详解在 [受限解析](modules/bounded-parser.md) |
| `grounding.py` | 把 object 提及落到真实 catalog 模型上，确定性 tie-break + 拒因记录。 | `ground_object`、`ground_scene`、`GroundedSelection`、`_semantic_score`、`_tie_break` | 被 `solver.solve_scene` 调用 |
| `solver.py` | bounded rejection backtracking；按 support depth 排序摆位；失败抛机读 trace。 | `solve_scene`、`SceneSolveError`、`CandidatePose`、`_support_depth`、`_pair_relation_reasons`、`COMPILER_VERSION = "scene_gen.stage5_solver.v3"` | 被 `script/generate_scene.py` 调用；机制在 [受限求解器](modules/solver.md) |
| `support_geometry.py` | 目标局部 support / containment 几何数学。 | `footprint_2d`、`support_surface_dimensions`、`support_surface_shape`、`support_surface_z`、`support_footprint_margin`、`sample_supported_offset` | 被 `solver`、`validator`、`run_scene_runtime` 共用；机制在 [目标局部几何](modules/target-local-geometry.md) |
| `asset_generator.py` | catalog miss / collision 不稳时的确定性代理；procedural primitive + derived scale；携来源 lineage。 | `ensure_assets_for_scene`、`_geometry`、`_prism_obj`、`_write_proxy_asset`、`PRIMITIVE_PROXY_SOURCES = {("004_fluted-block", 0)}`、`SCALE_HEADROOM = 0.92`、`MIN_DERIVED_SCALE = 0.35`、`GENERATOR_VERSION`/`PROXY_GENERATOR_VERSION`/`SCALE_GENERATOR_VERSION` | 被 `script/generate_scene.py` 的 `--generate-missing-assets` 调用；机制在 [确定性代理](modules/derived-proxy.md) |
| `catalog.py` | 从 RoboTwin checkout 扫资产目录；尺寸/朝向/关节/可用性；override 合并。 | `AssetCatalog`、`CatalogEntry`、`CatalogModel`、`CatalogJoint`、`load_catalog`、`AssetCatalog.digest`、合并 `asset_overrides.yml` 的逻辑 | 被 `grounding`、`solver`、`validator`、`run_scene_runtime` 用；fixture 版在 `tests/fixtures/asset_catalog.json` |
| `asset_overrides.yml` | 实测 RoboTwin 资产几何 override。 | `003_plate` model 0 的 100 mm 稳定面 + 8 mm 余量、`110_basket` model 1 的 12 mm 内底偏移、`071_can`/`021_cup` 稳定朝向、`036_cabinet`/`037_box` articulation qpos | 全部下游通过 catalog 间接读 |
| `builder.py` | 哈希绑定的 resolved 包构建 + 自证。 | `build_scene_package`、`verify_package`、`generated_module_source`、`_sha256` | 被 `script/generate_scene.py` 调用；机制在 [哈希绑定包](modules/replay-package.md) |
| `scene_gen/envs/generated_scene.py` | 回放入口：把 `ResolvedSceneSpec` 构造成 SAPIEN actor；不执行用户代码。 | `load_resolved_scene`、`_coerce_resolved`、`_apply_color_override` | 被 `script/run_scene_runtime.py` 与 builder 写的 `generated_scene.py:load_scene` 调用 |
| `runtime_sampling.py` | 视频帧采样无依赖工具。 | `video_sample_steps` | 被 `run_scene_runtime.py` 用；行越长越好该函数越小 |
| `validator.py` | 静态 + 运行时 validator：把几何关系与 runtime_evidence 翻成 checks。 | `validate_resolved_scene`、`_relation_pass`、`_aabb3`、`_interior_aabb`、`_support_surface` | 被 `generate_scene.py`（静态）、`run_scene_runtime.py`（运行时）、`tests/scene_gen/test_builder_validator.py`（攻击）调用；门控在 [运行时门控](modules/runtime-gates.md) |
| `acceptance.py` | 批量 95% 通过率聚合。 | `summarize_acceptance`、`minimum_pass_rate=0.95` | 被 batch/matrix runner 用，不在主编译路径上 |
| `rendered_critic.py` | 可选 VLM 渲染评判，只查可见语义；非物理证据。 | `rendered_critic` 主入口 + 中间件 | 只在 `pip install -e '.[vlm]'` 后由 `script/run_rendered_critic.py` 调用；属相邻路径 |
| `colors.py` | 颜色名 → RGB 映射，运行时颜色 override 用它。 | `COLOR_RGB` | 被 `scene_gen/envs/generated_scene.py:_apply_color_override` |
| `scene_gen/prompts/parse_scene.md` | provider 路径的双语 LLM prompt 模板。 |—| rule-based CLI 不读它；仅 `StructuredSceneProvider` 用 |

## `script/`

| 重要代码 | 功能 | 关键符号 | 调用方 / 使用方 |
| --- | --- | --- | --- |
| `generate_scene.py` | 编译 CLI：`text -> ResolvedSceneSpec` 包 + 静态 `validation_report.json`。 | `main`、`--prompt`/`--seed`/`--asset-catalog`/`--out-root`/`--generate-missing-assets`/`--generated-objects-root` | 仓库 README「Compile」配方；CLI 表面变更须用 `python script/generate_scene.py --help` 核验 |
| `run_scene_runtime.py` | SAPIEN/RoboTwin 物理回放 + runtime evidence 采集 + runtime validator。 | `main`、`load_robotwin_args`、`summarize_contacts`、`runtime_support_margin`、`runtime_inside_contained`、`runtime_relation_results`、`head_camera_arrays`、`--precheck-steps`/`--settle-steps`/`--contact-window-steps`/`--video-frames`/`--fps`/`--task-config`/`--robotwin-root` | 需要 RoboTwin checkout；`--precheck-steps 0` 默认有意为之 |
| `run_100_seed_acceptance.py` | 100-seed 验收批量运行；可选 `--runtime` 触发 SAPIEN。 | `--seed-count`、`--video-seeds`、`--runtime`/`--robotwin-root` | 主路径之上的薄编排 |
| `run_prompt_matrix.py` | committed prompt 矩阵跨 seed 跑；可选运行时。 | `--matrix tests/fixtures/prompt_matrix.json`、`--runtime-all-seeds`、`--report` | 主路径之上的薄编排 |
| `run_rendered_critic.py` | 可选 VLM 渲染评判 CLI，对应 `scene_gen/rendered_critic.py`。 | `--resolved-scene`、`--image` x N、`--out` | 相邻路径，非物理证据 |
| `build_stage5_report.py` | 构建 stage-5 验收报告。 | — | 把运行时报告再聚合成 stage-5 视图，非主编译路径 |

Canonical C13 已从 active tree 撤下历史 `run_compile_acceptance.py` 与离线 Qwen
`run_replay_vlm_assessment.py` 入口；可从 `d08aaa1` Git 历史恢复，不再作为新运行配方。
它们的旧深模块与历史测试仍保留；这不是旧资格系统全部退役。稳定核心的 compile/runtime/critic
入口不变，新的平台实验只从 `x2env` 进入，由 managed Codex 提供视觉建议。
`run_qualified_replay.py` 薄 bootstrap 随后也已撤下（最后字节在 `33a7d6f`），历史深模块测试改为直接调用，
不再维护另一个旧 launcher；深模块与旧资格资源尚有消费者，继续单独清理。

## `demo/`

| 重要代码 | 功能 | 关键符号 | 调用方 / 使用方 |
| --- | --- | --- | --- |
| `demo/app.py` | Flask 控制面：保留旧 text2env job，并增加可选的 Harness compile submit、event replay、terminal audit 与固定 SceneSpec/static-validation 预览。 | `create_app`、`POST /api/harness/compile`、四个 `GET /api/harness/*` 路由、`HARNESS_WORKBENCH` | submit 只收 request/seed；audit/preview 只收 canonical v4 run id，preview 还拒绝 query/body/Transfer-Encoding/Range；同一 Workbench 优先拥有写入与读取 |
| `demo/harness_compile.py` | 把固定 qualified `CompileApplication` 隐藏在同步工作台 seam 后，并重读 terminal closure、Invocation、artifact metadata 与两个 succeeded compile 内容投影。 | `WorkbenchCompile.submit`、`audit`、`scene_preview`、`static_validation_preview`、`page`、`WorkbenchCompileAuthorityError` | 两个 preview 都复用完整终态 closure；SceneSpec、static report、ResolvedSceneSpec 分别以同 FD 有界读取 65,536 / 262,144 / 1,048,576 bytes，static preview 重算报告并把 canonical resolved digest 与 request/scene/seed/frame/unit/workspace/relations/source SceneSpec digest 绑定 package/report/SceneSpec，但不重跑 validator |
| `demo/harness_feed.py` | 把 `SQLiteEventJournal` 的 `EventPage` 投影成浏览器可消费、可恢复的全局 cursor 页。 | `HarnessEventFeed.page`、`HarnessEventFeedCorruptionError` | 只读 seam；保留 run/Skill/Event 信封与 artifact metadata，不增加任意路径读取 |
| `demo/static/` | 无构建步骤的 Event Timeline + compile-only 提交、依赖/artifact metadata 审计与两个固定字段预览 UI。 | `harness-compile-button`、`loadHarnessEvents`、`loadHarnessAuditIfEligible`、`loadHarnessScenePreview`、`loadHarnessStaticValidationPreview` | preview 只在 succeeded audit v2 精确绑定后由用户点击读取；独立 generation/abort、防缓存、响应上限与 exact-key 校验后用 `textContent` 渲染；static checks 可按任意契约有效顺序到达，且明示未运行物理 replay、不代表 publishable |
| `demo/__init__.py` | 包标记使 `demo` 可被导入。 | — | `python -m demo.app` |

## `tests/`

| 重要代码 | 功能 | 关键符号 | 调用方 / 使用方 |
| --- | --- | --- | --- |
| `tests/scene_gen/test_schema.py` | 禁用键、唯一性、跨字段不变量、关系成环、articulation 语义、JSON schema 暴露面。 | `test_scene_spec_rejects_backend_fields`、`test_scene_spec_rejects_missing_support_and_relation_cycles`、`test_scene_spec_accepts_nested_support_and_rejects_support_cycles`、`test_json_schema_exposes_only_semantic_scene_fields` | 改 `schema.py` 先改 / 先跑这里 |
| `tests/scene_gen/test_parser.py` | golden 双语 prompt 稳定性 + 反向拒绝 + 方向/距离语义 + provider 反走私。 | `test_all_bilingual_golden_prompts_are_stable_and_schema_valid`、`test_all_invalid_golden_prompts_are_rejected`、`test_provider_payload_cannot_smuggle_backend_fields_or_change_request`、`test_parser_supports_chinese_stack_inside_and_articulation` | 改 `parser.py` 先跑这里 |
| `tests/scene_gen/test_catalog.py` | 真实路径扫描可复现；缺碰撞/缺稳定朝向必拒；嵌套 articulation 与关节限位。 | `test_catalog_scans_real_paths_and_is_reproducible`、`test_catalog_requires_collision_dimensions_and_stable_pose`、`test_catalog_scans_nested_articulated_models_and_joint_limits` | 改 `catalog.py` 或 override 先跑 |
| `tests/scene_gen/test_grounding.py` | grounding 选真实可碰撞模型；同 catalog+seed 必复现。 | `test_grounding_selects_real_usable_models_without_inventing_ids`、`test_grounding_is_reproducible_for_fixed_catalog_query_and_seed` | 改 `grounding.py` 先跑 |
| `tests/scene_gen/test_solver.py` | 求解可复现；保留真实绝对路径；满足全部几何关系；100-seed 稳定性门槛；不可行工作区失败带机读 trace；articulation qpos 映射。 | `test_solver_is_deterministic_and_preserves_real_asset_paths`、`test_solver_meets_all_geometric_relations`、`test_fixed_100_seed_gate_passes_for_the_declared_can_basket_case`、`test_impossible_workspace_fails_with_bounded_machine_readable_trace`、`test_solver_rejects_a_source_that_cannot_fit_the_stable_support_surface`、`test_solver_maps_semantic_articulation_to_all_movable_joint_qpos` | 改 `solver.py` 或 `support_geometry.py` 先跑这里 |
| `tests/scene_gen/test_builder_validator.py` | builder 写哈希绑定包 + `verify_package` 篡改检测；静态 + 运行时 validator；运行时门控的全部攻击模式。 | `test_builder_writes_hash_bound_resolved_only_replay_package`、`test_package_verifier_detects_tampering`、`test_runtime_validator_requires_each_object_visibility_and_physics`、`test_runtime_v2_validates_dynamic_relations_instead_of_exact_spawn_pose`、`test_runtime_validator_accepts_explicit_fixed_static_support_only_for_static_objects`、`test_static_validator_rejects_edge_placement_even_inside_outer_plate_bounds`、`test_static_validator_rejects_target_local_container_overflow`、`test_runtime_validator_rejects_static_contact_free_nested_support`、`test_runtime_validator_rejects_intermittent_nested_contact`、`test_runtime_validator_rejects_nested_source_contacting_table` | 改 `builder.py`/`validator.py`/`runtime_sampling.py` 必跑 |
| `tests/scene_gen/test_asset_generator.py` | catalog miss 生成可回放代理 + provenance；确定性复用；derived uniform scale 缩到能塞进 plate；运行时不稳 block 用保维代理；mesh 边界对得上声明尺寸。 | `test_catalog_miss_generates_replayable_robotwin_proxy_with_provenance`、`test_incompatible_block_is_derived_at_a_uniform_scale_and_fits_plate`、`test_runtime_unstable_block_uses_dimension_preserving_proxy_on_table`、`test_proxy_mesh_bounds_match_declared_dimensions` | 改 `asset_generator.py` 先跑 |
| `tests/scene_gen/test_generated_scene.py` | 回放入口只构建 resolved 资产并登记 footprint；CLI 失败写结构化输入失败报告。 | `test_generated_scene_loads_only_resolved_assets_and_registers_footprints`、`test_generate_scene_cli_writes_structured_input_failure` | CI 只验可导入表面；真机回放在 RoboTwin 环境另跑 |
| `tests/scene_gen/test_acceptance.py` | 95% 通过率聚合 + 空输入 / 非法阈值拒绝。 | `test_acceptance_requires_at_least_95_percent_passes`、`test_acceptance_rejects_empty_and_invalid_threshold` | 改 `acceptance.py` 先跑 |
| `tests/scene_gen/test_acceptance_runner.py` | seed 多目录独立保留；批量汇总要求保留证据。 | `test_seed_scene_dir_retains_each_seed_independently`、`test_batch_summary_requires_retained_evidence` | 改 batch runner 先跑 |
| `tests/scene_gen/test_prompt_matrix.py` | committed 矩阵每 seed 可解析；视频采样覆盖全 settle 窗口且无重复。 | `test_prompt_matrix_parses_deterministically_for_every_declared_seed`、`test_video_samples_cover_full_settle_window_without_duplicates` | 改矩阵或视频采样器先跑 |
| `tests/scene_gen/test_rendered_critic.py` | VLM 评审要求完整机读检查；缺图缺回答 fail-closed；可修复一次 incomplete；major issue 不能 pass。 | `test_rendered_critic_requires_complete_machine_readable_vlm_checks`、`test_rendered_critic_cannot_pass_with_major_issue`、`test_rendered_critic_fails_closed_on_missing_checks_or_images` | 可选路径；只装了 `vlm` extra 才有意义 |
| `tests/fixtures/asset_catalog.json` | committed catalog fixture（无需 RoboTwin checkout）。 |—| 被几乎所有 `test_<module>.py` 加载；扩展而非重塑 |
| `tests/fixtures/golden_prompts.json` | golden prompt fixture。 |—| 被 `test_parser.py` 用 |
| `tests/fixtures/prompt_matrix.json` | 11 例中英 prompt × 3 seed，含 1 例预期 solver 拒绝。 | `infeasible_apple_plate_back_region`（`expect: reject`、`expected_failure_stage: solver`） | 被 `run_prompt_matrix.py` 与 `test_prompt_matrix.py` 用 |
| `tests/demo/` | Flask API 测试，无真实 GPU；见下小节。 |—| 改 `demo/app.py` 先跑；`pytest -q tests/demo` |

## `self_improving/harness/`

| 重要代码 | 功能 | 关键符号 | 验证 |
| --- | --- | --- | --- |
| `schemas/base.py`、`schemas/common.py` | 严格 frozen 基类、Skill 标识/SemVer/SHA-256 原语，以及 v1/v2 Descriptor、Invocation、RunState、Event、ArtifactRef、Blocker、Qualification。 | `HarnessModel`、`SkillDescriptor`、`SkillDescriptorV2`、`ExecutionReproducibility`、`Invocation`、`RunState` | `test_common_schemas.py`、`test_descriptor_reproducibility.py`；v1 只允许 bitwise deterministic，v2 可声明 evidence-invariant repeatability |
| `schemas/text2env.py` | compile/replay/validate 六个输入输出，以及对现有哈希绑定包的不可变引用；不复制 `scene_gen` 载荷。 | `EnvironmentPackage`、`Text2EnvCompileInput`、`Text2EnvReplayInput`、`Text2EnvValidateOutput` | `tests/self_improving/harness/test_text2env_schemas.py`；语句和分支覆盖均强制 100% |
| `schemas/text2env_validate_v2.py`、`validate_v2.py` | validate v2 的十二项 CAS-only evidence 输入、决定输出与首层 byte authority；固定一个 CAS，raw ref 全验后才去重，不作物理决定。 | `Text2EnvValidateV2Input`、`Text2EnvValidateV2Output`、`StrictValidateV2CasReader` | `test_validate_v2.py`；两模块 statement/branch 100%。当前只有切片 1，不可注册或晋升 |
| `schema_catalog.py`、`json_schemas/` | PR1 的 14 个公开 `$id` 加 descriptor v2、validate v2 input/output，共 17 份可审阅 JSON Schema snapshot。 | `SCHEMA_MODELS`、`schema_documents`、`export_schema_snapshots` | `python script/export_harness_schemas.py --check`、`test_schema_catalog.py`；v1 snapshot 前后字节身份相同 |
| `artifacts.py`、`events.py`、`event_journal.py`、`registry.py` | 本地摘要复核、callback 事件记录、SQLite append-only 持久主账、通用 exact-version Skill 注册/调用；handler 的 `RunContext` 携带 Invocation 已冻结的同一组只读依赖。 | `LocalArtifactStore`、`RunRecorder`、`SQLiteEventJournal`、`SkillRegistry` | 对应 `tests/self_improving/harness/` 测试；安全/回归边界见 [Harness Schema Tranche](modules/harness-schema-tranche.md) |
| `package_store.py` | 按 manifest 把 package members 发布到 CAS，并在隔离 staging 中重物化、复核后逐目录晋升。 | `PackageStore`、`PublishedPackage`、`PackageStoreError` | `tests/self_improving/harness/test_package_store.py`；它解决 package bytes 重建，不解决完整 run/media receipt |
| `runtime_events.py`、`runtime_capability.py`、`runtime_executor.py`、`runtime_assets.py` | 用专用事件 FD、双 capability 探测和 CAS 资产快照监督隔离的 RoboTwin replay；运行前后复验完整 loader 树，并把 executor 字节与超时/输出上限等策略编入无路径依赖身份。 | `RuntimeEventCodec`、`RuntimeExecutorIdentity`、`describe_runtime_capability`、`SubprocessRoboTwinRuntimeExecutor`、`RuntimeAssetStore` | 对应四组 Harness 测试；真实 2-step can-on-plate smoke 只证明接线，不能替代 900/120 validation |
| `media_sandbox.py`、`native/media_sandbox.c`、`media_verifier.py` | 在 delegated cgroup、Landlock/seccomp/rlimit 中用静态 FFmpeg 从 held FD 完整解码 replay PNG/MP4；复核帧数、fps、尺寸、SAR、互异帧并记录资源指标，缺沙箱不降级。 | `MediaSandbox`、`NativeCgroupSandbox`、`ReplayMediaVerifier`、`SubprocessReplayMediaVerifier` | `test_media_sandbox.py`、`test_media_verifier.py`；真实历史录像 120/114 frames，但这不是物理 gate 或正式 replay qualification |
| `handlers/text2env_replay.py`、`replay_dependencies.py` | replay adapter 精确绑定 capability、executor、handler config、media verifier、input-specific runtime assets 五项 Invocation 依赖；输出经 evidence/media consumer 复核后才晋升 typed artifacts。 | `Text2EnvReplayHandler`、`Text2EnvReplayDependencyResolver`、`build_text2env_replay_wiring`、`text2env_replay_descriptor` | descriptor 已用 v2 evidence-invariant repeatability；固定 loader/generator/application 有完整攻击测试，但尚未生成 pass bundle或完成真实 900/120 replay |
| `qualify_replay.py`、`replay_qualification.py`、`replay_application.py` | 固定案例的 candidate-kernel 资格生成、完整 CAS evidence closure 深验证与 production-only 注册装配；验证对象本身不是发布权限。 | `generate_replay_qualification`、`verify_replay_qualification_bundle`、`create_replay_application`、`EvidenceInvariantRegistrationPolicy` | 5 个核心模块 2,306 statements / 510 branches 全覆盖；仅测试固定证据形状，未生成 checked-in bundle、未运行真实 900/120 |
| `handlers/text2env_compile.py`、`text2env_compile_dependencies.py` | 显式组装后的 compile adapter：从 CAS catalog 调权威 `compile_scene`、发事件、收集制品、复核 package，并记录可变依赖。 | `Text2EnvCompileHandler`、`Text2EnvCompileDependencyResolver`、`text2env_compile_descriptor` | `test_text2env_compile_handler.py`；`ef5e29e`/`910ccb1` 锁住 catalog mutation，外部 asset payload 本身仍非执行 snapshot |
| `application.py`、`compile_cli.py` | 固定 production compile 装配、持久状态与命令行入口；可复用生成资产进入显式的持久资产库，而不是一次运行的 state root。 | `CompileApplicationSettings.asset_library_root`、`DEFAULT_PRODUCTION_ASSET_LIBRARY_ROOT`、`--asset-library-root` | `test_application.py`、`test_compile_cli.py`；干净检出 45 项通过，实验/qualification 必须使用 scratch 资产库 |
| `run_receipts.py` | 把一个 terminal run 的资格、Invocation、RunState、事件 transcript、输入与输出 artifact 复制成单一目标 CAS 可独立回读的收据 closure；CAS 内容身份允许输入/输出显示名不同，但不允许 bytes/media/schema 冲突。 | `PortableRunReceiptPublisher`、`load_portable_run_receipt` | `test_run_receipts.py`，当前 45 passed；收据完整不等于 replay/validate 或物理通过 |
| `system2/context.py`、`planner.py`、`dispatcher.py`、`domain.py`、`history.py` | 把 LLM planner 的 CAS prompt/decision 绑定到精确 compile Skill，重算 compile 语义，发布 canonical `TrustedToolReceipt`，并以完整 history authority 驱动可审计状态增量。 | `System2Planner`、`System2Dispatcher`、`TrustedToolReceipt`、`System2ToolResult`、`PlannerHistoryAuthority` | 四实现模块 261 项定向测试、statement/branch 100%；只派生 catalog/package 非物理事实，blocked/failed 无 delta，证据见 `docs/evidence/system2-compile-dispatch-20260831.md` |
| `qualification.py` | 严格加载一份预制 pass bundle，核 receipt/report/manifest、manifest 已列实现文件及 source-tree 摘要，再发布 CAS snapshot。 | `load_qualification_bundle`、`QualificationReportV1`、`ImplementationManifestV1` | `test_qualification.py`；截至 `ab03859` 无 Registry callsite，不执行资格案例，也不是 promotion transaction |
| `run_store.py` | 以 SQLite 保存 immutable Invocation 与 terminal RunState，并在读写时对账 event journal。 | `SQLiteRunStore`、`RunStoreConflictError`、`RunStoreCorruptionError` | `test_run_store.py`；截至 `ab03859` 未接入 Registry、无 resume、不会重验 artifact bytes 或导出完整 EvaluationRun identity |

PR1 只完成 schema tranche；后续已增加通用 `SkillRegistry`、PackageStore、compile adapter、静态
qualification bundle verifier 与 RunStore adapter，但
固定 `ab03859` 当时 replay/validate、自动 composition 和 MCP adapter 尚未实现。后续已加入独立
validate handler，以及 replay 的 event/capability/executor/immutable-asset/media-verification 底座；
A040 又接出精确消费五项 Invocation 依赖的 replay handler/resolver 候选，但固定 production
qualification/application、新的 900/120 handler receipt 与 descriptor 确定性语义仍未闭合，不能把
这个候选写成端到端 Skill 已晋升。契约文档仍是 `Status: Proposed`，不能
从这些构件推断三个 Skill 已接通、输入冻结、资产晋升具事务性、EvaluationRun 身份闭合或 RFC 已
Accepted。
PR1 的 21 个专项测试是历史基线；当前验证边界见模块页与
[PR1 报告](../docs/contracts/HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md)。

## `self_improving/asset_pipeline/`

| 重要代码 | 功能 | 关键符号 / 配置 | 验证 |
| --- | --- | --- | --- |
| `active/runtime_config.py` | 把仓库、RoboTwin、shadow、catalog、override、运行解释器统一成可迁移默认值和环境变量。 | `ASSET_PIPELINE_ROOT`、`GEN_ENV_ROOT`、`ROBOTWIN_ROOT`、`ROBOTWIN_SHADOW_ROOT`、`ASSET_CATALOG`、`ASSET_OVERRIDES` | `active/1_asset_reuse/tests/test_runtime_config.py` |
| `active/1_asset_reuse/` | 资产发现、采购、转换、实测属性、ledger、catalog 接入与物理验收；v3 gate 解析完整 loader closure，并由共享 writer 边界在发布前复核 files/provenance/receipt。 | `lib/ledger.py`、`lib/ledger_writes.py`、`migrate_v3.py`、`backfill_upstream.py` | asset-reuse 基础环境 `902 passed, 2 skipped`，真实 SAPIEN 对应节点另 `2 passed`；最终公共 S13b→S11 用真实 loader/native modules 完成 `SWEEP 1/1`。跨 Harness `164 passed`；边界是 cooperative POSIX/Linux pre/post snapshot，不是 opened-FD/同用户防伪。既有 162 份 ledger 的数据债务仍 open，见 `docs/evidence/asset-ledger-v3-integrity-20260831.*` |
| `active/web/` | 本地资产流水线 Web Studio。 | `app.py` | 11 passed |
| `active/shared/openxsim/` | 跨仿真 IR、adapter、导入/导出与 conformance。 | `agenticsim.openxsim` | 56 passed |
| `receipts/` | 外部资产的选择、ledger/model metadata 与全文件摘要；不含 mesh/texture/render。 | `asset_library_manifest.json`、`asset_library_301_361.sha256` | JSON 解析、manifest 摘要回读 |
| `branch_overlays/` | 只读保存 alias-screening 与 asset-sources 分支表面。 | 分支来源登记见 `source_inventory.json` | 不作为当前运行入口 |

## `tests/demo/`

| 重要代码 | 功能 | 关键符号 | 调用方 / 使用方 |
| --- | --- | --- | --- |
| `tests/demo/test_harness_compile.py`、`test_app.py`、`test_harness_feed.py` | 真实 qualified compile application、SQLite terminal/Invocation closure、Flask exact payload、SceneSpec 内容 authority 与 feed 公共 seam。 |—| 覆盖声明/读取越界、symlink/FIFO、同 FD 漂移、字节/SHA、严格 JSON/SceneSpec、语义/post-read authority binding 与 HTTP 脱敏；compile module 374 statements / 156 branches 全覆盖 |
| `tests/demo/test_workbench_browser.py` | 真 Flask + 本机 headless Chrome 的事件、提交、审计与按需 SceneSpec 预览端到端门。 |—| 覆盖不自动请求、成功投影、非成功不提供按钮、迟到 abort、XSS/locator、响应漂移/额外字段及关闭后重读；`pytest -q tests/demo` 当前 232 passed（47 Chrome） |

## 覆盖范围

覆盖：稳定核心四区全部进地图——`scene_gen/`、`script/`、`demo/`、`tests/`；平台总览登记 `self_improving/`、`apps/` 与 `external/` 的边界。`self_improving/legacy/robotwin_text2env_alt/` 是只读历史，`apps/pearl_evidence_portal/` 是呈现层；两者都不能覆盖 `scene_gen/` 的当前行为。`scene_gen/envs/generated_scene.py` 与 `scene_gen/prompts/parse_scene.md` 也属 `scene_gen/` 并已登记。

摘要但不逐文件追踪：`scene_gen/prompts/` 的 prompt 模板（provider 路径用，rule-based CLI 不读）、`demo/static/` 的前端资产（薄呈现层，不含流水线逻辑）、`tests/fixtures/` 的 JSON 内部结构（fixture 稳定，扩展而非全文展示）。

相邻但未追踪的路径：`script/run_100_seed_acceptance.py`、`script/run_prompt_matrix.py`、`script/build_stage5_report.py`、`scene_gen/acceptance.py` 都是主路径之上的批量/聚合层，登记在表中但机制不重述——它们编主路径的多次复刻。`scene_gen/rendered_critic.py` 与 `script/run_rendered_critic.py` 的可选 VLM 渲染评判属相邻但显式排除路径——它不产物理证据。`.github/` 工作流文件未列：CI 配置非本指南作用域。

未在本仓库 CI 中跑的验证：所有需要真实 RoboTwin/SAPIEN 与 GPU 的运行时回放。仓库根 `AGENTS.md` 的「Testing Requirements」约定这类改动须另在支持 RoboTwin/SAPIEN 的机器上做真机回放；`docs/evidence/prompt-matrix-20260719.md` 的 10/10 SAPIEN 通过与 `docs/evidence/physics-acceptance-20260717.md` 的 20/20 通过是已记录的真机证据。

要继续追问某个机制的 why，按主路径顺序就近去 `modules/`，或回头读 [一条真实路径](walkthroughs/one-real-run.md) 对应那一步。

证据状态：除特别标注外，本页基于当前源码已确认。

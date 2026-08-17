# 代码地图

主行为模型先在 [一条真实路径](walkthroughs/one-real-run.md) 建好。这里负责把那套行为翻译成「改动从哪里下手」。稳定核心仍是 `scene_gen/`、`script/`、`demo/`、`tests/`；平台组合入口位于 `self_improving/`，呈现层位于 `apps/`，独立项目位于 `external/`。

| 路径 | 职责 | 关键代码 | 与主流程的关系 |
| --- | --- | --- | --- |
| `scene_gen/` | 编译器核心库：契约、解析、grounding、求解、builder、validator、绘制代理、acceptance。 | `schema.py`、`parser.py`、`grounding.py`、`solver.py`、`builder.py`、`validator.py`、`scene_gen/envs/generated_scene.py` | 主流程每一阶段都住在这里；CLI 与 demo 只是薄入口 |
| `script/` | CLI 入口：编译、回放、批量验收、矩阵、可选渲染评判、stage-5 报告。 | `generate_scene.py`、`run_scene_runtime.py`、`run_100_seed_acceptance.py`、`run_prompt_matrix.py` | 编排 `scene_gen`；流水线逻辑加进 `scene_gen`，不要加在这里 |
| `demo/` | Flask 控制面，把 GPU 任务队列入队并按 id 暴露已注册产物。 | `app.py` | 复用同一 `scene_gen` 流水线；不是新流水线，只加队列 + 路由 |
| `tests/` | pytest 套件 + committed fixture；为每个误报模式留攻击测试。 | `tests/scene_gen/test_<module>.py`、`tests/fixtures/{asset_catalog,golden_prompts,prompt_matrix}.json` | 锁住契约与失败分支；套件无需 RoboTwin checkout 即可跑 |
| `self_improving/` | Harness 对外契约、平台编排、闭环诊断、资产复用、仿真适配、来源清单与只读历史。 | `harness/schemas/`、`harness/schema_catalog.py`、`registry.py`、`source_inventory.json`、`asset_pipeline/active/runtime_config.py`、各命名模块 | Harness 只引用权威载荷，平台消费稳定核心；都不能降低 `scene_gen` 门控 |
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

## `demo/`

| 重要代码 | 功能 | 关键符号 | 调用方 / 使用方 |
| --- | --- | --- | --- |
| `demo/app.py` | Flask 控制面：text2env 编译 + 运行时 + VLM 评审端点 + 任务存储 + 已注册资产服务。 | `app`、各 endpoint、由环境变量配置 `ROBOTWIN_ROOT`/`ROBOTWIN_PYTHON`/`SCENE_ASSET_CATALOG`/`SCENE_DEMO_JOBS_ROOT` | 仓库 README「Browser Demo」配方；流水线逻辑仍来自 `scene_gen` |
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
| `schemas/base.py`、`schemas/common.py` | 严格 frozen 基类、Skill 标识/SemVer/SHA-256 原语，以及 Descriptor、Invocation、RunState、Event、ArtifactRef、Blocker、Qualification。 | `HarnessModel`、`SkillDescriptor`、`Invocation`、`RunState`、`derive_mcp_tool_name` | `tests/self_improving/harness/test_common_schemas.py`；语句和分支覆盖均强制 100% |
| `schemas/text2env.py` | compile/replay/validate 六个输入输出，以及对现有哈希绑定包的不可变引用；不复制 `scene_gen` 载荷。 | `EnvironmentPackage`、`Text2EnvCompileInput`、`Text2EnvReplayInput`、`Text2EnvValidateOutput` | `tests/self_improving/harness/test_text2env_schemas.py` |
| `schema_catalog.py`、`json_schemas/` | 14 个公开 `$id` 到模型的不可变目录，以及可审阅的 JSON Schema 快照和漂移检测。 | `SCHEMA_MODELS`、`schema_documents`、`export_schema_snapshots` | `python script/export_harness_schemas.py --check`、`tests/self_improving/harness/test_schema_catalog.py` |

这批代码只完成 schema tranche；`SkillRegistry`、Text2Env handler 和 MCP adapter 尚未实现。契约文档仍是 `Status: Proposed`，不能从目录存在推断为已 Accepted。

## `self_improving/asset_pipeline/`

| 重要代码 | 功能 | 关键符号 / 配置 | 验证 |
| --- | --- | --- | --- |
| `active/runtime_config.py` | 把仓库、RoboTwin、shadow、catalog、override、运行解释器统一成可迁移默认值和环境变量。 | `ASSET_PIPELINE_ROOT`、`GEN_ENV_ROOT`、`ROBOTWIN_ROOT`、`ROBOTWIN_SHADOW_ROOT`、`ASSET_CATALOG`、`ASSET_OVERRIDES` | `active/1_asset_reuse/tests/test_runtime_config.py` |
| `active/1_asset_reuse/` | 资产发现、采购、转换、实测属性、ledger、catalog 接入与物理验收。 | `acquire_batch.py`、`measure_asset_attributes.py`、`s9_build_shadow_root.py` | 模块内 264 passed、1 skipped |
| `active/web/` | 本地资产流水线 Web Studio。 | `app.py` | 11 passed |
| `active/shared/openxsim/` | 跨仿真 IR、adapter、导入/导出与 conformance。 | `agenticsim.openxsim` | 56 passed |
| `receipts/` | 外部资产的选择、ledger/model metadata 与全文件摘要；不含 mesh/texture/render。 | `asset_library_manifest.json`、`asset_library_301_361.sha256` | JSON 解析、manifest 摘要回读 |
| `branch_overlays/` | 只读保存 alias-screening 与 asset-sources 分支表面。 | 分支来源登记见 `source_inventory.json` | 不作为当前运行入口 |

## `tests/demo/`

| 重要代码 | 功能 | 关键符号 | 调用方 / 使用方 |
| --- | --- | --- | --- |
| `tests/demo/test_app.py` | Flask 控制面 API 单元测试；不启用真实 GPU 运行时。 |—| 改 `demo/app.py` 先跑这里；`pytest -q tests/demo` |

## 覆盖范围

覆盖：稳定核心四区全部进地图——`scene_gen/`、`script/`、`demo/`、`tests/`；平台总览登记 `self_improving/`、`apps/` 与 `external/` 的边界。`self_improving/legacy/robotwin_text2env_alt/` 是只读历史，`apps/pearl_evidence_portal/` 是呈现层；两者都不能覆盖 `scene_gen/` 的当前行为。`scene_gen/envs/generated_scene.py` 与 `scene_gen/prompts/parse_scene.md` 也属 `scene_gen/` 并已登记。

摘要但不逐文件追踪：`scene_gen/prompts/` 的 prompt 模板（provider 路径用，rule-based CLI 不读）、`demo/static/` 的前端资产（薄呈现层，不含流水线逻辑）、`tests/fixtures/` 的 JSON 内部结构（fixture 稳定，扩展而非全文展示）。

相邻但未追踪的路径：`script/run_100_seed_acceptance.py`、`script/run_prompt_matrix.py`、`script/build_stage5_report.py`、`scene_gen/acceptance.py` 都是主路径之上的批量/聚合层，登记在表中但机制不重述——它们编主路径的多次复刻。`scene_gen/rendered_critic.py` 与 `script/run_rendered_critic.py` 的可选 VLM 渲染评判属相邻但显式排除路径——它不产物理证据。`.github/` 工作流文件未列：CI 配置非本指南作用域。

未在本仓库 CI 中跑的验证：所有需要真实 RoboTwin/SAPIEN 与 GPU 的运行时回放。仓库根 `AGENTS.md` 的「Testing Requirements」约定这类改动须另在支持 RoboTwin/SAPIEN 的机器上做真机回放；`docs/evidence/prompt-matrix-20260719.md` 的 10/10 SAPIEN 通过与 `docs/evidence/physics-acceptance-20260717.md` 的 20/20 通过是已记录的真机证据。

要继续追问某个机制的 why，按主路径顺序就近去 `modules/`，或回头读 [一条真实路径](walkthroughs/one-real-run.md) 对应那一步。

证据状态：除特别标注外，本页基于当前源码已确认。

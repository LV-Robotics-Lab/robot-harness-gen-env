const stageData = {
  parser: { kicker: "01 / INPUT BOUNDARY", path: "scene_gen/parser.py", title: "先把 prompt 变成“只有语义”的对象图", summary: "解析器先拒绝代码、路径、pose 和后端字段，再从双语词典中提取物体、颜色、材质、区域与关系。它不会替用户选择 asset_id，也不会生成坐标。", methods: ["validate_prompt_boundary", "extract_mentions", "_relation_between", "parse_rule_based"], output: "成功：SceneSpec（object_id 由 mention 顺序生成，如 can_1）。失败：SceneSpecError，拒绝原因停在 parser 边界。", pseudo: "normalize(request)\n→ regex boundary checks\n→ dictionary mentions + attributes\n→ relation phrase → RelationSpec\n→ SceneSpec.model_validate(...)\n\n不产生：asset_path / pose / qpos / code", source: "parser.py:parse_rule_based", doc: "../repo-docs/modules/bounded-parser.md" },
  schema: { kicker: "02 / TYPE GATE", path: "scene_gen/schema.py", title: "把对象图锁成不可变、可哈希的契约", summary: "Pydantic 严格模型负责字段类型和跨字段语义。extra=forbid、frozen=True、递归禁用键扫描，让 provider 或下游 dict 不能偷偷带入后端控制字段。", methods: ["SceneSpec.semantic_consistency", "_reject_forbidden_keys", "_has_cycle", "SceneSpec.digest"], output: "成功：SceneSpec.digest() 作为意图根。失败：SceneSpecError，具体到 unknown target、support cycle、distance contradiction。", pseudo: "before: _reject_forbidden_keys(value)\nafter: unique object_id\n  + every object has exactly one support\n  + support / axis graph acyclic\n  + distance min <= max\ncanonical JSON (sorted keys)\n→ SHA-256 digest", source: "schema.py:SceneSpec.semantic_consistency", doc: "../repo-docs/modules/scene-contract.md" },
  grounding: { kicker: "03 / ASSET GROUNDING", path: "scene_gen/grounding.py", title: "让语义落到真实、可碰撞的 catalog model", summary: "ground_object 遍历 catalog，先做 category / semantic_name / alias 匹配，再加颜色、材质、derived proxy、collision 和 dimensions 分数。不可用候选会保留在拒绝记录中。", methods: ["_semantic_score", "_tie_break", "ground_object", "ground_scene"], output: "成功：GroundedSelection（模型、score、reasons、rejected_candidates）。失败：没有 usable candidate 时抛 SceneSpecError。", pseudo: "for entry in catalog.entries:\n  score = semantic match + color/material\n  skip unavailable / unusable model\n  +2 collision, +1 dimensions\naccepted.sort((-score, sha256(seed:object:asset:model)) )\n→ deterministic winner + top-25 rejects", source: "grounding.py:ground_object", doc: "../repo-docs/code-map.md" },
  solver: { kicker: "04 / TARGET-LOCAL GEOMETRY", path: "scene_gen/solver.py + support_geometry.py", title: "先放支撑物，再在局部稳定面上求解", summary: "solve_scene 按 support depth 排序，递归 place 每个物体。每次尝试采样 yaw/xy，检查 workspace、keepout、三维重叠和所有已知关系；失败后继续尝试，递归失败则 bounded backtrack。", methods: ["solve_scene", "place(index)", "_candidate_reasons", "sample_supported_offset", "_articulation_qpos"], output: "成功：ResolvedSceneSpec + SolverTrace。失败：SceneSolveError，报告 max_attempts、max_backtracks 和逐次 attempts reason。", pseudo: "order = support_depth ↑, relation_degree ↓\nplace(i):\n  target = assigned[support.target]\n  sample offset inside target-local surface\n  reasons = precheck + pair constraints\n  if none: assign → place(i+1)\n  else: retry (≤96)\n  recursive fail: pop + backtrack (≤48)", source: "solver.py:solve_scene", doc: "../repo-docs/modules/solver.md" },
  package: { kicker: "05 / REPLAY PACKAGE", path: "scene_gen/builder.py", title: "把 resolved scene 做成自证的回放包", summary: "builder 只接受与 SceneSpec digest、scene_id、seed 相互绑定的 resolved 结果，然后写 request、scene spec、resolved scene、generated entrypoint 和 manifest。", methods: ["build_scene_package", "generated_module_source", "verify_package", "_sha256"], output: "成功：package_manifest.json。篡改或缺文件：verify_package 返回 fail；canonical resolved digest 也会复核。", pseudo: "assert resolved.source_scene_spec_sha256 == spec.digest()\nwrite 4 payload files + generated_scene.py\nmanifest.files = SHA-256 + bytes\nmanifest.resolved_scene_sha256 = resolved.digest()\n\nverify: every file hash + canonical digest", source: "builder.py:build_scene_package", doc: "../repo-docs/modules/replay-package.md" },
  runtime: { kicker: "06 / PHYSICAL REPLAY", path: "script/run_scene_runtime.py", title: "在 RoboTwin/SAPIEN 中留下释放全过程", summary: "runtime 入口只加载 ResolvedSceneSpec，创建 actor，记录初始 pose 与 contacts，然后按 total_steps 推进物理。默认 precheck=0，视频采样覆盖释放帧和最终 settled 帧。", methods: ["load_robotwin_args", "summarize_contacts", "runtime_support_margin", "runtime_inside_contained", "video_sample_steps"], output: "成功：runtime_evidence.json，含 objects、contacts、relations、video_frame_count/unique_frame_count、resolved_scene_sha256。", pseudo: "resolved = ResolvedSceneSpec.model_validate_json(...)\nload actors → initial contacts / poses\ntotal_steps = max(settle_steps, video_frames)\nfor step in total_steps:\n  scene.step()\n  sample video + final window contacts\n→ structured evidence (not screenshots only)", source: "run_scene_runtime.py:main", doc: "../repo-docs/walkthroughs/one-real-run.md" },
  validator: { kicker: "07 / ACCEPTANCE GATES", path: "scene_gen/validator.py", title: "把静态契约和物理证据翻译成 pass / incomplete / fail", summary: "validate_resolved_scene 先验 bounds、关系、roundtrip、manifest；若提供 runtime evidence，再按 static/dynamic 分支检查 drift、接触 fraction、unexpected contact、margin、containment、可见性、视频和 articulation。", methods: ["validate_resolved_scene", "_relation_pass", "_support_surface", "_interior_aabb", "_check"], output: "fail_count > 0 → fail；无 fail 但有 not_run → incomplete；所有 checks 通过 → pass。rendered_critic 只看可见语义，不是物理证据。", pseudo: "static checks → runtime_evidence?\n  no: runtime_evidence = not_run\n  yes: video + robot collision\n       per-object drift / settled / contact\n       nested: unexpected targets == []\n       on_top_of: margin >= target.support_margin\n       inside: inside_contained == true\nstatus = fail | incomplete | pass", source: "validator.py:validate_resolved_scene", doc: "../repo-docs/modules/runtime-gates.md" },
  platform: { kicker: "08 / PLATFORM CONSUMER", path: "self_improving/ + apps/ + external/", title: "外围平台把核心产物组织起来，但不能改写它", summary: "self_improving 负责选择、采集、训练、评估、诊断、资产复用和跨仿真适配；Harness tranche 提供 14 个严格 schema 和权威 ArtifactRef。Registry、Text2Env handler、MCP adapter 仍未实现。", methods: ["audit_repository", "schema_documents", "export_schema_snapshots", "EnvironmentPackage"], output: "平台输出应引用 resolved scene、catalog、manifest 的摘要与 URI，不复制内部 payload，也不能把 validator 结论降级。", pseudo: "scene_gen = stable owner\nharness = typed audit boundary\nplatform = orchestration / diagnosis / assets\napps = presentation only\nexternal = pinned submodules\n\nnew work: consume authoritative payloads", source: "self_improving/harness/schema_catalog.py", doc: "../repo-docs/modules/self-improving-platform.md" }
};

const modules = [
  { id: "schema", group: "core", path: "scene_gen/schema.py", title: "类型化场景契约", summary: "严格、不可变的 SceneSpec / ResolvedSceneSpec，跨字段不变量和 canonical SHA-256。", methods: "SceneSpec.semantic_consistency · digest · ResolvedPose.normalized_quaternion", tests: "tests/scene_gen/test_schema.py", detail: "先递归拒绝 backend keys，再验证唯一 object、唯一 support relation、无关系环和距离约束。resolved 的 pose quaternion 必须归一化。" },
  { id: "parser", group: "core", path: "scene_gen/parser.py", title: "受限双语解析器", summary: "词典 + 正则，把自然语言压缩成 object / relation 语义，不产出代码和 pose。", methods: "validate_prompt_boundary · extract_mentions · _relation_between · parse_rule_based", tests: "tests/scene_gen/test_parser.py", detail: "先拒绝 executable code、路径、坐标、backend field 和 unsupported between/alignment；然后按 span 抽取 mention、属性、数量与关系。" },
  { id: "catalog", group: "core", path: "scene_gen/catalog.py", title: "RoboTwin 资产目录", summary: "扫描真实文件，记录尺寸、碰撞、稳定朝向、容器 interior、关节限位并合并 measured overrides。", methods: "scan_robotwin_assets · _scan_model · load_catalog · AssetCatalog.digest", tests: "tests/scene_gen/test_catalog.py", detail: "缺 metadata、collision、stable pose 或 articulation 信息的模型标为 unusable；asset_overrides.yml 负责 plate stable surface、basket floor offset 等实测事实。" },
  { id: "grounding", group: "core", path: "scene_gen/grounding.py", title: "确定性资产 grounding", summary: "语义打分、可用性过滤、collision/dimensions 加分和 seed 绑定 tie-break。", methods: "_semantic_score · _tie_break · ground_object · ground_scene", tests: "tests/scene_gen/test_grounding.py", detail: "同一 catalog + seed + object_id 会得到同一 winner；lower-ranked candidates 仍进入 rejected_candidates，方便审计。" },
  { id: "geometry", group: "core", path: "scene_gen/support_geometry.py", title: "目标局部几何", summary: "在 target 的 stable support surface / interior 中计算完整 footprint margin。", methods: "footprint_2d · support_footprint_margin · sample_supported_offset · support_surface_z", tests: "tests/scene_gen/test_solver.py · test_builder_validator.py", detail: "矩形面先转 target-local 坐标；圆面按半径计算。负 margin 表示 overhang，不能用 outer AABB 代替。" },
  { id: "solver", group: "core", path: "scene_gen/solver.py", title: "受限求解器", summary: "bounded rejection sampling + recursive backtracking，留下逐次机器可读 trace。", methods: "solve_scene · place(index) · _candidate_reasons · _articulation_qpos", tests: "tests/scene_gen/test_solver.py", detail: "支撑深度决定排序；nested source 必须动态；on_top_of / inside 先向目标局部几何采样，再检查 keepout、重叠和 pair relations。" },
  { id: "asset", group: "core", path: "scene_gen/asset_generator.py", title: "确定性代理资产", summary: "catalog miss 或不稳时生成 procedural primitive / derived uniform scale，并记录 lineage。", methods: "ensure_assets_for_scene · _write_proxy_asset · _uniform_scale_limit · _prism_obj", tests: "tests/scene_gen/test_asset_generator.py", detail: "SCALE_HEADROOM=0.92、MIN_DERIVED_SCALE=0.35；自动缩放只对白名单 block/cube 类生效，provenance 写入 source asset/model 和 scale factor。" },
  { id: "builder", group: "core", path: "scene_gen/builder.py", title: "哈希绑定回放包", summary: "写入请求、契约、resolved、入口和 manifest，verify_package 负责篡改检测。", methods: "build_scene_package · generated_module_source · verify_package", tests: "tests/scene_gen/test_builder_validator.py", detail: "manifest 同时绑定 source SceneSpec、resolved scene、asset catalog 和每个文件的 SHA-256；resolved-only loader 是唯一可信回放入口。" },
  { id: "validator", group: "core", path: "scene_gen/validator.py", title: "静态 + 运行时 validator", summary: "把几何、物理、可见性、视频和 articulation 证据变成可审计 checks。", methods: "validate_resolved_scene · _relation_pass · _check", tests: "tests/scene_gen/test_builder_validator.py", detail: "静态 on_table fixed_static_pose 是唯一允许不要求 contact fraction 的例外；nested static contact-free、intermittent contact、碰桌均有攻击测试。" },
  { id: "entry", group: "entry", path: "script/ + demo/", title: "CLI 与浏览器控制面", summary: "generate_scene、runtime、批量 acceptance、prompt matrix 和 Flask job queue 都是薄编排。", methods: "generate_scene.main · run_scene_runtime.main · ScenePipeline._run · create_app", tests: "tests/scene_gen/test_run_scene_runtime.py · tests/demo/test_app.py", detail: "逻辑仍归 scene_gen；demo 只负责入队、stage 状态和白名单 artifact 服务，不创建第二条流水线。" },
  { id: "acceptance", group: "entry", path: "scene_gen/acceptance.py + script/", title: "批量验收与渲染评判", summary: "acceptance 只聚合 ≥95% 通过率；rendered_critic 只评 visible semantics。", methods: "summarize_acceptance · run_100_seed_acceptance · run_prompt_matrix · review_rendered_scene", tests: "tests/scene_gen/test_acceptance.py · test_rendered_critic.py", detail: "render is preview；接触、支撑、containment、drift 和 articulation 仍以 deterministic physics gates 为准。" },
  { id: "harness", group: "platform", path: "self_improving/harness/", title: "Harness Schema tranche", summary: "14 个公开 JSON Schema，冻结运行记录、ArtifactRef、EnvironmentPackage 和 Text2Env 边界。", methods: "schema_model · schema_documents · export_schema_snapshots", tests: "tests/self_improving/harness/ · export_harness_schemas.py --check", detail: "extra=forbid、frozen=True；ArtifactRef 用 media_type + schema_version + sha256 认内容，URI 只负责定位。" },
  { id: "platform", group: "platform", path: "self_improving/", title: "Self-Improving 平台层", summary: "编排选择、采集、训练、评估、诊断、资产复用、迁移与历史来源。", methods: "registry.audit_repository · stage5 · alchedata · asset_pipeline · sim_adapters", tests: "script/run_self_improving_tests.sh", detail: "平台是 core contract 的消费者；当前仓库仍明确区分稳定核心、Harness 契约、编排、资产、适配、呈现和历史层。" },
  { id: "portal", group: "platform", path: "apps/pearl_evidence_portal/ + external/", title: "呈现层与外部子模块", summary: "PEARL 只呈现已有证据；OpenReal2Sim、digital-cousins、MetaSim 保留独立生命周期。", methods: "app/page.tsx · external submodule commits", tests: "portal rendered HTML tests · submodule audit", detail: "呈现层不能产出 acceptance 结论，外部项目只钉 commit，不复制 vendor tree。" }
];

const glossary = [
  ["SceneSpec", "文本经过 parser 后的语义契约；没有 asset_id、路径、pose 或 qpos。"],
  ["ResolvedSceneSpec", "grounding + solver 后的可回放产物，含真实资产、pose、support geometry 和 lineage。"],
  ["support margin", "source 完整 footprint 到 target 局部稳定面边界的最小平面余量；负值就是 overhang。"],
  ["runtime evidence", "SAPIEN 回放生成的结构化 JSON；包括终态 pose、接触 fraction、visibility、video counts。"],
  ["fixed_static_pose", "静态且直接 on_table 的唯一特殊支持模式；不等于 nested static 可以免接触。"],
  ["derived proxy", "从 catalog miss / 不稳资产确定性生成的代理；必须带 source lineage 与 scale factor。"],
  ["incomplete", "validator 没有 fail，但仍有 not_run checks，通常表示尚未提供 runtime evidence。"],
  ["authority boundary", "scene_gen 负责验收事实；平台、portal、MCP 只能引用和组织，不能重新定义事实。"],
  ["attack test", "专门构造一个曾经会误报的输入，保证修复不会回退的测试。"]
];

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function updateStage(step) {
  const data = stageData[step] || stageData.parser;
  $$(".stage-tab").forEach((tab) => { const active = tab.dataset.step === step; tab.classList.toggle("is-active", active); tab.setAttribute("aria-selected", active); });
  $("#detailKicker").textContent = data.kicker;
  $("#detailPath").textContent = data.path;
  $("#detailTitle").textContent = data.title;
  $("#detailSummary").textContent = data.summary;
  $("#detailOutput").textContent = data.output;
  $("#detailPseudo").textContent = data.pseudo;
  $("#detailSource").textContent = data.source;
  $("#detailDoc").href = data.doc;
  $("#detailMethods").innerHTML = data.methods.map((method) => `<span class="method-pill">${method}</span>`).join("");
}

function renderModules(filter = "all", query = "") {
  const normalized = query.trim().toLowerCase();
  const visible = modules.filter((item) => (filter === "all" || item.group === filter) && (!normalized || [item.path, item.title, item.summary, item.methods, item.detail, item.tests].join(" ").toLowerCase().includes(normalized)));
  $("#moduleGrid").innerHTML = visible.map((item, index) => `<article class="module-card" data-module-id="${item.id}"><button class="module-summary" aria-expanded="false"><span class="module-index">${String(index + 1).padStart(2, "0")}</span><span><h3>${item.title}</h3><p>${item.summary}</p></span><span class="module-toggle">+</span></button><div class="module-detail"><dl><dt>路径</dt><dd><code>${item.path}</code></dd><dt>关键方法</dt><dd><code>${item.methods.replaceAll(" · ", "</code> <code>")}</code></dd><dt>逻辑</dt><dd>${item.detail}</dd></dl><div class="test-line">最近验证 → ${item.tests}</div></div></article>`).join("");
  $("#moduleEmpty").classList.toggle("is-hidden", visible.length > 0);
  $$(".module-summary").forEach((button) => button.addEventListener("click", () => { const card = button.closest(".module-card"); const open = card.classList.toggle("is-open"); button.setAttribute("aria-expanded", open); $(".module-toggle", button).textContent = open ? "−" : "+"; }));
}

function setModuleFilter(filter) {
  $$(".filter-tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.filter === filter));
  renderModules(filter, $("#moduleSearch").value);
}

function runSimulation() {
  const request = $("#promptInput").value.trim();
  const bad = /```|\b(?:import|exec|eval)\s*\(|\b(?:asset_id|model_id|qpos|quaternion|world_xyz)\b|\b[xyz]\s*=\s*-?\d|\[\s*-?\d+(?:\.\d+)?\s*,/.test(request);
  const isInside = /inside|within|放进|放入|里面|里/.test(request.toLowerCase());
  const isLeft = /left|左/.test(request.toLowerCase());
  const isChinese = /[\u3400-\u9fff]/.test(request);
  let objects = [];
  if (/can|罐|易拉罐/.test(request.toLowerCase())) objects.push(["can_1", "can", "071_can"]);
  if (/plate|dish|盘/.test(request.toLowerCase())) objects.push(["plate_1", "plate", "003_plate"]);
  if (/cup|杯/.test(request.toLowerCase())) objects.push(["cup_1", "cup", "021_cup"]);
  if (/basket|篮|筐/.test(request.toLowerCase())) objects.push(["basket_1", "basket", "110_basket"]);
  if (/block|cube|方块/.test(request.toLowerCase())) objects.push(["block_1", "block", "004_fluted-block"]);
  if (!objects.length && !bad) objects = [["object_1", "object", "catalog lookup"]];
  $$("[data-sim-node]").forEach((node) => node.classList.remove("is-done", "is-current"));
  if (bad) {
    $("#simStatus").textContent = "REJECTED"; $("#simStatusText").textContent = "prompt boundary 命中 forbidden pattern"; $("#simCallout").classList.add("is-error"); $("#simCallout").innerHTML = "<code>SceneSpecError</code> 在 parser 边界抛出；请求不会进入 grounding 或 solver。"; $("[data-sim-node=input]").classList.add("is-done"); $("[data-sim-node=parse]").classList.add("is-current"); $("#simObjects").innerHTML = `<span class="object-chip">raw text rejected</span>`; $("#simRelations").innerHTML = `<span class="muted">no SceneSpec emitted</span>`; return;
  }
  const relation = isInside ? "inside" : isLeft ? "left_of" : /top|上|面/.test(request.toLowerCase()) ? "on_top_of" : "on_table";
  $("#simStatus").textContent = "COMPILED"; $("#simStatusText").textContent = `${isChinese ? "mixed / zh" : "en"} · semantic trace ready`; $("#simCallout").classList.remove("is-error"); $("#simCallout").innerHTML = "提示：静态 validator 可能给出 <code>incomplete</code>，因为它还没有 runtime evidence。";
  ["input", "parse", "resolve", "evidence"].forEach((name) => $("[data-sim-node=" + name + "]").classList.add("is-done"));
  $("[data-sim-node=evidence]").classList.add("is-current");
  $("#simObjects").innerHTML = objects.map(([id, category, asset]) => `<span class="object-chip">${id} · ${category} · ${asset}</span>`).join("");
  $("#simRelations").innerHTML = `<span class="relation-chip">${objects[0]?.[0] || "object_1"} → ${relation}${objects[1] ? " → " + objects[1][0] : " → table"}</span>`;
}

function renderGlossary(query = "") {
  const normalized = query.trim().toLowerCase();
  const visible = glossary.filter(([term, detail]) => !normalized || `${term} ${detail}`.toLowerCase().includes(normalized));
  $("#glossaryList").innerHTML = visible.map(([term, detail]) => `<div class="glossary-item"><b>${term}</b><span>${detail}</span></div>`).join("");
}

$$('.stage-tab').forEach((tab) => tab.addEventListener('click', () => updateStage(tab.dataset.step)));
$$('.route-tab').forEach((tab) => tab.addEventListener('click', () => { const route = tab.dataset.route; $$('.route-tab').forEach((item) => { const active = item === tab; item.classList.toggle('is-active', active); item.setAttribute('aria-selected', active); }); $$('[data-route-panel]').forEach((panel) => panel.classList.toggle('is-hidden', panel.dataset.routePanel !== route)); }));
$$('[data-jump-step]').forEach((link) => link.addEventListener('click', () => updateStage(link.dataset.jumpStep)));
$$('[data-module-filter]').forEach((link) => link.addEventListener('click', () => setModuleFilter(link.dataset.moduleFilter)));
$$('.filter-tab').forEach((tab) => tab.addEventListener('click', () => setModuleFilter(tab.dataset.filter)));
$("#moduleSearch").addEventListener("input", () => renderModules($(".filter-tab.is-active").dataset.filter, $("#moduleSearch").value));
$$('.gate-tab').forEach((tab) => tab.addEventListener('click', () => { const gate = tab.dataset.gate; $$('.gate-tab').forEach((item) => item.classList.toggle('is-active', item === tab)); $$('[data-gate-panel]').forEach((panel) => panel.classList.toggle('is-hidden', panel.dataset.gatePanel !== gate)); }));
$$('.sample-prompts button').forEach((button) => button.addEventListener('click', () => { $("#promptInput").value = button.dataset.prompt; $("#promptInput").focus(); }));
$("#runSimulation").addEventListener("click", runSimulation);
$("#glossarySearch").addEventListener("input", () => renderGlossary($("#glossarySearch").value));

updateStage("parser");
renderModules();
renderGlossary();

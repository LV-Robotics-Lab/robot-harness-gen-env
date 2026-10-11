const architecture = {
  intake: {
    index: "01", title: "输入接收", status: "当前启用", tone: "live",
    metaphor: "像前台登记：先确认你交来的是文字、图片、视频，还是它们的组合，再给整份请求一个不可混淆的内容指纹。",
    related: ["controller"],
    inside: [
      ["InputBundle", "统一保存文字、媒体类型、尺寸、帧数和 seed。"],
      ["边界检查", "限制文件大小、图片像素、视频时长和输入组合。"],
      ["来源绑定", "每个媒体都记录 SHA-256 内容指纹，后续不能悄悄换图。"]
    ],
    tradeoff: [
      ["严格输入换来可追溯", "拒绝模糊或超限输入，会牺牲一点“什么都能塞”的方便。"],
      ["视频先采样", "控制模型成本，但当前只恢复静态环境，不复演动作。"]
    ],
    facts: [["四种入口", "text / image / video / image+text 均有真实简单案例。"], ["组合优先级", "S04 中显式文字颜色覆盖参考图颜色。"]]
  },
  controller: {
    index: "02", title: "Harness 控制器", status: "当前启用", tone: "live",
    metaphor: "像项目经理：决定先做什么、什么时候停，以及哪些结果有资格进入下一步。",
    related: ["intake", "agent", "assets", "compile", "package"],
    inside: [
      ["持久状态机", "Harness.resume / _resume 按已有产物决定下一阶段；不是无限 LLM 对话循环。"],
      ["操作预算", "模型重试、资产修订和场景修订都有上限。"],
      ["Controller approval", "有副作用的资产修改要先预留并批准。"],
      ["失败包", "即使中途停止，也导出可审查的输入、错误和已有产物。"]
    ],
    tradeoff: [
      ["Fail closed", "证据不完整就停，成功率看起来更低，但不会用猜测冒充通过。"],
      ["恢复比重跑复杂", "换来幂等和预算不被重复消耗。"]
    ],
    facts: [["循环代码", "harness.py:65–188；有界修订在 1125–1297。"], ["单一公共入口", "一次 submit 贯穿 interpret → package。"], ["成功不等于发布", "workflow succeeded 与 sim_ready / release_qualified 分开。"]]
  },
  agent: {
    index: "03", title: "Codex 模型", status: "当前启用", tone: "live",
    metaphor: "像懂图也懂文字的研究助理：把自然语言和媒体整理成严格的场景意图，也负责有限的视觉诊断。",
    related: ["controller", "assets", "validate"],
    inside: [
      ["Interpret", "抽取实体、关系、已知值、未知值和来源。"],
      ["Ground / diagnose advice", "提出布局、检索词、视觉问题或有限修订建议。"],
      ["严格输出合同", "模型只提案；本地 schema 和完成门重新核验。"]
    ],
    tradeoff: [
      ["统一 max reasoning", "提高复杂合同遵循，但真实案例出现过 600 秒长尾超时。"],
      ["模型没有最终权力", "多一次核验成本，换来可重复、可拒绝的边界。"]
    ],
    facts: [["固定实验模型", "openai/gpt-5.6-terra。"], ["主提示词", "codex.py:362–408；没有独立 SYSTEM_PROMPT.md。"], ["发送与回执", "codex.py:559–646 保存 prompt.txt，再以只读、禁工具进程执行。"], ["推理设置", "所有内部 Codex 角色显式请求 max；不自动降级。"]]
  },
  assets: {
    index: "04", title: "资产解析", status: "部分受限", tone: "scoped",
    metaphor: "像道具管理员：先翻自己的库，再查合规网络来源，最后才考虑从图片重建新物体。",
    related: ["agent", "compile", "package"],
    inside: [
      ["Yuxin local", "Registry 投影进入 RoboTwinLocalProvider.search_phrases，再过尺寸、预览与视觉门。"],
      ["Yuxin web", "复用 load_providers / tiered_search；Harness 另查文件、许可和来源版本。"],
      ["Gujie reconstruction", "Codex 提分割点框，固定 Gujie seam 执行 SAM2 + TRELLIS；仅授权输入可登记。"],
      ["不可变版本", "改颜色等修订会生成 child，不覆盖原资产。"]
    ],
    tradeoff: [["复用优先", "更快也更可靠，但小资产库会提高 blocked 概率。"], ["重建最后使用", "新颖性更强，但成本、许可和几何质量风险都更高。"]],
    facts: [["本地复用", "S01 同一 workflow 13 operations 通过，物理 26/26、视觉 passed。"], ["Web 证据", "916be468… 真正二次检索，最终 blocked_license。"], ["重建证据", "15dc259c… 走到 ground；保温杯 82779899… 生成 7.5MB GLB 后被许可门阻断。"]]
  },
  compile: {
    index: "SKILL 1/3", title: "x2env.compile", status: "公开 Skill · 1.0.0", tone: "live",
    metaphor: "像工程制图：把模型理解出的意图，结合真实资产尺寸，变成物理引擎可以精确加载的文件。",
    related: ["assets", "replay", "package"],
    inside: [
      ["SceneIR", "场景的机器可读中间表示：物体、位置、关系和来源。"],
      ["Grounding", "用资产实测尺寸推导支撑高度，不让模型猜 Z。"],
      ["RuntimeScene", "冻结引擎输入、碰撞形状、材质和哈希引用。"]
    ],
    tradeoff: [["确定性胜过自由生成", "相同输入与版本可重算；不支持的复杂关系会明确拒绝。"], ["几何门很严格", "凹盘、孔洞和多接触会暴露真实能力缺口。"]],
    facts: [["代码", "skill_execution.py:49–62；Harness 在 harness.py:824–875 按 1.0.0 调用。"], ["S01 实跑", "compile 0.008103 秒 succeeded；随后才进入 replay。"], ["边界", "CompiledScene 与静态诊断不授予物理通过。"]]
  },
  replay: {
    index: "SKILL 2/3", title: "x2env.replay", status: "公开 Skill · 1.0.0", tone: "live",
    metaphor: "像把布景真正放到实验台：不是看截图，而是放手、等待、记录接触与漂移。",
    related: ["compile", "validate", "package", "vlm"],
    inside: [
      ["双 profile", "baseline 与 half_dt 用不同时间步重复跑，防止偶然通过。"],
      ["连续证据", "记录每帧姿态、接触对、速度、漂移和视频。"],
      ["隔离 child", "运行时只能读取声明的依赖与包内容。"]
    ],
    tradeoff: [["真实物理比较慢", "简单案例约 5–6 分钟，但能发现截图看不到的穿透或不稳定。"], ["渲染不是裁判", "视觉好看也不能覆盖接触失败。"]],
    facts: [["代码", "skill_execution.py:64–74；Harness 在 harness.py:877–946 调用 GenesisReplayExecutor。"], ["S01 实跑", "主 workflow replay 120.397165 秒 succeeded。"], ["copy-run", "baseline + half_dt 118.718138 秒；独立复算物理 26/26。"], ["限制", "post-step reset、机器人 policy 和数据采集未验证。"]]
  },
  validate: {
    index: "SKILL 3/3", title: "x2env.validate", status: "公开 Skill · 1.0.0", tone: "live",
    metaphor: "像论文复核：不相信上一步自报成功，而是从原输入、场景、资产和运行证据重新计算结论。",
    related: ["agent", "replay", "package", "vlm"],
    inside: [
      ["物理评估", "核接触、支撑域、穿透、漂移、拓扑和双 profile 一致性。"],
      ["视觉评估", "单独判断物体、颜色、数量和画面是否符合意图。"],
      ["完成门", "重算关键来源、模型回执、修订历史和产物引用。"]
    ],
    tradeoff: [["多重核验会误拒", "S02/S03/S04 曾因 JSON 键序被误拒；修复后原失败仍保留。"], ["分开视觉与物理", "结论更多，但原因更清楚。"]],
    facts: [["代码", "skill_execution.py:76–122；Harness 在 harness.py:1037–1077 调用并提交结论。"], ["S01 实跑", "validate 0.004727 秒 succeeded；physical 26/26、visual passed。"], ["matrix v2", "四例 visual / physical 均 passed。"], ["包状态", "仍是 development_review，不是正式 release qualification。"]]
  },
  package: {
    index: "08", title: "证据与环境包", status: "当前启用", tone: "live",
    metaphor: "像封存实验材料：代码、资产、媒体、报告和每个内容指纹一起装箱，换目录后仍能核验。",
    related: ["controller", "assets", "compile", "replay", "validate"],
    inside: [
      ["CAS", "Content-Addressed Storage：按内容哈希寻址，内容一变地址就变。"],
      ["Manifest", "列出包中每个成员、大小、哈希与依赖。"],
      ["Delivery", "成功包和失败包都可导出，且拒绝覆盖漂移的目标目录。"]
    ],
    tradeoff: [["证据很多", "包会变大，换来可审计和抗偷换。"], ["可复制不等于已复制运行", "必须在拒读原路径的环境里真的 load / step / 产新媒体。"]],
    facts: [["S04 包", "209 个成员、11,772,987 bytes，成员核验通过。"], ["copy-run", "本轮只完成 S01 一本成功包。"]]
  },
  vlm: {
    index: "计划", title: "轻量 VLM 观察员", status: "尚未接入", tone: "scoped",
    metaphor: "像只负责看画面的助教：可以提醒“少了杯子”或“颜色不对”，但没有资格宣布物理稳定。",
    related: ["replay", "validate"],
    inside: [
      ["可信媒体入口", "只读取已解码、哈希绑定的 replay 图片或视频。"],
      ["Advisory receipt", "输出可放弃的语义观察，并永远声明 claims_physical_pass=false。"],
      ["历史候选", "Qwen2.5-VL 3B / 7B 候选有代码和预检，尚无正式研究推理。"]
    ],
    tradeoff: [["小模型成本低", "适合可见语义检查；细粒度几何和长视频理解可能不足。"], ["不接物理裁决", "少一条自动决策路径，但保持可信边界清楚。"]],
    facts: [["正式调用", "0 次；canonical replay 尚无 VLM artifact。"], ["旧 Demo", "历史上跑过 Qwen 四图 critic，但属于旁路。"], ["正确顺序", "先 replay 取可信媒体，再让 VLM 观察。"]]
  }
};

let selectedNode = "controller";
let selectedTab = "inside";
const nodeButtons = [...document.querySelectorAll(".arch-node")];
const detailPanel = document.querySelector("#architectureDetail");
const detailBody = document.querySelector("#detailBody");

function renderArchitecture() {
  const data = architecture[selectedNode];
  nodeButtons.forEach((button) => {
    const id = button.dataset.node;
    button.classList.toggle("active", id === selectedNode);
    button.classList.toggle("related", data.related.includes(id));
    button.classList.toggle("dimmed", id !== selectedNode && !data.related.includes(id));
  });
  detailPanel.querySelector(".detail-index").textContent = data.index;
  const chip = detailPanel.querySelector(".status-chip");
  chip.textContent = data.status;
  chip.className = `status-chip ${data.tone}`;
  detailPanel.querySelector("h3").textContent = data.title;
  detailPanel.querySelector(".detail-metaphor").textContent = data.metaphor;
  detailPanel.querySelectorAll(".detail-tabs button").forEach((button) => button.classList.toggle("selected", button.dataset.detailTab === selectedTab));
  const rows = data[selectedTab];
  detailBody.innerHTML = `<ul>${rows.map(([title, text]) => `<li><strong>${title}</strong><small>${text}</small></li>`).join("")}</ul><div class="detail-fact">点击相邻节点，可继续沿着本次请求向前或向后查看。</div>`;
}

nodeButtons.forEach((button) => button.addEventListener("click", () => { selectedNode = button.dataset.node; selectedTab = "inside"; renderArchitecture(); }));
detailPanel.querySelectorAll(".detail-tabs button").forEach((button) => button.addEventListener("click", () => { selectedTab = button.dataset.detailTab; renderArchitecture(); }));
renderArchitecture();

const workflowSteps = [
  { name: "接收", title: "把输入冻结成一份可追踪的请求", input: "文字 / 图片 / 视频 + seed", output: "InputBundle + SHA-256", explain: "从这里开始，任何图片或文本变化都会产生新的内容指纹。" },
  { name: "理解", title: "模型提出场景意图，不直接改环境", input: "InputBundle + 严格 schema", output: "实体 / 关系 / 未知项", explain: "Codex 负责理解语义；本地合同负责拒绝缺字段、矛盾或不可信输出。" },
  { name: "找资产", title: "按 local → web → reconstruction 查找", input: "实体类别 + 外观要求", output: "版本化资产 + 来源回执", explain: "先复用已测量资产。来源、许可证或授权不够时，流程会明确 blocked。" },
  { name: "落地", title: "用真实尺寸决定位置与支撑高度", input: "SceneIR + 资产几何", output: "GroundingValues", explain: "模型不提交最终 Z；高度由真实几何和支撑关系计算。" },
  { name: "编译", title: "生成 Genesis 可以加载的确定性场景", input: "场景 + 资产 + 固定策略", output: "RuntimeScene + manifest", explain: "同一份输入、资产版本和策略应该得到可重算的结果。" },
  { name: "回放", title: "双时间步运行，采集连续物理证据", input: "RuntimeScene", output: "轨迹 / 接触 / 图片 / 视频", explain: "画面只是人类入口；物理结论来自连续轨迹、接触、穿透和漂移。" },
  { name: "验证出包", title: "独立重算后，导出成功包或失败包", input: "原请求 + 全部回执与证据", output: "validation + environment / failure", explain: "流程成功、物理通过、可复制和发布资格分别记录，不合并成含糊的“成功”。" }
];

const scenarios = {
  text: { type: "TEXT INPUT", title: "“Place a can on a plate”", description: "稳定核心验收案例，用来解释从一句话到物理验证的完整路径。", image: "assets/media/can-on-plate-output.png", meta: [["seed", "7"], ["asset", "can + plate"], ["route", "stable core"]] },
  image: { type: "IMAGE INPUT", title: "这张照片里的物体是什么？", description: "用户附图中的黑色 Thermos 保温杯；当前 harness 真实结果见验证区。", image: "assets/media/thermos-input.png", meta: [["seed", "53"], ["text", "none"], ["route", "default"]] },
  mixed: { type: "IMAGE + TEXT", title: "“参考图片，但方块要蓝色”", description: "图片提供物体类别，显式文字覆盖颜色；每个字段保留自己的来源。", image: "assets/media/red-cube-input.png", meta: [["seed", "41"], ["override", "color=blue"], ["route", "default"]] }
};

let workflowScenario = "text";
let workflowIndex = 0;
let workflowTimer = null;
const progressEl = document.querySelector("#workflowProgress");
const workflowDetail = document.querySelector("#workflowDetail");
const requestPreview = document.querySelector("#requestPreview");
const playButton = document.querySelector("#workflowPlay");
const workflowState = document.querySelector("#workflowState");
const timerWrap = document.querySelector(".run-timer");

function renderWorkflow() {
  const scenario = scenarios[workflowScenario];
  requestPreview.innerHTML = `<span class="request-type">${scenario.type}</span><h3>${scenario.title}</h3><p>${scenario.description}</p><img src="${scenario.image}" alt="当前流程示例输入"><dl>${scenario.meta.map(([key,value]) => `<div><dt>${key}</dt><dd>${value}</dd></div>`).join("")}</dl>`;
  progressEl.innerHTML = workflowSteps.map((step,index) => `<button class="progress-step ${index < workflowIndex ? "done" : ""} ${index === workflowIndex ? "current" : ""}" data-step="${index}"><i>${index < workflowIndex ? "✓" : String(index + 1).padStart(2,"0")}</i><b>${step.name}</b></button>`).join("");
  const step = workflowSteps[workflowIndex];
  workflowDetail.innerHTML = `<div class="workflow-detail-top"><span class="step-label">STEP ${String(workflowIndex + 1).padStart(2,"0")} / ${workflowSteps.length}</span><span class="status-chip ${workflowIndex === workflowSteps.length - 1 ? "pass" : "live"}">${workflowIndex === workflowSteps.length - 1 ? "形成明确终态" : "处理中"}</span></div><h3>${step.title}</h3><p>${step.explain}</p><div class="io-grid"><div class="io-card"><span>接收</span><b>${step.input}</b></div><div class="io-card"><span>产出</span><b>${step.output}</b></div></div><div class="workflow-explain"><i>i</i><span>${workflowIndex === 5 ? "同一场景用 baseline 和 half_dt 两套时间步运行；只有两边都满足冻结阈值，才算物理证据通过。" : workflowIndex === 6 ? "即使中途失败，输入、停止阶段、错误与已有产物也会进入 failure bundle。" : "每一步都把输入和输出写进持久日志，断线后能够判断是继续、停止还是需要人工补资源。"}</span></div>`;
  progressEl.querySelectorAll(".progress-step").forEach(button => button.addEventListener("click", () => { stopWorkflow(); workflowIndex = Number(button.dataset.step); renderWorkflow(); workflowState.textContent = `查看第 ${workflowIndex + 1} 步`; }));
}

function stopWorkflow() { if (workflowTimer) window.clearInterval(workflowTimer); workflowTimer = null; timerWrap.classList.remove("running"); playButton.innerHTML = "<span>▶</span> 播放一次执行"; }
function playWorkflow() {
  if (workflowTimer) { stopWorkflow(); return; }
  if (workflowIndex === workflowSteps.length - 1) workflowIndex = 0;
  timerWrap.classList.add("running"); workflowState.textContent = "执行中 · 保存每步证据"; playButton.innerHTML = "<span>Ⅱ</span> 暂停"; renderWorkflow();
  workflowTimer = window.setInterval(() => {
    if (workflowIndex >= workflowSteps.length - 1) { stopWorkflow(); workflowState.textContent = "完成 · 结论分层记录"; return; }
    workflowIndex += 1; renderWorkflow();
  }, 1500);
}

document.querySelectorAll(".scenario-tabs button").forEach(button => button.addEventListener("click", () => { stopWorkflow(); workflowScenario = button.dataset.scenario; workflowIndex = 0; document.querySelectorAll(".scenario-tabs button").forEach(item => item.classList.toggle("selected", item === button)); workflowState.textContent = "等待播放"; renderWorkflow(); }));
playButton.addEventListener("click", playWorkflow);
document.querySelector("#workflowReset").addEventListener("click", () => { stopWorkflow(); workflowIndex = 0; workflowState.textContent = "等待播放"; renderWorkflow(); });
renderWorkflow();

document.querySelectorAll(".evidence-filters button").forEach(button => button.addEventListener("click", () => {
  const filter = button.dataset.filter;
  document.querySelectorAll(".evidence-filters button").forEach(item => item.classList.toggle("selected", item === button));
  document.querySelectorAll(".case-card").forEach(card => card.classList.toggle("hidden", filter !== "all" && card.dataset.status !== filter));
}));

const dialog = document.querySelector("#videoDialog");
const dialogVideo = document.querySelector("#dialogVideo");
document.querySelectorAll(".media-play").forEach(button => button.addEventListener("click", () => { dialogVideo.src = button.dataset.video; document.querySelector("#dialogTitle").textContent = button.dataset.title; dialog.showModal(); dialogVideo.play().catch(() => {}); }));
function closeDialog() { dialogVideo.pause(); dialogVideo.removeAttribute("src"); dialogVideo.load(); dialog.close(); }
dialog.querySelector(".dialog-close").addEventListener("click", closeDialog);
dialog.addEventListener("click", event => { if (event.target === dialog) closeDialog(); });

document.querySelectorAll("[data-scroll]").forEach(button => button.addEventListener("click", () => document.querySelector(button.dataset.scroll).scrollIntoView({behavior:"smooth"})));

const observer = new IntersectionObserver(entries => entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add("visible"); observer.unobserve(entry.target); } }), { threshold: .08 });
document.querySelectorAll(".reveal").forEach(element => observer.observe(element));

document.querySelectorAll(".case-media video[loop]").forEach(video => {
  const mediaObserver = new IntersectionObserver(entries => entries.forEach(entry => entry.isIntersecting ? video.play().catch(() => {}) : video.pause()), { threshold: .35 });
  mediaObserver.observe(video);
});

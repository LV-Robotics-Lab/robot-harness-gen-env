"use client";

import { useMemo, useState } from "react";

type Scenario = "exact" | "cousin" | "generate" | "replay-fail";

type WalkthroughStep = {
  id: string;
  number: string;
  skill: string;
  version: string;
  label: string;
  title: string;
  eyebrow: string;
  summary: string;
  status: "ready" | "running" | "succeeded" | "incomplete" | "blocked";
  statusLabel: string;
  details: string[];
  artifacts: string[];
  checks: { label: string; value: string; tone: "pass" | "warn" | "blocked" }[];
};

const scenarioCopy: Record<Scenario, { label: string; note: string; request: string }> = {
  exact: {
    label: "命中既有环境",
    note: "最快路径：相同 invocation digest 命中已核验的 EnvironmentPackage。",
    request: "Place a can on top of a plate.",
  },
  cousin: {
    label: "复用 digital cousin",
    note: "精确资产不合适，但一个已经验证的近亲资产满足任务相关几何与物理约束。",
    request: "Place a fluted block on top of a plate.",
  },
  generate: {
    label: "最后才生成资产",
    note: "前面的复用层都失败后，才启用受限、可追溯的 proxy generation。",
    request: "Put a hexagonal prism inside a basket.",
  },
  "replay-fail": {
    label: "回放门禁失败",
    note: "compile 可以成功；replay 发现接触/稳定性问题后，validate 不会给发布资格。",
    request: "Place a can on the edge of a plate.",
  },
};

const fallbackTiers = [
  { id: "env", label: "已有环境", short: "EnvironmentPackage", helper: "同一 digest 的完整包" },
  { id: "asset", label: "精确资产", short: "catalog grounding", helper: "语义 + 几何 + collision" },
  { id: "cousin", label: "digital cousin", short: "已验证近亲", helper: "任务相关属性可替代" },
  { id: "generate", label: "受限生成", short: "proxy + provenance", helper: "最后一层，必须可审计" },
];

function makeSteps(scenario: Scenario, progress: number): WalkthroughStep[] {
  const replayBlocked = scenario === "replay-fail" && progress >= 3;
  const selection = scenario === "exact" ? "EnvironmentPackage 复用" : scenario === "cousin" ? "digital cousin 复用" : scenario === "generate" ? "procedural proxy 生成" : "精确资产（边缘摆放）";
  return [
    {
      id: "preflight",
      number: "01",
      skill: "Registry",
      version: "preflight",
      label: "输入与身份",
      eyebrow: "先把调用说清楚",
      title: "原样保存请求，展开默认值，再计算 invocation digest",
      summary: "Registry 先验证精确 Skill 版本、参数和 Artifact 内容身份。请求文本不 trim；URI 不是身份证，sha256 才是。",
      status: progress >= 0 ? "succeeded" : "ready",
      statusLabel: "PRECHECK PASS",
      details: ["text2env.compile@1.0.0", "seed = 42 · max_attempts = 1", "request_sha256 + catalog.sha256 已锁定"],
      artifacts: ["Invocation", "request.sha256", "catalog.sha256"],
      checks: [
        { label: "Skill descriptor", value: "exact v1.0.0", tone: "pass" },
        { label: "默认值", value: "expanded", tone: "pass" },
        { label: "输入边界", value: "受限语义", tone: "pass" },
      ],
    },
    {
      id: "compile",
      number: "02",
      skill: "text2env.compile",
      version: "@1.0.0",
      label: "复用优先的编译",
      eyebrow: "只编排，不重写 scene_gen",
      title: `${selection}，然后交给既有编译链`,
      summary: "Compile handler 依次调用受限解析、asset grounding、bounded solver、builder 和静态 validator。Harness 只补稳定输入、输出、版本和审计边界。",
      status: progress >= 1 ? "succeeded" : "ready",
      statusLabel: progress >= 1 ? "SUCCEEDED" : "WAITING",
      details: ["parse → ground → solve → build → static validate", scenarioCopy[scenario].note, "所有选择、拒绝和来源都进入 route evidence"],
      artifacts: ["SceneSpec", "ResolvedSceneSpec", "EnvironmentPackage", "route_report"],
      checks: [
        { label: "RunState", value: "succeeded", tone: "pass" },
        { label: "静态验证", value: "incomplete（无 runtime）", tone: "warn" },
        { label: "编译产物", value: "hash-bound", tone: "pass" },
      ],
    },
    {
      id: "static",
      number: "03",
      skill: "text2env.compile",
      version: "@1.0.0",
      label: "静态验证",
      eyebrow: "先挡住确定性错误",
      title: "几何和包自洽，但不冒充物理证明",
      summary: "静态 validator 检查完整 footprint、目标局部 stable surface、containment、roundtrip 和 manifest。它还没有看到 SAPIEN 的真实接触。",
      status: progress >= 2 ? "incomplete" : "ready",
      statusLabel: progress >= 2 ? "INCOMPLETE" : "WAITING",
      details: ["outer AABB 不算通过；support margin 必须 target-local", "package files / canonical digest 对账", "compile succeeded ≠ publishable"],
      artifacts: ["static_validation.json", "package_manifest.json", "solver_trace"],
      checks: [
        { label: "scene schema", value: "pass", tone: "pass" },
        { label: "support / containment", value: "pass", tone: "pass" },
        { label: "runtime evidence", value: "not run", tone: "warn" },
      ],
    },
    {
      id: "replay",
      number: "04",
      skill: "text2env.replay",
      version: "@1.0.0",
      label: "物理回放",
      eyebrow: "从静态猜测到真实证据",
      title: replayBlocked ? "接触窗口发现门禁问题，run 被阻止" : "释放、沉降、采样，留下连续物理证据",
      summary: replayBlocked ? "本例的 edge placement 在回放中出现 support contact fraction 不足。Replay 不是截图检查，而是对物理规律、碰撞、稳定性和视频完整性逐项取证。" : "RoboTwin/SAPIEN 读取 resolved scene，记录初始/终态 pose、接触、穿透、可见性和 120 帧序列。",
      status: progress >= 3 ? (replayBlocked ? "blocked" : "succeeded") : "ready",
      statusLabel: progress >= 3 ? (replayBlocked ? "BLOCKED" : "SUCCEEDED") : "WAITING",
      details: replayBlocked ? ["support_contact_fraction = 0.42 < 0.80", "unexpected contact = table", "可重试一次，但不会绕过物理门禁"] : ["precheck = 0 · settle = 900 · contact window = 120", "video = 120 frames · unique frames ≥ 30", "evidence.resolved_scene_sha256 与包一致"],
      artifacts: ["runtime_evidence.json", "observer_runtime.mp4", "contact_records"],
      checks: replayBlocked ? [
        { label: "support contact", value: "0.42 / 0.80", tone: "blocked" },
        { label: "稳定性", value: "fail", tone: "blocked" },
        { label: "retry", value: "allowed once", tone: "warn" },
      ] : [
        { label: "连续帧", value: "120 / 120", tone: "pass" },
        { label: "contact window", value: "120 steps", tone: "pass" },
        { label: "hash binding", value: "match", tone: "pass" },
      ],
    },
    {
      id: "validate",
      number: "05",
      skill: "text2env.validate",
      version: "@1.0.0",
      label: "发布门禁",
      eyebrow: "只有一个 Skill 能说 publishable",
      title: replayBlocked ? "验证正常结束，但 publishable = false" : "把 compile、replay 和所有门控合成最终资格",
      summary: replayBlocked ? "Validate handler 会正常产出类型化 fail 报告；这不是 Harness 崩溃，而是物理证据明确拒绝发布。" : "Validate 核对包、证据哈希、物理/碰撞/稳定/可见性/视频门控和 qualification。全部 pass 才能发布。",
      status: progress >= 4 ? (replayBlocked ? "blocked" : "succeeded") : "ready",
      statusLabel: progress >= 4 ? (replayBlocked ? "NOT PUBLISHABLE" : "PUBLISHABLE") : "WAITING",
      details: replayBlocked ? ["validation_status = fail", "blocker: T2E_VALIDATION_FAILED", "self-improving 记住这次 edge placement 失败原因"] : ["validation_status = pass", "publishable = true", "selection + replay + validation evidence 可供未来复用"],
      artifacts: ["validation_report.json", "publishability decision", "failure memory / promotion record"],
      checks: replayBlocked ? [
        { label: "validation run", value: "succeeded", tone: "pass" },
        { label: "physical gates", value: "fail", tone: "blocked" },
        { label: "publishable", value: "false", tone: "blocked" },
      ] : [
        { label: "all gates", value: "pass", tone: "pass" },
        { label: "qualification", value: "pass", tone: "pass" },
        { label: "publishable", value: "true", tone: "pass" },
      ],
    },
  ];
}

export default function Home() {
  const [scenario, setScenario] = useState<Scenario>("exact");
  const [progress, setProgress] = useState(0);
  const [selectedStep, setSelectedStep] = useState(0);
  const [request, setRequest] = useState(scenarioCopy.exact.request);
  const steps = useMemo(() => makeSteps(scenario, progress), [scenario, progress]);
  const current = steps[selectedStep];
  const finished = progress >= 4;

  function selectScenario(next: Scenario) {
    setScenario(next);
    setRequest(scenarioCopy[next].request);
    setProgress(0);
    setSelectedStep(0);
  }

  function advance() {
    setProgress((value) => Math.min(4, value + 1));
    setSelectedStep((value) => Math.min(4, value + 1));
  }

  function reset() {
    setProgress(0);
    setSelectedStep(0);
  }

  return (
    <main>
      <header className="topbar">
        <div className="brand"><span className="brand-mark">RH</span><span>ROBOT HARNESS <b>/GEN-ENV</b></span></div>
        <nav><a href="#walkthrough">Walkthrough</a><a href="#skills">三个 Skill</a><a href="#gates">门禁</a><a href="#loop">自改进闭环</a></nav>
        <div className="top-status"><span className="live-dot" />PR2 design walkthrough</div>
      </header>

      <section className="hero shell">
        <div className="hero-copy">
          <div className="eyebrow"><span className="eyebrow-mark" />硕士研究生项目组 · 读者导览</div>
          <h1>从一句话到<br /><em>可发布环境</em>，中间发生什么？</h1>
          <p className="hero-lede">这不是“生成一张看起来对的图”。Harness 把已有的受限解析、资产 grounding、受限求解、builder、静态 validator 和真实 SAPIEN 回放接成一条可审计的 self-improving 路径。</p>
          <div className="hero-pills"><span>reuse-first</span><span>hash-bound</span><span>physics evidence</span><span>publish gate</span></div>
          <div className="hero-actions"><a className="primary-button" href="#walkthrough">开始走一遍 <span>→</span></a><a className="text-button" href="#principles">先看三条原则</a></div>
        </div>
        <div className="hero-visual" aria-label="三个 Skill 连接的流程图">
          <div className="visual-header"><span>TEXT2ENV ROUTE</span><span>seed 42</span></div>
          <div className="hero-flow">
            <div className="hero-node"><span>01</span><b>compile</b><small>typed package</small></div><i>→</i><div className="hero-node active"><span>02</span><b>replay</b><small>physical evidence</small></div><i>→</i><div className="hero-node"><span>03</span><b>validate</b><small>publishability</small></div>
          </div>
          <div className="hero-equation"><code>RunState.succeeded</code><span>≠</span><code>publishable</code></div>
          <div className="hero-caption"><span className="caption-line" />compile 可以成功，静态 validation 仍然是 <strong>incomplete</strong>；真实物理证据要等 replay。</div>
        </div>
      </section>

      <section className="stat-row shell" aria-label="关键数字"><div><strong>3</strong><span>固定版本 Skill</span><small>compile · replay · validate</small></div><div><strong>4</strong><span>复用 fallback 层</span><small>环境 → 资产 → cousin → 生成</small></div><div><strong>120</strong><span>默认 replay 帧</span><small>连续序列，不是单张截图</small></div><div><strong>1</strong><span>发布出口</span><small>只有 validate 能给资格</small></div></section>

      <section className="progress-strip shell" aria-label="当前实现进度">
        <div className="progress-heading"><span>CURRENT IMPLEMENTATION</span><strong>先分清“已经有”与“下一步要接”</strong></div>
        <div className="progress-item done"><span>✓</span><div><b>PR1 已完成</b><small>14 个严格 Harness schema、ArtifactRef、EnvironmentPackage 与状态记录</small></div></div>
        <div className="progress-item next"><span>→</span><div><b>PR2 实现蓝图</b><small>Registry、三个 handler、reuse-first route evidence 与 MCP adapter</small></div></div>
        <p>本页的互动运行是经确认的目标 walkthrough，不代表浏览器正在调用 RoboTwin/SAPIEN。</p>
      </section>

      <section className="principles shell" id="principles">
        <div className="section-intro"><div className="eyebrow">先记住这三件事</div><h2>不会混淆，才不会误改。</h2></div>
        <div className="principle-grid"><article><span className="principle-num">01</span><h3>Harness 不替代 scene_gen</h3><p>它负责稳定的输入、输出、版本、artifact 身份、状态和审计；权威解析、grounding、solver、builder、validator 仍在现有核心。</p><code>scene_gen = authority</code></article><article><span className="principle-num">02</span><h3>成功不等于通过</h3><p><code>compile RunState = succeeded</code> 只表示类型化结果产出了。没有 runtime evidence 时，静态报告保持 <code>incomplete</code> 是正确的。</p><code>status ≠ validation_status</code></article><article><span className="principle-num">03</span><h3>每次选择都要留下记忆</h3><p>命中、拒绝、fallback、回放失败、验证通过都要结构化记录，未来才能更快复用、诊断和晋升。</p><code>evidence → next decision</code></article></div>
      </section>

      <section className="walkthrough shell" id="walkthrough">
        <div className="section-intro walkthrough-intro"><div><div className="eyebrow">Interactive walkthrough · 一次走完</div><h2>输入一句话，逐步打开三项 Skill</h2><p>先选一个情景，再按“继续”推进。每一步都会显示当前状态、证据和门禁理由。</p></div><div className="run-id"><span>RUN ID</span><code>8c1a…7e42</code><small>same digest on retry</small></div></div>
        <div className="scenario-picker"><span className="picker-label">选择一个情景</span>{(Object.keys(scenarioCopy) as Scenario[]).map((key) => <button key={key} className={scenario === key ? "scenario-button selected" : "scenario-button"} onClick={() => selectScenario(key)}>{scenarioCopy[key].label}</button>)}</div>
        <div className="walkthrough-grid">
          <aside className="walk-rail">
            <div className="request-card"><label htmlFor="request">本次请求</label><textarea id="request" value={request} onChange={(event) => setRequest(event.target.value)} /><div className="request-meta"><span>seed <b>42</b></span><span>catalog <b>v1</b></span><span>route <b>text2env</b></span></div></div>
            <div className="step-list" role="tablist" aria-label="walkthrough 步骤">{steps.map((step, index) => <button key={step.id} className={`step-button ${selectedStep === index ? "current" : ""} ${index <= progress ? "visited" : ""}`} onClick={() => setSelectedStep(index)} role="tab" aria-selected={selectedStep === index}><span className="step-icon">{index < progress || (index === 4 && finished) ? "✓" : step.number}</span><span><b>{step.label}</b><small>{step.skill}{step.version}</small></span><em>{index <= progress ? step.statusLabel : "WAITING"}</em></button>)}</div>
            <div className="rail-actions"><button className="primary-button compact" onClick={advance} disabled={finished}>{finished ? "已完成这次 walkthrough" : progress === 0 ? "运行 compile →" : progress === 3 ? "运行 validate →" : "继续下一步 →"}</button><button className="reset-button" onClick={reset}>重置情景</button></div>
          </aside>
          <article className="step-panel" aria-live="polite">
            <div className="step-panel-head"><div><span className="step-kicker">{current.number} / {current.eyebrow}</span><div className="skill-tag">{current.skill}{current.version}</div></div><span className={`status-badge ${current.status}`}>{current.statusLabel}</span></div>
            <h3>{current.title}</h3><p className="step-summary">{current.summary}</p>
            <div className="detail-columns"><div><span className="label">这一步发生什么</span><ul>{current.details.map((detail) => <li key={detail}>{detail}</li>)}</ul></div><div><span className="label">留下的 artifacts</span><div className="artifact-stack">{current.artifacts.map((artifact) => <span key={artifact}>{artifact}</span>)}</div></div></div>
            <div className="check-card"><div className="check-card-title"><span>GATE SNAPSHOT</span><small>{current.id === "compile" ? "compile 与静态验证分开记" : current.id === "replay" ? "真实物理证据" : "结构化门禁"}</small></div><div className="check-grid">{current.checks.map((check) => <div className="check-row" key={check.label}><span>{check.label}</span><b className={check.tone}>{check.value}</b></div>)}</div></div>
            <div className="next-hint">{current.id === "compile" && <><strong>关键提醒：</strong>你看到的 succeeded 是“编译接口产出了类型化结果”，不是“这个环境已经通过物理发布门禁”。</>}{current.id === "replay" && <><strong>为什么必须 replay：</strong>只有连续的 contact、drift、containment、visibility 和 video 证据，才能检验“它真的稳”。</>}{current.id === "validate" && <><strong>self-improving：</strong>这次的选择和结果会进入 evidence ledger，成为下一次复用、诊断和资产晋升的输入。</>}{current.id !== "compile" && current.id !== "replay" && current.id !== "validate" && <>下一步会把类型化输入交给 <strong>text2env.compile@1.0.0</strong>，并保留同一个 digest。</>}</div>
          </article>
        </div>
      </section>

      <section className="fallback-section shell" id="skills">
        <div className="section-intro"><div className="eyebrow">Compile 的关键设计</div><h2>先复用，再 fallback；永远不要一上来生成。</h2><p>环境复用和资产复用是两层不同机制：前者找完整包，后者为当前 SceneSpec 重新做兼容性核验。</p></div>
        <div className="fallback-ladder">{fallbackTiers.map((tier, index) => { const activeIndex = scenario === "exact" ? 0 : scenario === "cousin" ? 2 : 3; const active = index === activeIndex; const skipped = index < activeIndex; return <div className={`fallback-tier ${active ? "active" : ""} ${skipped ? "skipped" : ""}`} key={tier.id}><div className="tier-left"><span className="tier-number">0{index + 1}</span><div><b>{tier.label}</b><small>{tier.short}</small></div></div><div className="tier-helper">{tier.helper}</div><div className="tier-result">{active ? <><span className="result-dot" />selected</> : skipped ? <>✓ consulted</> : <>next fallback</>}</div></div>})}</div>
        <div className="route-evidence"><div><span className="label">ROUTE EVIDENCE · envgen / harness</span><h3>每一层都回答“看过谁，为什么没选，最后选了谁”</h3></div><div className="evidence-pills"><span>tiers_consulted</span><span>candidate verdicts</span><span>rejection reasons</span><span>provenance</span><span>validation result</span></div></div>
      </section>

      <section className="skills-section shell"><div className="section-intro"><div className="eyebrow">Three fixed skills · exact versions</div><h2>三个 Skill，各自只做一件清楚的事。</h2></div><div className="skill-grid"><article className="skill-card compile-card"><div className="skill-card-top"><span className="skill-number">01</span><span className="version-pill">@1.0.0</span></div><div className="skill-name">text2env.<strong>compile</strong></div><p>复用现有核心编译链，产出 SceneSpec、ResolvedSceneSpec、EnvironmentPackage 和 static validation。</p><div className="skill-line"><span>max_attempts</span><b>1</b></div><div className="skill-line"><span>可能的 validation</span><b className="warn-text">incomplete</b></div></article><article className="skill-card replay-card"><div className="skill-card-top"><span className="skill-number">02</span><span className="version-pill">@1.0.0</span></div><div className="skill-name">text2env.<strong>replay</strong></div><p>在 RoboTwin/SAPIEN 中释放和沉降，采集连续帧、接触、穿透、稳定性和可见性证据。</p><div className="skill-line"><span>max_attempts</span><b>2</b></div><div className="skill-line"><span>可重试条件</span><b>仅 transient</b></div></article><article className="skill-card validate-card"><div className="skill-card-top"><span className="skill-number">03</span><span className="version-pill">@1.0.0</span></div><div className="skill-name">text2env.<strong>validate</strong></div><p>核对 package、runtime evidence、全部物理门控和 qualification；唯一能产生 publishable。</p><div className="skill-line"><span>max_attempts</span><b>1</b></div><div className="skill-line"><span>发布出口</span><b className="pass-text">publishable</b></div></article></div></section>

      <section className="gates-section shell" id="gates"><div className="section-intro"><div className="eyebrow">Acceptance map</div><h2>四道门，缺一不可。</h2><p>“看起来像”只属于 preview；“物理上成立”必须由 runtime evidence 证明。</p></div><div className="gates-grid"><article><span className="gate-index">A</span><h3>输入边界</h3><p>受限 prompt、严格 schema、精确版本和依赖摘要。预期拒绝是 <code>blocked</code>，不是内部崩溃。</p><span className="gate-token">HARN_INPUT_INVALID</span></article><article><span className="gate-index">B</span><h3>静态契约</h3><p>完整 footprint、target-local support margin、containment、package manifest 和 hash binding。</p><span className="gate-token">T2E_PACKAGE_INVALID</span></article><article><span className="gate-index">C</span><h3>物理回放</h3><p>support contact fraction ≥ 0.80、unexpected contact = 0、drift、可见像素和 120 帧视频。</p><span className="gate-token">T2E_REPLAY_FAILED</span></article><article><span className="gate-index">D</span><h3>发布资格</h3><p>compile、replay、validate、qualification、包与证据哈希及所有门控都通过，才是 publishable。</p><span className="gate-token">publishable = true</span></article></div></section>

      <section className="loop-section shell" id="loop"><div className="loop-copy"><div className="eyebrow">Self-improving loop</div><h2>每次运行，都是下一次选择的训练数据。</h2><p>平台层不把失败藏起来，也不把 proxy 假装成原始资产。它把选择轨迹、淘汰理由、回放证据和验证结论沉淀下来，供未来做更好的复用、诊断与晋升。</p><a className="text-button" href="../../repo-docs/modules/self-improving-platform.md">查看平台边界 ↗</a></div><div className="loop-map"><div className="loop-node"><b>选择</b><small>exact / cousin / generate</small></div><span>→</span><div className="loop-node"><b>运行</b><small>compile + replay</small></div><span>→</span><div className="loop-node"><b>验证</b><small>gates + blockers</small></div><span>→</span><div className="loop-node emphasized"><b>记忆</b><small>reuse / diagnose / promote</small></div><div className="loop-return">↺ evidence 回到下一次选择</div></div></section>

      <footer className="footer shell"><div><span className="brand-mark">RH</span><strong>Robot Harness /gen-env</strong><span>面向项目组成员的 PR2 交互导览</span></div><div><a href="../contracts/HARNESS_MVP_CONTRACT_V1.zh-CN.md">契约 ↗</a><a href="../contracts/HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md">PR1 报告 ↗</a><a href="../../repo-docs/walkthroughs/one-real-run.md">真实路径 ↗</a></div></footer>
    </main>
  );
}

const reports = [
  {
    number: "01",
    title: "SceneAgent / selection2env",
    subtitle: "RoboTwin existing-asset scene composition",
    status: "DONE · BOUNDED",
    statusClass: "status-pass",
    metric: "4/4 · 4/4 · 3/3",
    metricLabel: "held-out · randomized · cross-task",
    summary:
      "Connects natural-language task intent to asset selection, placement regions, executable task programs, rollout evidence, and promotion gates.",
    boundary:
      "The promoted policy is pose-conditioned and open-loop. Historical ACT robustness remains 1/4 and is retained as a negative parent.",
    href: "/reports/sceneagent/index.html",
    manifest: "/reports/sceneagent/hosted_subset_manifest.json",
    image:
      "/reports/harness/assets/evidence/policy_randomized_poster.png",
    imageAlt: "RoboTwin domain-randomized apple and plate rollout",
  },
  {
    number: "02",
    title: "Text2Env literature review",
    subtitle: "From paper matrix to executed empirical gates",
    status: "DONE · 5/5 GATES",
    statusClass: "status-pass",
    metric: "20 × 8",
    metricLabel: "sources × capability dimensions",
    summary:
      "A 20-source audit spanning scene synthesis, articulated assets, cross-simulator infrastructure, training transfer, materials, memory, and evaluation, including candidate-nominated projects.",
    boundary:
      "The predeclared failure-score experiment is a retained null result: r = −0.255, exact p = 0.727, n = 12.",
    href: "/reports/text2env/index.html",
    manifest: "/reports/text2env/hosted_subset_manifest.json",
    image:
      "/reports/text2env/assets/source_pages/embodiedgen_v2.png",
    imageAlt: "EmbodiedGen V2 official repository snapshot",
  },
  {
    number: "03",
    title: "PEARL Open X Sim",
    subtitle: "A command surface across simulator backends",
    status: "DONE · 8/8 ACCEPTANCE",
    statusClass: "status-pass",
    metric: "10 adapters",
    metricLabel: "2 newly audited · 120 executed Isaac steps",
    summary:
      "Defines /gen-env, /collect, /train, /evaluate, /diagnose, and /transfer with typed inputs, artifacts, ownership, and failure codes.",
    boundary:
      "Isaac executes the five non-training commands. Transfer proves task-semantic reuse, not policy, embodiment, asset, or material parity.",
    href: "/reports/openxsim/index.html",
    manifest: "/reports/openxsim/hosted_subset_manifest.json",
    image:
      "/reports/openxsim/assets/isaac_command_loop/frames/frame_00023.png",
    imageAlt: "Isaac Open X Sim placement task final frame",
  },
  {
    number: "04",
    title: "Embodied harness system",
    subtitle: "Framing self-improvement as governed execution",
    status: "DONE · 6/6 ACCEPTANCE",
    statusClass: "status-pass",
    metric: "0/3 → 3/3",
    metricLabel: "fixed-checkpoint RGB harness ablation",
    summary:
      "Turns weakness mining, memory, candidate edits, regression tests, simulator execution, and promotion decisions into explicit proof obligations.",
    boundary:
      "Historical priority is not established and real-robot evaluation was not run. Both claims remain prohibited by the specification.",
    href: "/reports/harness/index.html",
    manifest: "/reports/harness/hosted_subset_manifest.json",
    image:
      "/reports/harness/assets/evidence/policy_cross_task_poster.png",
    imageAlt: "Cross-task can and basket policy rollout",
  },
];

const metrics = [
  ["4/4", "held-out placement"],
  ["4/4", "declared randomization"],
  ["3/3", "cross-task policy"],
  ["0/3 → 3/3", "memory intervention"],
  ["24/24", "unique Isaac frames"],
  ["36/36", "local + RTX tests"],
];

const commands = [
  "/gen-env",
  "/collect",
  "/train",
  "/evaluate",
  "/diagnose",
  "/transfer",
];

export default function Home() {
  return (
    <main>
      <section className="hero" aria-labelledby="site-title">
        <video
          className="hero-media"
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          poster="/reports/harness/assets/evidence/policy_randomized_poster.png"
          aria-label="RoboTwin domain-randomized policy rollout"
        >
          <source
            src="/reports/harness/assets/evidence/policy_randomized.mp4"
            type="video/mp4"
          />
        </video>
        <div className="hero-shade" />
        <nav className="topbar" aria-label="Project navigation">
          <a className="wordmark" href="#top" aria-label="PEARL project home">
            PEARL / SELF-IMPROVING AGENTS
          </a>
          <div className="nav-links">
            <a href="#reports">Reports</a>
            <a href="#evidence">Evidence</a>
            <a href="#boundaries">Boundaries</a>
          </div>
        </nav>
        <div className="hero-copy" id="top">
          <p className="eyebrow">EMBODIED SYSTEM · AUDITABLE RESULTS · JUL 2026</p>
          <h1 id="site-title">PEARL Self-Improving Agents</h1>
          <p className="hero-lede">
            A bounded research system for composing robot environments,
            collecting execution traces, diagnosing failures, testing harness
            interventions, and promoting only verified candidates.
          </p>
          <div className="hero-actions">
            <a className="primary-action" href="#reports">
              Review all reports
            </a>
            <a
              className="secondary-action"
              href="/reports/harness/index.html"
            >
              Read the system thesis
            </a>
          </div>
        </div>
        <div className="hero-proof">
          <span className="proof-dot" aria-hidden="true" />
          <span>[COMPUTED] ALL CORE CAUSAL GATES PASS</span>
        </div>
      </section>

      <section className="metrics-band" aria-label="Verified project metrics">
        <div className="metrics-grid">
          {metrics.map(([value, label]) => (
            <div className="metric" key={label}>
              <strong>{value}</strong>
              <span>{label}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="intro-band" id="reports">
        <div className="section-heading">
          <div>
            <p className="eyebrow dark">COMPLETE PROJECT RECORD</p>
            <h2>Four workstreams. One evidence chain.</h2>
          </div>
          <p className="section-summary">
            Each hosted report preserves every image, video, and evidence file
            referenced by its HTML page. The original bundle inventory and a
            SHA-256 hosted-subset manifest remain available for audit. “Done”
            means the declared bounded acceptance passed, not that excluded
            claims were silently promoted.
          </p>
        </div>

        <div className="report-list">
          {reports.map((report) => (
            <article className="report-row" key={report.number}>
              <div className="report-index">{report.number}</div>
              <div className="report-visual">
                <img src={report.image} alt={report.imageAlt} loading="lazy" />
              </div>
              <div className="report-body">
                <div className="report-title-line">
                  <div>
                    <h3>{report.title}</h3>
                    <p>{report.subtitle}</p>
                  </div>
                  <span className={`status ${report.statusClass}`}>
                    {report.status}
                  </span>
                </div>
                <div className="report-metric">
                  <strong>{report.metric}</strong>
                  <span>{report.metricLabel}</span>
                </div>
                <p className="report-summary">{report.summary}</p>
                <p className="report-boundary">
                  <span>BOUNDARY</span> {report.boundary}
                </p>
                <div className="report-actions">
                  <a className="report-link" href={report.href}>
                    Open full report
                  </a>
                  <a className="manifest-link" href={report.manifest}>
                    Inspect hosted assets
                  </a>
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="command-band" aria-labelledby="command-title">
        <div className="section-heading inverse">
          <div>
            <p className="eyebrow">SHARED COMMAND CONTRACT</p>
            <h2 id="command-title">From scene intent to promotion decision.</h2>
          </div>
          <p className="section-summary">
            The contract separates environment generation, data collection,
            policy ownership, evaluation, diagnosis, and simulator transfer so
            every stage leaves inspectable artifacts.
          </p>
        </div>
        <div className="command-rail" role="list" aria-label="PEARL commands">
          {commands.map((command, index) => (
            <div className="command-step" role="listitem" key={command}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <code>{command}</code>
            </div>
          ))}
        </div>
      </section>

      <section className="evidence-band" id="evidence">
        <div className="section-heading">
          <div>
            <p className="eyebrow dark">EXECUTED EVIDENCE</p>
            <h2>Inspect motion, not endpoints.</h2>
          </div>
          <p className="section-summary">
            Continuous rollout videos replace the earlier two-frame endpoint
            proxies. The Isaac trace contains 120 states and actions; the
            RoboTwin policy views show held-out, randomized, and cross-task
            execution.
          </p>
        </div>

        <div className="evidence-grid">
          <figure className="evidence-feature">
            <video
              controls
              playsInline
              preload="metadata"
              poster="/reports/harness/assets/evidence/isaac_task_semantic_transfer_poster.png"
            >
              <source
                src="/reports/harness/assets/evidence/isaac_task_semantic_transfer.mp4"
                type="video/mp4"
              />
            </video>
            <figcaption>
              <div>
                <span>ISAAC · TASK-SEMANTIC TRANSFER</span>
                <strong>Five-command runtime bundle</strong>
              </div>
              <a href="/reports/openxsim/index.html">Open evidence</a>
            </figcaption>
          </figure>

          <figure className="evidence-item">
            <video
              controls
              playsInline
              preload="metadata"
              poster="/reports/harness/assets/evidence/policy_heldout_poster.png"
            >
              <source
                src="/reports/harness/assets/evidence/policy_heldout.mp4"
                type="video/mp4"
              />
            </video>
            <figcaption>
              <span>ROBOTWIN · HELD-OUT</span>
              <strong>4/4 varied placements</strong>
            </figcaption>
          </figure>

          <figure className="evidence-item">
            <video
              controls
              playsInline
              preload="metadata"
              poster="/reports/harness/assets/evidence/policy_cross_task_poster.png"
            >
              <source
                src="/reports/harness/assets/evidence/policy_cross_task.mp4"
                type="video/mp4"
              />
            </video>
            <figcaption>
              <span>ROBOTWIN · CROSS-TASK</span>
              <strong>3/3 can to basket</strong>
            </figcaption>
          </figure>
        </div>
      </section>

      <section className="boundary-band" id="boundaries">
        <div className="boundary-title">
          <p className="eyebrow">CLAIM CONTROL</p>
          <h2>What this project does not claim.</h2>
        </div>
        <div className="boundary-grid">
          <div>
            <span>01</span>
            <h3>No vision-only robustness claim</h3>
            <p>
              The promoted SceneAgent policy consumes privileged object pose;
              ACT remains a retained negative result under placement shift.
            </p>
          </div>
          <div>
            <span>02</span>
            <h3>No lossless simulator transfer</h3>
            <p>
              Isaac parity is task-semantic. Robot embodiment, policy weights,
              geometry, assets, and material systems are declared transfer
              losses.
            </p>
          </div>
          <div>
            <span>03</span>
            <h3>No priority or real-robot result</h3>
            <p>
              The harness framing is supported by bounded simulator evidence;
              historical priority and real-robot validation remain unproven.
            </p>
          </div>
        </div>
      </section>

      <footer>
        <div>
          <strong>PEARL / SELF-IMPROVING AGENTS</strong>
          <span>Evidence portal · four reports with reachable assets</span>
        </div>
        <a href="#top">Back to top</a>
      </footer>
    </main>
  );
}

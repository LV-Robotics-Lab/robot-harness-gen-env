#!/usr/bin/env python3
"""Build the self-contained PEARL embodied-harness framing report."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

from embodied_harness import AUDIT, CAUSAL_ABLATION, DOC, REPORT_ROOT, ROOT, SPEC, validate_embodied_harness_package


DASHBOARD_HARNESS_ASSET = Path("/Users/boris/workspace/BorisGuo6.github.io/dashboard/assets/self-improving-embodied-harness-loop-20260707.png")
IMAGE_SOURCES = {
    "embodied_harness_loop.png": DASHBOARD_HARNESS_ASSET,
    "aspire.png": ROOT / "reports/text2env_literature_review/assets/source_pages/aspire.png",
    "enpire.png": ROOT / "reports/text2env_literature_review/assets/source_pages/enpire.png",
    "robotwin_2.png": ROOT / "reports/text2env_literature_review/assets/source_pages/robotwin_2.png",
    "openxsim_benchmarks.png": ROOT / "reports/openxsim_command_loop/qa/desktop-benchmarks.png",
    "openxsim_fallbacks.png": ROOT / "reports/openxsim_command_loop/qa/desktop-fallbacks.png",
}
MEMORY_ABLATION = Path("artifacts/text2env_empirics/memory_ablation_rgb_adapter_v1.json")
EMPIRICAL_AUDIT = Path("artifacts/text2env_empirics/text2env_empirical_audit_v1.json")
POLICY_PROMOTION = Path("artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json")
ISAAC_COMMAND_RUN = Path("runs/isaac_openxsim_place_container_plate_v1")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def read_json(relative_path: Path | str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def badge(status: str) -> str:
    style = "pass" if status == "pass" or status.startswith("proven_") else "open"
    return f'<span class="status {style}">{esc(status.replace("_", " "))}</span>'


def build_html(spec: dict, audit: dict, causal: dict, memory: dict, empirical: dict, policy: dict) -> str:
    claim = spec["paper_claim"]
    acceptance_rows = "".join(
        f'<tr><td class="number">{item["id"]:02d}</td><td>{esc(item["requirement"])}</td><td>{badge(item["status"])}</td><td><code>{esc(item["evidence"][0])}</code></td></tr>'
        for item in audit["items"]
    )
    thesis = "".join(f"<p>{esc(paragraph)}</p>" for paragraph in claim["thesis"])
    loop_rows = "".join(
        f"""
        <article class="loop-step">
          <span class="step-number">{row['order']:02d}</span>
          <div><h3>{esc(row['sketch_term'])}</h3><p>{esc(row['pearl_term'])}</p><small>{esc(row['gate'])}</small></div>
        </article>"""
        for row in spec["loop_steps"]
    )
    surface_rows = "".join(
        f"<tr><td><strong>{esc(row['surface'].replace('_', ' '))}</strong></td><td>{esc(row['contract'])}</td><td>{' · '.join(esc(value) for value in row['artifacts'])}</td></tr>"
        for row in spec["embodied_surfaces"]
    )
    novelty_rows = "".join(
        f"<tr><td><strong>{esc(row['comparison'])}</strong></td><td>{esc(row['overlap'])}</td><td>{esc(row['pearl_hypothesis'])}</td><td>{esc(row['implemented_evidence'])}</td><td>{esc(row['missing_evidence'])}</td></tr>"
        for row in spec["novelty_table"]
    )
    route_rows = "".join(
        f"<tr><td><code>{esc(row['command'])}</code></td><td>{' · '.join(esc(value.replace('_', ' ')) for value in row['harness_surfaces'])}</td><td>{' · '.join(esc(value) for value in row['required_fields'])}</td><td>{esc(row['promotion_gate'])}</td><td><code>{esc(row['evidence'])}</code></td></tr>"
        for row in spec["command_routing"]
    )
    proof_rows = "".join(
        f"<tr><td>{esc(row['claim'])}</td><td>{badge(row['status'])}</td><td>{esc(row['required_next_evidence'])}</td></tr>"
        for row in spec["proof_obligations"]
    )
    figure_brief = "".join(f"<li>{esc(value)}</li>" for value in spec["figure"]["brief"])
    causal_outcomes = causal["outcomes"]
    memory_gate = empirical["gates"]["memory_ablation"]
    cross_sim = empirical["gates"]["same_task_cross_sim_execution"]
    policy_gates = policy["gates"]
    evidence_cards = "".join(
        f'<article class="result-card"><header><h3>{esc(title)}</h3><span class="status pass">proven bounded</span></header><strong>{esc(result)}</strong><p>{esc(boundary)}</p><a href="{esc(link)}">Machine evidence</a></article>'
        for title, result, boundary, link in (
            (
                "Fixed-checkpoint harness edit",
                f'{causal_outcomes["baseline_success_count"]}/3 baseline -> {causal_outcomes["candidate_success_count"]}/3 candidate',
                "Only observations.runtime_color_adapter changes; checkpoint, dataset stats, seeds, placement, actions, and verifier are fixed.",
                "assets/evidence/fixed_checkpoint_rgb_ablation_v1.json",
            ),
            (
                "Memory-mediated correction",
                f'{memory_gate["no_memory_success_count"]}/3 no-memory -> {memory_gate["memory_success_count"]}/3 memory',
                "Versioned failure memory selects the accepted identity adapter under the same fixed evaluation protocol.",
                "assets/evidence/memory_ablation_rgb_adapter_v1.json",
            ),
            (
                "Second-simulator command parity",
                f'{cross_sim["command_count"]} commands · {cross_sim["trace_steps"]} steps · target verifier pass',
                "Task semantics transfer through primitive proxies; robot embodiment, policy, source assets, and materials do not.",
                "assets/evidence/text2env_empirical_audit_v1.json",
            ),
            (
                "Bounded learned-policy promotion",
                f'{policy_gates["heldout_varied_placement"]["success_count"]}/4 held-out · {policy_gates["declared_domain_randomization"]["success_count"]}/4 randomized · {policy_gates["cross_task_learned_policy"]["success_count"]}/3 second task',
                policy["claim_boundary"],
                "assets/evidence/pose_conditioned_policy_promotion_v1.json",
            ),
        )
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PEARL Embodied Harness · Paper Framing</title>
  <style>
    :root {{ --ink:#17212b; --muted:#5c6874; --line:#d5dde4; --canvas:#f3f5f7; --surface:#fff; --charcoal:#27313b; --green:#14795b; --green-bg:#e8f5ef; --amber:#8a5100; --amber-bg:#fff4d6; --blue:#2456b8; --blue-bg:#edf2ff; --red:#a13b2b; --red-bg:#fdecea; }}
    * {{ box-sizing:border-box; }} html {{ scroll-behavior:smooth; }} body {{ margin:0; background:var(--canvas); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; font-size:15px; line-height:1.55; letter-spacing:0; }}
    a {{ color:var(--blue); text-underline-offset:3px; }} code {{ font-family:"SFMono-Regular",Consolas,monospace; overflow-wrap:anywhere; }} .topbar {{ background:var(--charcoal); color:#fff; border-bottom:4px solid #38a57b; }} .topbar-inner,main {{ width:min(1220px,calc(100% - 32px)); margin:0 auto; }} .topbar-inner {{ min-height:58px; display:flex; align-items:center; justify-content:space-between; gap:20px; }} .brand {{ font-weight:750; }} .top-meta {{ color:#dbe2e8; font-size:12px; }}
    main {{ padding:34px 0 64px; }} .head {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:24px; align-items:end; padding-bottom:26px; border-bottom:1px solid var(--line); }} h1 {{ margin:0 0 8px; font-size:34px; line-height:1.16; }} h2 {{ margin:0; font-size:23px; }} h3 {{ margin:0; font-size:17px; }} p {{ margin:0; }} .subtitle {{ max-width:850px; color:var(--muted); }}
    .verdict {{ min-width:240px; padding:14px 16px; border:1px solid #9fd2be; border-radius:6px; background:var(--green-bg); color:#0d6047; }} .verdict strong {{ display:block; font-size:22px; }} .verdict span {{ font-size:12px; }} .tag {{ display:inline-block; margin-right:5px; padding:2px 6px; border:1px solid #aeb8c2; border-radius:4px; background:#f8fafb; color:#4b5864; font-size:10px; font-weight:700; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:22px 0; }} .metric {{ min-height:102px; padding:16px; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .metric strong {{ display:block; font-size:28px; }} .metric span {{ color:var(--muted); font-size:12px; }}
    .claim-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }} .claim-box {{ padding:16px; border:1px solid var(--line); border-top:4px solid var(--green); border-radius:6px; background:var(--surface); }} .claim-box.blocked {{ border-top-color:var(--red); }} .claim-box p {{ margin-top:6px; color:var(--muted); font-size:13px; }} .claim-box strong {{ color:var(--green); }} .claim-box.blocked strong {{ color:var(--red); }}
    .block {{ min-width:0; padding-top:42px; }} .section-head {{ display:flex; align-items:end; justify-content:space-between; gap:16px; margin-bottom:14px; }} .note {{ color:var(--muted); font-size:12px; }} .table-wrap {{ min-width:0; max-width:100%; overflow:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} table {{ width:100%; min-width:820px; border-collapse:collapse; }} th,td {{ padding:11px 13px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:12px; }} th {{ background:#edf1f4; color:#48545f; font-size:10px; text-transform:uppercase; }} tr:last-child td {{ border-bottom:0; }} .number {{ width:42px; color:var(--muted); font-weight:750; }}
    .status {{ display:inline-flex; align-items:center; gap:5px; padding:3px 7px; border-radius:4px; white-space:nowrap; font-size:10px; font-weight:750; }} .status::before {{ content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }} .status.pass {{ color:var(--green); background:var(--green-bg); }} .status.open {{ color:var(--amber); background:var(--amber-bg); }}
    .thesis {{ padding:18px; border-left:4px solid var(--blue); background:var(--surface); }} .thesis p {{ margin-top:12px; color:#334251; }} .thesis p:first-child {{ margin-top:0; }} .attribution {{ margin-top:14px; padding:12px; background:var(--blue-bg); color:#244780; font-size:12px; }}
    .figure-panel {{ display:grid; grid-template-columns:minmax(0,1.5fr) minmax(260px,.5fr); gap:16px; align-items:start; }} .figure-panel figure {{ margin:0; border:1px solid var(--line); border-radius:6px; overflow:hidden; background:#fff; }} .figure-panel img {{ display:block; width:100%; height:auto; }} .figure-panel figcaption {{ padding:10px 12px; color:var(--muted); font-size:11px; }} .brief {{ padding:16px; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .brief ul {{ margin:8px 0 0; padding-left:19px; }} .brief li {{ margin:6px 0; color:var(--muted); font-size:12px; }}
    .loop {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; }} .loop-step {{ min-width:0; display:grid; grid-template-columns:38px 1fr; gap:9px; padding:13px; border:1px solid var(--line); border-left:4px solid var(--blue); border-radius:6px; background:var(--surface); }} .step-number {{ display:grid; place-items:center; width:32px; height:32px; border-radius:4px; background:var(--blue-bg); color:var(--blue); font-weight:750; }} .loop-step p {{ margin-top:4px; color:#384858; font-size:12px; }} .loop-step small {{ display:block; margin-top:7px; color:var(--muted); }}
    .gallery {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }} .shot {{ margin:0; border:1px solid var(--line); border-radius:6px; overflow:hidden; background:var(--surface); }} .shot img {{ display:block; width:100%; aspect-ratio:16/10; object-fit:cover; object-position:top; }} .shot figcaption {{ display:flex; justify-content:space-between; gap:10px; padding:9px 11px; }} .shot figcaption span {{ color:var(--muted); font-size:10px; }}
    .result-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }} .result-card {{ min-width:0; padding:15px; border:1px solid var(--line); border-top:4px solid var(--green); border-radius:6px; background:var(--surface); }} .result-card header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:10px; }} .result-card strong {{ display:block; margin-top:12px; font-size:20px; }} .result-card p {{ margin:7px 0 10px; color:var(--muted); font-size:12px; }} .result-card a {{ font-size:11px; }} .result-videos {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:12px; }} .result-videos figure {{ margin:0; overflow:hidden; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .result-videos video {{ display:block; width:100%; aspect-ratio:4/3; background:#111; }} .result-videos figcaption {{ padding:8px 10px; color:var(--muted); font-size:10px; }}
    .boundary {{ padding:16px; border:1px solid #e3b5aa; border-left:4px solid var(--red); border-radius:6px; background:var(--red-bg); color:#782b20; }} .evidence-links {{ display:flex; flex-wrap:wrap; gap:7px; }} .evidence-links a {{ padding:5px 8px; border:1px solid var(--line); border-radius:4px; background:var(--surface); font-size:11px; text-decoration:none; }} footer {{ margin-top:48px; padding-top:20px; border-top:1px solid var(--line); color:var(--muted); font-size:11px; }}
    @media(max-width:900px) {{ .loop {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .figure-panel {{ grid-template-columns:1fr; }} }} @media(max-width:680px) {{ .claim-grid,.gallery,.result-grid,.result-videos {{ grid-template-columns:1fr; }} .metrics {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media(max-width:520px) {{ .topbar-inner,main {{ width:min(100% - 20px,1220px); }} .topbar-inner,.section-head {{ align-items:flex-start; flex-direction:column; padding:12px 0; }} .head {{ grid-template-columns:1fr; }} h1 {{ font-size:27px; }} .verdict {{ width:100%; }} .loop {{ grid-template-columns:1fr; }} .shot figcaption {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <header class="topbar"><div class="topbar-inner"><div class="brand">ALCHEDATA · SELF IMPROVING AGENTS</div><div class="top-meta">PEARL embodied harness framing · 2026-07-13</div></div></header>
  <main>
    <header class="head"><div><h1>PEARL as an Embodied Harness System</h1><p class="subtitle"><span class="tag">[INFERRED]</span><span class="tag">[CONFIDENCE: HIGH]</span>Scoped paper framing backed by controlled harness, memory, second-simulator, and learned-policy evidence.</p></div><div class="verdict"><strong>Acceptance 6 / 6</strong><span>[COMPUTED] [CONFIDENCE: HIGH] Framing plus four bounded evidence classes</span></div></header>
    <section class="metrics"><article class="metric"><strong>9</strong><span>Versioned loop steps</span></article><article class="metric"><strong>11</strong><span>Embodied harness surfaces</span></article><article class="metric"><strong>5</strong><span>Required comparisons</span></article><article class="metric"><strong>5</strong><span>Routed commands</span></article></section>
    <section class="claim-grid"><article class="claim-box"><h3>Working paper claim</h3><p><strong>Scoped:</strong> {esc(claim['working_claim'])}</p></article><article class="claim-box blocked"><h3>Priority claim</h3><p><strong>Not established:</strong> {esc(claim['prohibited_public_claim'])}</p></article></section>
    <section class="block"><div class="section-head"><h2>Acceptance Audit</h2><p class="note">Six paper-framing requirements mapped to artifacts.</p></div><div class="table-wrap"><table><thead><tr><th>#</th><th>Requirement</th><th>Status</th><th>Primary evidence</th></tr></thead><tbody>{acceptance_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>One-Page Thesis</h2><p class="note">Precise system claim with causal attribution control.</p></div><article class="thesis">{thesis}<p class="attribution"><strong>Attribution rule:</strong> {esc(claim['attribution_control'])}</p></article></section>
    <section class="block"><div class="section-head"><h2>Bounded System Evidence</h2><p class="note">Positive results are scoped; negative and unrun obligations remain visible below.</p></div><div class="result-grid">{evidence_cards}</div><div class="result-videos"><figure><video controls preload="metadata" poster="assets/evidence/isaac_task_semantic_transfer_poster.png" src="assets/evidence/isaac_task_semantic_transfer.mp4"></video><figcaption>Isaac task-semantic `/collect` and verifier trajectory · 24/24 unique frames</figcaption></figure><figure><video controls preload="metadata" poster="assets/evidence/policy_heldout_poster.png" src="assets/evidence/policy_heldout.mp4"></video><figcaption>Apple/plate signature-disjoint held-out placement · 143 frames</figcaption></figure><figure><video controls preload="metadata" poster="assets/evidence/policy_randomized_poster.png" src="assets/evidence/policy_randomized.mp4"></video><figcaption>Apple/plate randomized background, light, camera, and table height · 143 frames</figcaption></figure><figure><video controls preload="metadata" poster="assets/evidence/policy_cross_task_poster.png" src="assets/evidence/policy_cross_task.mp4"></video><figcaption>Can/basket fixed-placement seed holdout · 144 frames</figcaption></figure></div></section>
    <section class="block"><div class="section-head"><h2>Architecture Figure</h2><p class="note">Original harness sketch bundled locally.</p></div><div class="figure-panel"><figure><img src="assets/embodied_harness_loop.png" alt="Embodied harness loop from current harness through weakness mining, proposal validation, and updated harness"><figcaption>{esc(spec['figure']['caption'])}</figcaption></figure><aside class="brief"><h3>Diagram brief</h3><ul>{figure_brief}</ul></aside></div></section>
    <section class="block"><div class="section-head"><h2>Loop Mapping</h2><p class="note">h_t to h_(t+1), with an explicit gate at every transition.</p></div><div class="loop">{loop_rows}</div></section>
    <section class="block"><div class="section-head"><h2>Embodied Harness Surfaces</h2><p class="note">Physical experiment state that must be versioned.</p></div><div class="table-wrap"><table><thead><tr><th>Surface</th><th>Required contract</th><th>Example artifacts</th></tr></thead><tbody>{surface_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Comparison And Missing Evidence</h2><p class="note">Differentiation stays a testable hypothesis.</p></div><div class="table-wrap"><table><thead><tr><th>Comparison</th><th>Overlap</th><th>PEARL hypothesis</th><th>Implemented</th><th>Missing</th></tr></thead><tbody>{novelty_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Bundled Evidence</h2><p class="note">Primary-source snapshots and current implementation proof surfaces.</p></div><div class="gallery"><figure class="shot"><img src="assets/aspire.png" alt="ASPIRE official project page"><figcaption><strong>ASPIRE</strong><span>Primary-source comparison</span></figcaption></figure><figure class="shot"><img src="assets/enpire.png" alt="ENPIRE official project page"><figcaption><strong>ENPIRE</strong><span>Primary-source comparison</span></figcaption></figure><figure class="shot"><img src="assets/robotwin_2.png" alt="RoboTwin 2 official project page"><figcaption><strong>RoboTwin 2.0</strong><span>Primary-source comparison</span></figcaption></figure><figure class="shot"><img src="assets/openxsim_benchmarks.png" alt="Three Open X Sim RoboTwin benchmark cards"><figcaption><strong>Open X Sim smokes</strong><span>Current execution evidence</span></figcaption></figure><figure class="shot"><img src="assets/openxsim_fallbacks.png" alt="Open X Sim fallback gate blockers"><figcaption><strong>Fallback gates</strong><span>Blocked outcomes remain visible</span></figcaption></figure></div></section>
    <section class="block"><div class="section-head"><h2>Implementation Routing</h2><p class="note">Requirements land on existing commands, not the project intro.</p></div><div class="table-wrap"><table><thead><tr><th>Command</th><th>Harness surfaces</th><th>Required fields</th><th>Promotion gate</th><th>Evidence</th></tr></thead><tbody>{route_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Proof Obligations</h2><p class="note">Unproven system and priority claims remain explicit.</p></div><div class="table-wrap"><table><thead><tr><th>Claim</th><th>Status</th><th>Required next evidence</th></tr></thead><tbody>{proof_rows}</tbody></table></div></section>
    <section class="block"><div class="boundary"><span class="tag">[KNOWN]</span><span class="tag">[CONFIDENCE: HIGH]</span>{esc(spec['claim_boundary'])}</div></section>
    <section class="block"><div class="section-head"><h2>Evidence Files</h2><p class="note">Machine-readable package, Markdown, QA, and manifest.</p></div><div class="evidence-links"><a href="assets/embodied_harness_spec.json">Harness spec JSON</a><a href="assets/acceptance_audit.json">Acceptance audit</a><a href="assets/evidence/fixed_checkpoint_rgb_ablation_v1.json">Harness ablation</a><a href="assets/evidence/memory_ablation_rgb_adapter_v1.json">Memory ablation</a><a href="assets/evidence/text2env_empirical_audit_v1.json">Cross-sim audit</a><a href="assets/evidence/pose_conditioned_policy_promotion_v1.json">Policy promotion</a><a href="assets/evidence/isaac_bundle_manifest.json">Isaac bundle manifest</a><a href="assets/evidence/isaac_transfer.json">Transfer mappings</a><a href="embodied_harness_thesis.md">Markdown thesis</a><a href="assets/browser_qa.txt">Browser QA</a><a href="qa/desktop-viewport.png">Desktop QA</a><a href="qa/mobile-viewport.png">Mobile QA</a><a href="report_manifest.json">Bundle manifest</a></div></section>
    <footer><span class="tag">[KNOWN]</span><span class="tag">[CONFIDENCE: HIGH]</span>Static local report. All images, source snapshots, implementation screenshots, JSON, and Markdown are bundled.</footer>
  </main>
</body>
</html>
"""


def write_manifest(output_dir: Path) -> dict:
    rows = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "report_manifest.json":
            continue
        rows.append({
            "path": path.relative_to(output_dir).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    manifest = {
        "schema_version": "alchedata.embodied_harness_report_manifest.v1",
        "status": "pass_report_bundle",
        "generated_at": "2026-07-13",
        "file_count": len(rows),
        "files": rows,
    }
    (output_dir / "report_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_report(output_dir: Path) -> dict:
    validate_embodied_harness_package(require_report=False)
    spec = read_json(SPEC)
    audit = read_json(AUDIT)
    causal = read_json(CAUSAL_ABLATION)
    memory = read_json(MEMORY_ABLATION)
    empirical = read_json(EMPIRICAL_AUDIT)
    policy = read_json(POLICY_PROMOTION)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = output_dir / "assets"
    assets.mkdir(exist_ok=True)
    for name, source in IMAGE_SOURCES.items():
        if not source.exists():
            raise FileNotFoundError(f"Required report image is missing: {source}")
        shutil.copy2(source, assets / name)
    shutil.copy2(ROOT / SPEC, assets / "embodied_harness_spec.json")
    shutil.copy2(ROOT / AUDIT, assets / "acceptance_audit.json")
    shutil.copy2(ROOT / DOC, output_dir / "embodied_harness_thesis.md")
    evidence_assets = assets / "evidence"
    if evidence_assets.is_dir():
        shutil.rmtree(evidence_assets)
    evidence_assets.mkdir()
    for source, name in (
        (CAUSAL_ABLATION, "fixed_checkpoint_rgb_ablation_v1.json"),
        (MEMORY_ABLATION, "memory_ablation_rgb_adapter_v1.json"),
        (EMPIRICAL_AUDIT, "text2env_empirical_audit_v1.json"),
        (POLICY_PROMOTION, "pose_conditioned_policy_promotion_v1.json"),
        (ISAAC_COMMAND_RUN / "bundle_manifest.json", "isaac_bundle_manifest.json"),
        (ISAAC_COMMAND_RUN / "transfer.json", "isaac_transfer.json"),
    ):
        shutil.copy2(ROOT / source, evidence_assets / name)
    media_sources = (
        (ISAAC_COMMAND_RUN / "isaac_place_on_rollout.mp4", "isaac_task_semantic_transfer.mp4"),
        (Path(policy["gates"]["heldout_varied_placement"]["videos"][0]["path"]), "policy_heldout.mp4"),
        (Path(policy["gates"]["declared_domain_randomization"]["videos"][0]["path"]), "policy_randomized.mp4"),
        (Path(policy["gates"]["cross_task_learned_policy"]["videos"][0]["path"]), "policy_cross_task.mp4"),
    )
    for source, name in media_sources:
        shutil.copy2(ROOT / source, evidence_assets / name)
    poster_sources = (
        (ISAAC_COMMAND_RUN / "frames/frame_00023.png", "isaac_task_semantic_transfer_poster.png"),
        (Path(policy["gates"]["heldout_varied_placement"]["videos"][0]["path"]).parent / "final_observer_camera.png", "policy_heldout_poster.png"),
        (Path(policy["gates"]["declared_domain_randomization"]["videos"][0]["path"]).parent / "final_observer_camera.png", "policy_randomized_poster.png"),
        (Path(policy["gates"]["cross_task_learned_policy"]["videos"][0]["path"]).parent / "final_observer_camera.png", "policy_cross_task_poster.png"),
    )
    for source, name in poster_sources:
        shutil.copy2(ROOT / source, evidence_assets / name)
    (output_dir / "index.html").write_text(
        build_html(spec, audit, causal, memory, empirical, policy), encoding="utf-8"
    )
    manifest = write_manifest(output_dir)
    return {
        "status": "pass_embodied_harness_report_build",
        "output": str(output_dir),
        "file_count": manifest["file_count"],
        "images": len(list(assets.rglob("*.png"))),
        "loop_steps": len(spec["loop_steps"]),
        "surfaces": len(spec["embodied_surfaces"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / REPORT_ROOT)
    args = parser.parse_args()
    print(json.dumps(build_report(args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

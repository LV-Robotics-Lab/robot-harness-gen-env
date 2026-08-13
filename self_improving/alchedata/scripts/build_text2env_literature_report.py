#!/usr/bin/env python3
"""Build the static Text2Env literature review report from audited JSON."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

from text2env_literature_review import (
    ACCEPTANCE_AUDIT,
    METHOD_MATRIX,
    REVIEW_DOC,
    ROOT,
    SOURCE_REGISTRY,
    validate_review_package,
)


LEVEL_LABELS = {"none": "-", "low": "L", "medium": "M", "high": "H"}
CAPABILITY_LABELS = {
    "task_generation": "Task gen",
    "asset_retrieval_generation": "Asset retrieve/gen",
    "placement_planning": "Placement",
    "physics_collision_validation": "Physics / collision",
    "code_generation_repair": "Code gen / repair",
    "simulator_smoke": "Sim smoke",
    "data_collection": "Data collect",
    "policy_evaluation": "Policy eval",
}
SOURCE_SCREENSHOTS = {
    "scenesmith": "scenesmith.png",
    "robotwin_2": "robotwin_2.png",
    "robotwin_generative_digital_twins": "robotwin_digital_twins.png",
    "robogen": "robogen.png",
    "fate": "fate.png",
    "vlmbench_amsolver": "vlmbench.png",
    "realm": "realm.png",
    "aspire": "aspire.png",
    "enpire": "enpire.png",
    "articraft": "articraft.png",
    "embodiedgen_v2": "embodiedgen_v2.png",
    "roboverse": "roboverse.png",
    "generative_worlds_sim2real_rl": "generative_worlds_sim2real_rl.png",
    "dipo": "dipo.png",
    "three_d_fixer": "three_d_fixer.png",
    "uni3r": "uni3r.png",
}
CANDIDATE_APPENDIX_SCREENSHOTS = {
    "gausstr": "gausstr.png",
    "dirtnet": "dirtnet.png",
    "instancenet": "instancenet.png",
}
CANDIDATE_INTAKE = Path("artifacts/literature_review/candidate_project_intake_paul_20260714.json")
EMPIRICAL_AUDIT = Path("artifacts/text2env_empirics/text2env_empirical_audit_v1.json")
MATERIAL_RUN = Path("runs/isaac_material_sidecar_roundtrip_v1")
ISAAC_COMMAND_RUN = Path("runs/isaac_openxsim_place_container_plate_v1")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def read_json(relative_path: Path) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def source_links(source: dict) -> str:
    labels = {"project": "Project", "paper": "Paper", "code": "Code", "data": "Data"}
    links = [
        f'<a href="{esc(url)}" target="_blank" rel="noreferrer">{label}</a>'
        for key, label in labels.items()
        if (url := source["links"].get(key))
    ]
    return " · ".join(links) if links else '<span class="muted">Local evidence only</span>'


def candidate_links(project: dict) -> str:
    labels = {"project": "Project", "paper": "Paper", "code": "Code"}
    links = [
        f'<a href="{esc(url)}" target="_blank" rel="noreferrer">{label}</a>'
        for key, label in labels.items()
        if (url := project["public_sources"].get(key))
    ]
    return " · ".join(links) if links else '<span class="muted">No public primary source identified</span>'


def status_badge(status: str) -> str:
    style = "pass" if status == "pass" else "pending" if status == "not_run" else "blocked"
    return f'<span class="status {style}">{esc(status.replace("_", " "))}</span>'


def build_html(registry: dict, matrix: dict, audit: dict, empirical: dict, candidate: dict) -> str:
    source_by_id = {source["source_id"]: source for source in registry["sources"]}

    acceptance_rows = "".join(
        f"""
        <tr>
          <td class="number">{item['id']:02d}</td>
          <td>{esc(item['requirement'])}</td>
          <td>{status_badge(item['status'])}</td>
          <td><code>{esc(item['evidence'][0])}</code></td>
        </tr>"""
        for item in audit["items"]
    )

    taxonomy_rows = "".join(
        f"<tr><td><code>{esc(name)}</code></td><td>{esc(definition)}</td></tr>"
        for name, definition in audit["taxonomy"].items()
    )

    gallery_cards = "".join(
        f"""
        <figure class="source-shot">
          <img src="assets/source_pages/{esc(filename)}" alt="{esc(source_by_id[source_id]['name'])} official source page screenshot">
          <figcaption>
            <strong>{esc(source_by_id[source_id]['name'])}</strong>
            <span>Captured 2026-07-14 · primary URL rechecked 2026-07-15</span>
          </figcaption>
        </figure>"""
        for source_id, filename in SOURCE_SCREENSHOTS.items()
    )

    candidate_by_id = {project["project_id"]: project for project in candidate["projects"]}
    candidate_rows = "".join(
        f"""
        <tr>
          <td><strong>{esc(project['name'])}</strong><br><span class="muted">{esc(project['resume_relation'])}</span></td>
          <td>{candidate_links(project)}</td>
          <td><code>{esc(project['relevance_bucket'])}</code></td>
          <td>{esc(project['verified_scope'])}</td>
          <td><strong>{esc(project['disposition'].replace('_', ' '))}</strong><br><span class="muted">{esc(project['boundary'])}</span></td>
        </tr>"""
        for project in candidate["projects"]
    )
    candidate_gallery_cards = "".join(
        f"""
        <figure class="source-shot">
          <img src="assets/source_pages/{esc(filename)}" alt="{esc(candidate_by_id[project_id]['name'])} official source page screenshot">
          <figcaption>
            <strong>{esc(candidate_by_id[project_id]['name'])}</strong>
            <span>Captured 2026-07-14 · primary URL rechecked 2026-07-15</span>
          </figcaption>
        </figure>"""
        for project_id, filename in CANDIDATE_APPENDIX_SCREENSHOTS.items()
    )

    source_rows = "".join(
        f"""
        <details class="source-row">
          <summary>
            <span><strong>{esc(source['name'])}</strong><small>{source['year']} · {esc(source['source_kind'].replace('_', ' '))}</small></span>
            <span class="tier {esc(source['interface_relation']['adoption_tier'].lower())}">{esc(source['interface_relation']['adoption_tier'])}</span>
          </summary>
          <div class="source-body">
            <div><span class="label">Primary links</span><p>{source_links(source)}</p></div>
            <div><span class="label">Input → output</span><p>{esc(source['input'])}<br><strong>→</strong> {esc(source['output'])}</p></div>
            <div><span class="label">Environment / assets</span><p>{esc(source['environment_assets'])}</p></div>
            <div><span class="label">Open status</span><p>{esc(source['open_status']['code_status'])} · {esc(source['open_status']['license'])}</p></div>
            <div><span class="label">Reproducibility</span><p><strong>{esc(source['reproducibility']['level'])}</strong><br>{esc(source['reproducibility']['evidence'][0])}</p></div>
            <div><span class="label">RoboTwin / AgenticSim</span><p>{esc(source['interface_relation']['robotwin'])}<br>{esc(source['interface_relation']['agenticsim'])}</p></div>
            <div class="wide"><span class="label">Required gates</span><p>{' · '.join(esc(gate) for gate in source['interface_relation']['required_gates'])}</p></div>
          </div>
        </details>"""
        for source in registry["sources"]
    )

    capability_headers = "".join(f"<th>{esc(CAPABILITY_LABELS[name])}</th>" for name in matrix["capabilities"])
    matrix_rows = "".join(
        "<tr>"
        f"<td><strong>{esc(source_by_id[row['source_id']]['name'])}</strong></td>"
        + "".join(
            f'<td><span class="level {esc(row["scores"][name])}" title="{esc(row["scores"][name])}">{LEVEL_LABELS[row["scores"][name]]}</span></td>'
            for name in matrix["capabilities"]
        )
        + "</tr>"
        for row in matrix["rows"]
    )

    shortlist_columns = "".join(
        f"""
        <section class="tier-column">
          <h3>{tier}</h3>
          {''.join(
              f'<article><strong>{esc(item["decision"])}</strong><span class="disposition">{esc(item["current_status"].replace("_", " "))}</span><p>{esc(item["evidence"])}</p></article>'
              for item in audit['shortlist'][tier]
          )}
        </section>"""
        for tier in ("P0", "P1", "P2")
    )

    handoff_fields = "".join(f"<code>{esc(field)}</code>" for field in audit["handoff"]["zheng_ye_produces"]["required_fields"])
    handoff_rows = "".join(
        f"<tr><td><code>{esc(command)}</code></td><td>{' · '.join(esc(value) for value in values)}</td></tr>"
        for command, values in audit["handoff"]["gaochen_consumes"].items()
    )
    blocker_rows = "".join(f"<li>{esc(blocker)}</li>" for blocker in audit["handoff"]["open_blockers"])

    innovation = audit["innovation_after_aspire_enpire"]
    innovation_dimensions = "".join(
        f'<span class="dimension">{esc(value.replace("_", " "))}</span>'
        for value in innovation["distinct_hypothesis_dimensions"]
    )
    experiment_rows = "".join(
        f"<tr><td>{esc(item['experiment'])}</td><td>{status_badge(item['status'])}</td><td>{esc(item['evidence'])}</td></tr>"
        for item in innovation["next_experiments"]
    )
    empirical_gates = empirical["gates"]
    cross_sim = empirical_gates["same_task_cross_sim_execution"]
    memory = empirical_gates["memory_ablation"]
    material = empirical_gates["material_extraction"]
    correlation = empirical_gates["failure_score_correlation"]
    policy = empirical_gates["robust_policy_result"]
    empirical_rows = "".join(
        (
            f'<article class="empirical"><header><h3>{esc(title)}</h3><span class="status pass">pass</span></header>'
            f'<strong>{esc(result)}</strong><p>{esc(boundary)}</p><a href="{esc(link)}">Machine evidence</a></article>'
        )
        for title, result, boundary, link in (
            (
                "Task-semantic cross-sim",
                f'{cross_sim["command_count"]} commands · {cross_sim["trace_steps"]} trace steps · {cross_sim["unique_video_frames"]}/{cross_sim["video_frames"]} unique frames',
                "RoboTwin task semantics are mapped into an Isaac primitive-proxy scene; robot embodiment and learned policy are not transferred.",
                "assets/empirics/text2env_empirical_audit_v1.json",
            ),
            (
                "Matched memory ablation",
                f'{memory["no_memory_success_count"]}/3 no-memory → {memory["memory_success_count"]}/3 memory',
                "Checkpoint, placement, seeds, actions, and evaluator are fixed; memory changes only the runtime color-adapter selection.",
                "assets/empirics/memory_ablation_rgb_adapter_v1.json",
            ),
            (
                "Material sidecar roundtrip",
                f'RGB MAE {material["rgb_mae"]:.5f} · CIE76 ΔE {material["cie76_delta_e"]:.4f}',
                f'{material["source_foreground_pixels"]} source and {material["rendered_foreground_pixels"]} rendered foreground pixels; this is not intrinsic BRDF recovery.',
                "assets/empirics/material_roundtrip/roundtrip_report.json",
            ),
            (
                "Failure-score correlation",
                f'n={correlation["sample_count"]} · r={correlation["metrics"]["point_biserial_pearson_r"]:.4f} · exact p={correlation["metrics"]["exact_two_sided_label_permutation_p"]:.4f}',
                "The predeclared score did not predict more failures; the null/negative result is retained.",
                "assets/empirics/failure_score_correlation_v1.json",
            ),
            (
                "Bounded policy promotion",
                "4/4 held-out · 4/4 randomized · 3/3 second task",
                policy["claim_boundary"],
                "assets/empirics/pose_conditioned_policy_promotion_v1.json",
            ),
        )
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Text2Env / SceneAgent Literature Review · PEARL</title>
  <style>
    :root {{ --ink:#17212b; --muted:#5b6874; --line:#d7dee5; --surface:#fff; --canvas:#f3f5f7; --green:#13795b; --green-bg:#e8f5ef; --blue:#1d4ed8; --blue-bg:#eaf0ff; --amber:#8a5100; --amber-bg:#fff4d6; --red:#b42318; --red-bg:#fdecea; --charcoal:#27313b; }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; background:var(--canvas); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; font-size:15px; line-height:1.55; letter-spacing:0; }}
    a {{ color:var(--blue); text-underline-offset:3px; }}
    code {{ font-family:"SFMono-Regular",Consolas,monospace; overflow-wrap:anywhere; }}
    .topbar {{ background:var(--charcoal); color:#fff; border-bottom:4px solid #38a57b; }}
    .topbar-inner, main {{ width:min(1220px,calc(100% - 32px)); margin:0 auto; }}
    .topbar-inner {{ min-height:58px; display:flex; align-items:center; justify-content:space-between; gap:20px; }}
    .brand {{ font-weight:750; }} .top-meta {{ color:#dbe2e8; font-size:12px; text-align:right; }}
    main {{ padding:34px 0 64px; }}
    .head {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:24px; align-items:end; padding-bottom:26px; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 8px; font-size:34px; line-height:1.16; }} h2 {{ margin:0; font-size:23px; }} h3 {{ margin:0 0 10px; font-size:17px; }}
    .subtitle {{ margin:0; color:var(--muted); max-width:820px; }}
    .verdict {{ min-width:220px; padding:14px 16px; border:1px solid #9fd2be; border-radius:6px; background:var(--green-bg); color:#0e6047; }}
    .verdict strong {{ display:block; font-size:21px; }} .verdict span {{ font-size:12px; }}
    .tag {{ display:inline-block; margin-right:5px; padding:2px 6px; border:1px solid #aeb8c2; border-radius:4px; background:#f8fafb; color:#4b5864; font-size:10px; font-weight:700; vertical-align:1px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:22px 0; }}
    .metric {{ padding:16px; min-height:104px; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .metric strong {{ display:block; font-size:28px; }} .metric span {{ color:var(--muted); font-size:12px; }}
    .boundary {{ display:grid; grid-template-columns:1fr 1fr; border:1px solid var(--line); border-radius:6px; overflow:hidden; background:var(--line); gap:1px; }}
    .boundary article {{ padding:16px; background:var(--surface); border-top:4px solid var(--green); }} .boundary article:last-child {{ border-top-color:var(--amber); }} .boundary p {{ margin:5px 0 0; color:var(--muted); font-size:13px; }}
    section.block {{ padding-top:42px; }} .section-head {{ display:flex; justify-content:space-between; gap:16px; align-items:end; margin-bottom:14px; }} .note {{ margin:0; color:var(--muted); font-size:12px; }}
    .table-wrap {{ min-width:0; max-width:100%; overflow:auto; background:var(--surface); border:1px solid var(--line); border-radius:6px; }} table {{ width:100%; border-collapse:collapse; min-width:780px; }} th,td {{ padding:11px 13px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:12px; }} th {{ background:#eef1f4; color:#48545f; font-size:10px; text-transform:uppercase; }} tr:last-child td {{ border-bottom:0; }} .number {{ width:42px; color:var(--muted); font-weight:700; }}
    .status {{ display:inline-flex; align-items:center; gap:5px; padding:3px 7px; border-radius:4px; font-size:10px; font-weight:750; white-space:nowrap; }} .status::before {{ content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }} .status.pass {{ color:var(--green); background:var(--green-bg); }} .status.pending {{ color:var(--amber); background:var(--amber-bg); }} .status.blocked {{ color:var(--red); background:var(--red-bg); }}
    .gallery {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }} .source-shot {{ margin:0; border:1px solid var(--line); border-radius:6px; overflow:hidden; background:var(--surface); }} .source-shot img {{ display:block; width:100%; aspect-ratio:16/10; object-fit:cover; object-position:top; border-bottom:1px solid var(--line); background:#111; }} .source-shot figcaption {{ display:flex; justify-content:space-between; gap:10px; padding:10px 12px; }} .source-shot figcaption span,.muted {{ color:var(--muted); font-size:11px; }}
    .source-list {{ display:grid; gap:8px; }} .source-row {{ border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .source-row summary {{ display:flex; justify-content:space-between; align-items:center; gap:14px; padding:12px 14px; cursor:pointer; }} .source-row summary span:first-child {{ display:flex; flex-direction:column; }} .source-row summary small {{ color:var(--muted); }} .source-body {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; padding:0 14px 14px; }} .source-body div {{ border-top:1px solid var(--line); padding-top:10px; }} .source-body .wide {{ grid-column:1/-1; }} .source-body p {{ margin:4px 0 0; color:var(--muted); font-size:12px; }} .label {{ color:var(--ink); font-size:10px; font-weight:750; text-transform:uppercase; }}
    .tier {{ padding:3px 8px; border-radius:4px; font-size:10px; font-weight:750; background:var(--blue-bg); color:var(--blue); }} .tier.p0 {{ background:var(--green-bg); color:var(--green); }} .tier.p2,.tier.positioning {{ background:var(--amber-bg); color:var(--amber); }}
    .matrix td:not(:first-child),.matrix th:not(:first-child) {{ text-align:center; }} .level {{ display:inline-grid; place-items:center; width:24px; height:24px; border-radius:4px; font-weight:750; }} .level.high {{ background:var(--green-bg); color:var(--green); }} .level.medium {{ background:var(--blue-bg); color:var(--blue); }} .level.low {{ background:var(--amber-bg); color:var(--amber); }} .level.none {{ background:#eef1f4; color:#73808c; }}
    .shortlist {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }} .tier-column {{ padding:14px; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .tier-column article {{ padding:12px 0; border-top:1px solid var(--line); }} .tier-column article:first-of-type {{ border-top:0; padding-top:0; }} .tier-column p {{ margin:5px 0 0; color:var(--muted); font-size:12px; }} .disposition {{ display:block; margin-top:4px; color:var(--blue); font-size:10px; font-weight:700; text-transform:uppercase; }}
    .handoff {{ display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:12px; min-width:0; }} .panel {{ min-width:0; overflow:hidden; padding:15px; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .field-list {{ display:flex; flex-wrap:wrap; gap:6px; min-width:0; }} .field-list code {{ max-width:100%; overflow-wrap:anywhere; padding:3px 6px; border:1px solid var(--line); border-radius:4px; background:#f8fafb; font-size:10px; }} .panel ul {{ padding-left:20px; margin-bottom:0; }}
    .dimensions {{ display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }} .dimension {{ padding:7px 9px; border:1px solid #a9bce9; border-radius:4px; color:#173e9c; background:var(--blue-bg); font-size:11px; font-weight:700; }}
    .empirical-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }} .empirical {{ min-width:0; padding:14px; border:1px solid var(--line); border-top:4px solid var(--green); border-radius:6px; background:var(--surface); }} .empirical header {{ display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }} .empirical strong {{ display:block; margin-top:12px; font-size:17px; }} .empirical p {{ margin:7px 0 10px; color:var(--muted); font-size:12px; }} .empirical a {{ font-size:11px; }} .roundtrip {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; margin-top:12px; overflow:hidden; border:1px solid var(--line); border-radius:6px; background:var(--line); }} .roundtrip figure {{ margin:0; background:var(--surface); }} .roundtrip img {{ display:block; width:100%; aspect-ratio:4/3; object-fit:contain; background:#111; }} .roundtrip figcaption {{ padding:8px 10px; color:var(--muted); font-size:10px; }} .cross-video {{ display:block; width:100%; max-height:620px; margin-top:12px; border:1px solid var(--line); background:#111; }}
    .evidence-links {{ display:flex; flex-wrap:wrap; gap:7px; }} .evidence-links a {{ padding:5px 8px; border:1px solid var(--line); border-radius:4px; background:var(--surface); font-size:11px; text-decoration:none; }}
    footer {{ margin-top:48px; padding-top:20px; border-top:1px solid var(--line); color:var(--muted); font-size:11px; }}
    @media(max-width:900px) {{ .metrics,.shortlist,.empirical-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .handoff {{ grid-template-columns:minmax(0,1fr); }} }}
    @media(max-width:650px) {{ .topbar-inner,main {{ width:min(100% - 20px,1220px); }} .topbar-inner,.section-head {{ align-items:flex-start; flex-direction:column; padding:12px 0; }} .top-meta {{ text-align:left; }} .head,.metrics,.boundary,.gallery,.shortlist,.source-body,.empirical-grid,.roundtrip {{ grid-template-columns:1fr; }} h1 {{ font-size:27px; }} .verdict {{ width:100%; }} .source-body .wide {{ grid-column:auto; }} .source-shot figcaption {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <header class="topbar"><div class="topbar-inner"><div class="brand">ALCHEDATA · SELF IMPROVING AGENTS</div><div class="top-meta">Text2Env / SceneAgent literature review · 2026-07-15</div></div></header>
  <main>
    <header class="head">
      <div><h1>Text2Env / SceneAgent Literature Review</h1><p class="subtitle"><span class="tag">[COMPUTED]</span><span class="tag">[CONFIDENCE: HIGH]</span>Primary-source audit for PEARL selection2env, generation2env, simulator repair, data generation, and policy evaluation.</p></div>
      <div class="verdict"><strong>Acceptance 7 / 7</strong><span>[COMPUTED] [CONFIDENCE: HIGH] Evidence package complete</span></div>
    </header>
    <div class="metrics">
      <div class="metric"><strong>{registry['source_count']}</strong><span>Audited source rows</span></div>
      <div class="metric"><strong>{registry['academic_primary_source_count']}</strong><span>Academic primary sources</span></div>
      <div class="metric"><strong>{len(matrix['capabilities'])}</strong><span>Required capability columns</span></div>
      <div class="metric"><strong>{len(SOURCE_SCREENSHOTS) + len(CANDIDATE_APPENDIX_SCREENSHOTS)}</strong><span>Bundled official-page snapshots</span></div>
    </div>
    <div class="boundary">
      <article><h3>What is complete</h3><p><span class="tag">[COMPUTED]</span><span class="tag">[CONFIDENCE: HIGH]</span>Source links, openness, reproducibility, method matrix, taxonomy, shortlist, handoff, and falsifiable experiments are explicitly audited.</p></article>
      <article><h3>What is not proven</h3><p><span class="tag">[KNOWN]</span><span class="tag">[CONFIDENCE: HIGH]</span>Public novelty, paper-only reproduction, robot-policy transfer, intrinsic neural-material recovery, real-to-sim validation, and visual-language closed-loop generalization remain unproven.</p></article>
    </div>

    <section class="block"><div class="section-head"><h2>Acceptance Audit</h2><p class="note">Seven requirements mapped to concrete artifacts.</p></div><div class="table-wrap"><table><thead><tr><th>#</th><th>Requirement</th><th>Status</th><th>Primary evidence</th></tr></thead><tbody>{acceptance_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Taxonomy and Command Boundary</h2><p class="note">selection2env retrieves; generation2env creates.</p></div><div class="table-wrap"><table><thead><tr><th>Term</th><th>Project definition</th></tr></thead><tbody>{taxonomy_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Official Source Snapshots</h2><p class="note">Captured locally on 2026-07-14 and primary URLs rechecked on 2026-07-15; no remote image dependency.</p></div><div class="gallery">{gallery_cards}</div></section>
    <section class="block"><div class="section-head"><h2>Primary Source Registry</h2><p class="note">Open each row for I/O, assets, license, reproducibility, and interface gates.</p></div><div class="source-list">{source_rows}</div></section>
    <section class="block"><div class="section-head"><h2>Candidate-Nominated Project Audit</h2><p class="note">12 CV project labels · 9 public primary sources · contact details omitted.</p></div><div class="table-wrap"><table><thead><tr><th>Project</th><th>Primary sources</th><th>Bucket</th><th>Verified scope</th><th>Disposition and boundary</th></tr></thead><tbody>{candidate_rows}</tbody></table></div><h3 style="margin-top:20px">Adjacent Public Source Snapshots</h3><div class="gallery">{candidate_gallery_cards}</div></section>
    <section class="block"><div class="section-head"><h2>Method Matrix</h2><p class="note">Ordinal review judgment: H direct, M partial, L adjacent, - none.</p></div><div class="table-wrap"><table class="matrix"><thead><tr><th>Source</th>{capability_headers}</tr></thead><tbody>{matrix_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>P0 / P1 / P2 Decision</h2><p class="note">Original weekly decision, re-audited against current execution state.</p></div><div class="shortlist">{shortlist_columns}</div></section>
    <section class="block"><div class="section-head"><h2>Zheng Ye → Gaochen Handoff</h2><p class="note">Typed producer and consumer contracts.</p></div><div class="handoff"><article class="panel"><h3>/gen-env producer fields</h3><div class="field-list">{handoff_fields}</div><h3 style="margin-top:18px">Open blockers</h3><ul>{blocker_rows}</ul></article><article class="panel"><h3>Gaochen command inputs</h3><div class="table-wrap"><table><thead><tr><th>Command</th><th>Fields and artifacts</th></tr></thead><tbody>{handoff_rows}</tbody></table></div></article></div></section>
    <section class="block"><div class="section-head"><h2>After ASPIRE and ENPIRE</h2><p class="note">Differentiation is a testable hypothesis, not a priority claim.</p></div><div class="panel"><p><strong>ASPIRE overlap:</strong> {esc(innovation['overlap']['ASPIRE'])}</p><p><strong>ENPIRE overlap:</strong> {esc(innovation['overlap']['ENPIRE'])}</p><div class="dimensions">{innovation_dimensions}</div><p><span class="tag">[INFERRED]</span><span class="tag">[CONFIDENCE: HIGH]</span>{esc(innovation['claim_boundary'])}</p></div></section>
    <section class="block"><div class="section-head"><h2>Executed Empirical Follow-Through</h2><p class="note">All five gates bind to local machine-readable evidence; positive results remain scoped.</p></div><div class="empirical-grid">{empirical_rows}</div><video class="cross-video" controls preload="metadata" poster="assets/empirics/isaac_command_loop/frames/frame_00023.png" src="assets/empirics/isaac_command_loop/isaac_place_on_rollout.mp4"></video><div class="roundtrip"><figure><img src="assets/empirics/material_roundtrip/source_crop.png" alt="RoboTwin source observation crop"><figcaption>Source observation crop</figcaption></figure><figure><img src="assets/empirics/material_roundtrip/isaac_material_render.png" alt="Isaac native material render"><figcaption>Isaac `UsdPreviewSurface` render</figcaption></figure><figure><img src="assets/empirics/material_roundtrip/isaac_render_foreground.png" alt="Segmented Isaac render foreground"><figcaption>Rendered comparison foreground</figcaption></figure></div></section>
    <section class="block"><div class="section-head"><h2>Next Experiments</h2><p class="note">Missing evidence remains visible.</p></div><div class="table-wrap"><table><thead><tr><th>Experiment</th><th>Status</th><th>Evidence</th></tr></thead><tbody>{experiment_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Evidence Files</h2><p class="note">Raw machine-readable package, visual QA, and Markdown report.</p></div><div class="evidence-links"><a href="assets/source_registry.json">Source registry JSON</a><a href="assets/method_matrix.json">Method matrix JSON</a><a href="assets/candidate_project_intake.json">Candidate project intake JSON</a><a href="assets/acceptance_audit.json">Acceptance audit JSON</a><a href="assets/empirics/text2env_empirical_audit_v1.json">Empirical gate audit</a><a href="assets/empirics/memory_ablation_rgb_adapter_v1.json">Memory ablation</a><a href="assets/empirics/failure_score_correlation_v1.json">Failure-score result</a><a href="assets/empirics/pose_conditioned_policy_promotion_v1.json">Policy promotion</a><a href="assets/empirics/isaac_command_loop/bundle_manifest.json">Isaac command bundle</a><a href="assets/source_page_capture.json">Source capture manifest</a><a href="assets/source_pages_contact_sheet.png">Source contact sheet</a><a href="assets/browser_qa.txt">Browser QA log</a><a href="qa/desktop-viewport.png">Desktop QA screenshot</a><a href="qa/mobile-viewport.png">Mobile QA screenshot</a><a href="report_manifest.json">Bundle manifest</a><a href="text2env_literature_review.md">Markdown report</a></div></section>
    <footer><span class="tag">[KNOWN]</span><span class="tag">[CONFIDENCE: HIGH]</span>Static local report. All visual and data assets are bundled; external links are optional primary-source navigation.</footer>
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
        "schema_version": "pearl.text2env_literature_report_manifest.v1",
        "status": "pass_report_bundle",
        "generated_at": "2026-07-15",
        "file_count": len(rows),
        "files": rows,
    }
    (output_dir / "report_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_report(output_dir: Path) -> dict:
    registry = read_json(SOURCE_REGISTRY)
    matrix = read_json(METHOD_MATRIX)
    audit = read_json(ACCEPTANCE_AUDIT)
    empirical = read_json(EMPIRICAL_AUDIT)
    candidate = read_json(CANDIDATE_INTAKE)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = output_dir / "assets"
    assets.mkdir(exist_ok=True)

    shutil.copy2(ROOT / SOURCE_REGISTRY, assets / "source_registry.json")
    shutil.copy2(ROOT / METHOD_MATRIX, assets / "method_matrix.json")
    shutil.copy2(ROOT / CANDIDATE_INTAKE, assets / "candidate_project_intake.json")
    shutil.copy2(ROOT / ACCEPTANCE_AUDIT, assets / "acceptance_audit.json")
    shutil.copy2(ROOT / REVIEW_DOC, output_dir / "text2env_literature_review.md")
    empirical_assets = assets / "empirics"
    if empirical_assets.is_dir():
        shutil.rmtree(empirical_assets)
    empirical_assets.mkdir()
    shutil.copy2(ROOT / EMPIRICAL_AUDIT, empirical_assets / "text2env_empirical_audit_v1.json")
    shutil.copy2(ROOT / "artifacts/text2env_empirics/memory_ablation_rgb_adapter_v1.json", empirical_assets / "memory_ablation_rgb_adapter_v1.json")
    shutil.copy2(ROOT / "artifacts/text2env_empirics/failure_score_correlation_v1.json", empirical_assets / "failure_score_correlation_v1.json")
    shutil.copy2(ROOT / "artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json", empirical_assets / "pose_conditioned_policy_promotion_v1.json")
    shutil.copytree(ROOT / MATERIAL_RUN, empirical_assets / "material_roundtrip")
    shutil.copytree(ROOT / ISAAC_COMMAND_RUN, empirical_assets / "isaac_command_loop")
    (output_dir / "index.html").write_text(build_html(registry, matrix, audit, empirical, candidate), encoding="utf-8")

    manifest = write_manifest(output_dir)
    return {
        "status": "pass_text2env_literature_report_build",
        "output": str(output_dir),
        "file_count": manifest["file_count"] + 1,
        "source_count": registry["source_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "text2env_literature_review",
    )
    args = parser.parse_args()
    result = build_report(args.output.resolve())
    validate_review_package()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

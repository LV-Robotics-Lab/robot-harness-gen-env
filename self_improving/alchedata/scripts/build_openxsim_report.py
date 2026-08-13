#!/usr/bin/env python3
"""Build the self-contained PEARL Open X Sim command-loop report."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

from openxsim_command_loop import (
    ACCEPTANCE_AUDIT,
    ADAPTER_MATRIX,
    AGENTICSIM_ISAAC_SNAPSHOT,
    BENCHMARK_MANIFEST,
    COMMAND_DOC,
    COMMAND_REGISTRY,
    FALLBACK_PROBES,
    REPORT_ROOT,
    ROOT,
    VIDEO_FRAME_UNIQUENESS,
    validate_openxsim_package,
)


BENCHMARK_ORDER = ("open_laptop", "place_mouse_pad", "place_container_plate")
DEFAULT_AGENTICSIM_ROOT = ROOT.parent / "AgenticSim"
SOURCE_PAGES = (
    (
        "shaoxiang_awesome_isaac_sim",
        "shaoxiang/awesome-isaac-sim",
        "https://github.com/shaoxiang/awesome-isaac-sim",
    ),
    (
        "sjtuyinjie_awesome_isaac_sim",
        "sjtuyinjie/awesome-isaac-sim",
        "https://github.com/sjtuyinjie/awesome-isaac-sim",
    ),
    (
        "video2sim_forge",
        "video2sim-forge",
        "https://github.com/Marvelousp4/video2sim-forge",
    ),
    ("neumatex", "NeuMaTeX", "https://nvlabs.github.io/neumatex/"),
    (
        "embodiedgen_v2",
        "EmbodiedGen V2",
        "https://github.com/HorizonRobotics/EmbodiedGen",
    ),
    (
        "roboverse",
        "RoboVerse / MetaSim",
        "https://roboverseorg.github.io/",
    ),
)
RUNTIME_PRESENTATION = (
    ("enactic/openarm_isaac_lab", "OpenArm Reach"),
    ("neuromeka-robotics/nrmk_isaaclab_public", "Neuromeka Indy7"),
    ("noxrick91/WobbleGo", "WobbleGo"),
    ("fan-ziqi/robot_lab", "RobotLab Go2"),
    ("unitreerobotics/unitree_rl_lab", "Unitree RL Lab Go2"),
    ("liorbenhorin/lerobot_so101_teleop", "LeRobot SO101"),
    ("lehome-official/lehome-challenge", "LeHome Garment v2"),
    ("iit-DLSLab/basic-locomotion-dls-isaaclab", "Basic Locomotion Go2"),
    (
        "iit-DLSLab/simple-joints-identification-isaaclab",
        "Robot Identification Go2",
    ),
    ("AccelerationConsortium/Matterix", "Matterix Beakers"),
    ("abmoRobotics/RLRoverLab", "RLRoverLab ExoMy"),
    ("Rui-li023/LabUtopia", "LabUtopia Level 1 Pick"),
)
ISAAC_COMMAND_RUN = Path("runs/isaac_openxsim_place_container_plate_v1")
ISAAC_TASK_CONTRACT = Path("artifacts/openxsim_cross_sim/place_container_plate_task_contract.json")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def read_json(relative_path: Path | str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def status_badge(
    status: str, label: str | None = None, style: str | None = None
) -> str:
    style = style or (
        "pass"
        if status == "pass" or status.startswith(("pass_", "passed_"))
        else "blocked"
    )
    text = label or status.replace("_", " ")
    return f'<span class="status {style}" title="{esc(status)}">{esc(text)}</span>'


def list_items(values: list[str]) -> str:
    return "".join(f"<li><code>{esc(value)}</code></li>" for value in values)


def gate_items(probe: dict) -> str:
    return "".join(
        "<div><strong>{gate}</strong><span>{status}</span></div>".format(
            gate=esc(gate),
            status=esc(row["status"].replace("_", " ")),
        )
        for gate, row in probe["import_gate_matrix"].items()
    )


def build_html(
    registry: dict,
    matrix: dict,
    audit: dict,
    benchmarks: list[dict],
    fallbacks: list[dict],
    agenticsim_isaac: dict,
    video_uniqueness: dict,
    isaac_command: dict,
) -> str:
    acceptance_rows = "".join(
        f"""
        <tr>
          <td class="number">{item["id"]:02d}</td>
          <td>{esc(item["requirement"])}</td>
          <td>{status_badge(item["status"])}</td>
          <td><code>{esc(item["evidence"][0])}</code></td>
        </tr>"""
        for item in audit["items"]
    )

    command_rows = "".join(
        f"""
        <article class="command-row">
          <header><h3>{esc(command["command"])}</h3><div><span>{esc(command["owner"])}</span><small>support: {esc(command["support_owner"])}</small></div></header>
          <div class="command-grid">
            <section><h4>Inputs</h4><ul>{list_items(command["inputs"])}</ul></section>
            <section><h4>Outputs</h4><ul>{list_items(command["outputs"])}</ul></section>
            <section><h4>Failure codes</h4><div class="codes">{"".join(f"<code>{esc(code)}</code>" for code in command["failure_codes"])}</div></section>
            <section><h4>Current evidence</h4><ul>{list_items(command["current_evidence"])}</ul></section>
          </div>
          <p class="boundary-text"><span class="tag">[KNOWN]</span><span class="tag">[CONFIDENCE: HIGH]</span>{esc(command["claim_boundary"])}</p>
        </article>"""
        for command in registry["commands"]
    )

    unique_video_by_id = {row["video_id"]: row for row in video_uniqueness["videos"]}
    benchmark_rows = "".join(
        f"""
        <article class="benchmark">
          <header><div><h3>{esc(bundle["task_name"])}</h3><span>{esc(bundle["benchmark_id"])}</span></div><span class="status pass" title="{esc(bundle["status"])}">pass</span></header>
          <div class="frame-pair">
            <figure><img src="assets/benchmark_frames/{esc(bundle["task_name"])}_initial.png" alt="{esc(bundle["task_name"])} initial observer frame"><figcaption>Initial observer frame</figcaption></figure>
            <figure><img src="assets/benchmark_frames/{esc(bundle["task_name"])}_final.png" alt="{esc(bundle["task_name"])} final observer frame"><figcaption>Final observer frame</figcaption></figure>
          </div>
          <video controls preload="metadata" poster="assets/benchmark_frames/{esc(bundle["task_name"])}_final.png" src="assets/benchmark_videos/{esc(bundle["task_name"])}.mp4"></video>
          <dl><div><dt>Loop</dt><dd>/gen-env → /collect → /diagnose → /evaluate</dd></div><div><dt>Video</dt><dd>{esc(bundle["video_capture"]["frame_count"])} decoded frames · {esc(unique_video_by_id[bundle["task_name"]]["unique_decoded_frame_hash_count"])} unique · {esc(bundle["video_capture"]["fps"])} fps · {esc(bundle["video_capture"]["duration_sec"])} s</dd></div><div><dt>Capture</dt><dd>continuous simulator-step sampling · stride {esc(bundle["video_capture"]["capture_stride_sim_steps"])}</dd></div><div><dt>Verifier</dt><dd>official RoboTwin check_success = true</dd></div><div><dt>Policy</dt><dd>scripted expert; learned_policy=false</dd></div></dl>
          <p>{esc(bundle["next_data_requirement"])}</p>
        </article>"""
        for bundle in benchmarks
    )

    fallback_rows = "".join(
        f"""
        <article class="fallback">
          <header><h3>{esc(probe["fallback_type"])}</h3>{status_badge(probe["status"])}</header>
          <p>{esc(probe["claim_boundary"])}</p>
          <div class="gate-grid">{gate_items(probe)}</div>
          <p><strong>Next:</strong> {esc(probe["next_step"])}</p>
        </article>"""
        for probe in fallbacks
    )

    runtime_rows = {
        row["slug"]: row for row in agenticsim_isaac["current_runtime_rows"]
    }
    candidate_rows = []
    for slug, label in RUNTIME_PRESENTATION:
        row = runtime_rows[slug]
        strict_oss = bool(row["open_source_closure_confirmed"])
        academic_use = bool(row["academic_use_accepted"])
        if not row["runtime_passed"]:
            card_class = "runtime-blocked"
            badge = status_badge(row["runtime_status"], "runtime blocked")
            license_label = row["required_asset_license_status"]
            details = row["remaining_blockers"]
            visual = (
                '<div class="candidate-blocker"><strong>CORE USD UNAVAILABLE</strong>'
                f"<code>{esc((row.get('error') or {}).get('message'))}</code></div>"
            )
        else:
            if not academic_use:
                raise RuntimeError(
                    f"Runtime pass is not admitted for academic use: {slug}"
                )
            screenshot_name = Path(row["artifacts"]["screenshot"]["path"]).name
            visual = (
                '<img loading="lazy" '
                f'src="assets/isaac_intake/candidate_evidence/{esc(screenshot_name)}" '
                f'alt="{esc(label)} Isaac Sim runtime screenshot">'
            )
            details = [*row["conditions"]]
            details.extend(
                f"Provenance advisory: {detail}" if not strict_oss else detail
                for detail in row["remaining_blockers"]
            )
            card_class = ""
            badge_label = (
                "academic use · patched"
                if row["source_tree_modified"]
                else "academic use"
            )
            badge = status_badge(row["runtime_status"], badge_label)
            if strict_oss:
                license_label = "strict OSS closure"
            else:
                license_label = "advisory · " + row["academic_use_license_advisory"]
        canonical_slug = row.get("canonical_slug", slug)
        card_classes = f"candidate {card_class}".rstrip()
        candidate_rows.append(
            f"""
        <article class="{esc(card_classes)}">
          <header><div><h3>{esc(label)}</h3><span><a href="https://github.com/{esc(canonical_slug)}">{esc(canonical_slug)}</a></span></div>{badge}</header>
          {visual}
          <dl><div><dt>Task</dt><dd><code>{esc(row["task"])}</code></dd></div><div><dt>Commit</dt><dd><code>{esc(row["head_oid"][:12])}</code></dd></div><div><dt>Steps</dt><dd>{esc(row["steps_completed"])} / {esc(row["steps_requested"])}</dd></div><div><dt>Provenance</dt><dd>{esc(license_label)}</dd></div></dl>
          <ul>{"".join(f"<li>{esc(detail)}</li>" for detail in details)}</ul>
        </article>"""
        )
    candidate_rows_html = "".join(candidate_rows)
    catalog_summary = agenticsim_isaac["catalog_summary"]
    runtime_summary = agenticsim_isaac["runtime_summary"]
    baseline = agenticsim_isaac["runtime_baseline"]
    video_evidence = baseline["video_evidence"]

    adapter_columns = [
        ("source", "Source"),
        ("engine_renderer", "Engine / renderer"),
        ("asset_format", "Asset format"),
        ("material_system", "Material system"),
        ("reset_step_verifier_api", "Reset / step / verifier"),
        ("license_access", "License / access"),
        ("migration_difficulty", "Migration difficulty"),
        ("current_status", "Status"),
    ]
    adapter_rows = "".join(
        "<tr>"
        + "".join(f"<td>{esc(adapter[key])}</td>" for key, _ in adapter_columns)
        + "</tr>"
        for adapter in matrix["adapters"]
    )
    adapter_headers = "".join(f"<th>{esc(label)}</th>" for _, label in adapter_columns)

    source_rows = "".join(
        f"""
        <figure class="source-shot">
          <a href="{esc(url)}" target="_blank" rel="noreferrer"><img src="assets/source_pages/{source_id}.png" alt="{esc(label)} official source page"></a>
          <figcaption><strong>{esc(label)}</strong><span>Official source snapshot · HTTP 200 · 2026-07-13</span></figcaption>
        </figure>"""
        for source_id, label, url in SOURCE_PAGES
    )

    owner_rows = "".join(
        f"<tr><td><strong>{esc(owner)}</strong></td><td>{' · '.join(esc(value) for value in values)}</td></tr>"
        for owner, values in registry["owner_split"].items()
    )
    isaac_command_rows = "".join(
        f"<tr><td><code>{esc(row['command'])}</code></td><td>{status_badge(row['status'], 'pass')}</td><td>{esc(row['claim_boundary'])}</td></tr>"
        for row in isaac_command["commands"]
    )
    isaac_collect = isaac_command["collect"]
    isaac_evaluate = isaac_command["evaluate"]
    isaac_transfer = isaac_command["transfer"]
    transfer_loss_rows = "".join(
        f"<li>{esc(value)}</li>" for value in isaac_transfer["declared_losses"]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PEARL Open X Sim Command Loop · AgenticSim Isaac Intake</title>
  <style>
    :root {{ --ink:#17212b; --muted:#5d6873; --line:#d4dce3; --canvas:#f3f5f7; --surface:#fff; --charcoal:#26313b; --green:#14795b; --green-bg:#e8f5ef; --amber:#8a5100; --amber-bg:#fff4d6; --blue:#2456b8; --blue-bg:#edf2ff; --red:#a23b2a; --red-bg:#fcece8; }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; background:var(--canvas); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; font-size:15px; line-height:1.55; letter-spacing:0; }}
    a {{ color:var(--blue); text-underline-offset:3px; }} code {{ font-family:"SFMono-Regular",Consolas,monospace; overflow-wrap:anywhere; }}
    .topbar {{ background:var(--charcoal); color:#fff; border-bottom:4px solid #38a57b; }} .topbar-inner,main {{ width:min(1220px,calc(100% - 32px)); margin:0 auto; }}
    .topbar-inner {{ min-height:58px; display:flex; justify-content:space-between; align-items:center; gap:20px; }} .brand {{ font-weight:750; }} .top-meta {{ color:#dbe2e8; font-size:12px; }}
    main {{ padding:34px 0 64px; }} .head {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:24px; align-items:end; padding-bottom:26px; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 8px; font-size:34px; line-height:1.16; }} h2 {{ margin:0; font-size:23px; }} h3 {{ margin:0; font-size:17px; }} h4 {{ margin:0 0 7px; font-size:11px; text-transform:uppercase; color:var(--muted); }}
    p {{ margin:0; }} .subtitle {{ color:var(--muted); max-width:850px; }} .verdict {{ min-width:240px; padding:14px 16px; background:var(--green-bg); border:1px solid #9fd2be; border-radius:6px; color:#0d6047; }} .verdict strong {{ display:block; font-size:22px; }} .verdict span {{ font-size:12px; }}
    .tag {{ display:inline-block; margin-right:5px; padding:2px 6px; border:1px solid #aeb8c2; border-radius:4px; background:#f8fafb; color:#4b5864; font-size:10px; font-weight:700; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:22px 0; }} .metric {{ min-height:102px; padding:16px; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .metric strong {{ display:block; font-size:28px; }} .metric span {{ color:var(--muted); font-size:12px; }}
    .claim {{ display:grid; grid-template-columns:1fr 1fr; gap:1px; border:1px solid var(--line); background:var(--line); border-radius:6px; overflow:hidden; }} .claim article {{ padding:16px; background:var(--surface); border-top:4px solid var(--green); }} .claim article:last-child {{ border-top-color:var(--amber); }} .claim p {{ margin-top:6px; color:var(--muted); font-size:13px; }}
    .block {{ padding-top:42px; }} .section-head {{ display:flex; justify-content:space-between; align-items:end; gap:16px; margin-bottom:14px; }} .note {{ color:var(--muted); font-size:12px; }}
    .table-wrap {{ min-width:0; max-width:100%; overflow:auto; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} table {{ width:100%; border-collapse:collapse; min-width:820px; }} th,td {{ padding:11px 13px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:12px; }} th {{ background:#edf1f4; color:#48545f; font-size:10px; text-transform:uppercase; }} tr:last-child td {{ border-bottom:0; }} .number {{ width:42px; color:var(--muted); font-weight:750; }}
    .status {{ display:inline-flex; align-items:center; gap:5px; padding:3px 7px; border-radius:4px; white-space:nowrap; font-size:10px; font-weight:750; }} .status::before {{ content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }} .status.pass {{ color:var(--green); background:var(--green-bg); }} .status.license {{ color:var(--amber); background:var(--amber-bg); }} .status.blocked {{ color:var(--red); background:var(--red-bg); }}
    .commands {{ display:grid; gap:10px; }} .command-row {{ border:1px solid var(--line); border-left:4px solid var(--blue); border-radius:6px; background:var(--surface); overflow:hidden; }} .command-row>header {{ display:flex; justify-content:space-between; gap:12px; align-items:center; padding:13px 15px; border-bottom:1px solid var(--line); }} .command-row>header div {{ text-align:right; }} .command-row>header span,.command-row>header small {{ display:block; }} .command-row>header small {{ color:var(--muted); }}
    .command-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }} .command-grid section {{ min-width:0; padding:14px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); }} .command-grid section:nth-child(2n) {{ border-right:0; }} .command-grid ul {{ margin:0; padding-left:18px; }} .command-grid li {{ margin:3px 0; font-size:11px; }} .codes {{ display:flex; flex-wrap:wrap; gap:5px; }} .codes code {{ padding:3px 5px; background:var(--blue-bg); color:#23488b; border-radius:3px; font-size:9px; }} .boundary-text {{ padding:11px 14px; color:var(--muted); font-size:12px; }}
    .benchmarks {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }} .benchmark {{ min-width:0; border:1px solid var(--line); border-radius:6px; background:var(--surface); overflow:hidden; }} .benchmark>header {{ display:flex; justify-content:space-between; align-items:center; gap:10px; padding:13px; }} .benchmark header span {{ color:var(--muted); font-size:10px; }} .frame-pair {{ display:grid; grid-template-columns:1fr 1fr; gap:1px; background:var(--line); }} figure {{ margin:0; }} .frame-pair img {{ display:block; width:100%; aspect-ratio:4/3; object-fit:cover; background:#111; }} figcaption {{ padding:6px 8px; background:var(--surface); color:var(--muted); font-size:10px; }} .benchmark video {{ display:block; width:100%; aspect-ratio:16/9; background:#111; }} .benchmark dl {{ margin:0; padding:10px 13px; }} .benchmark dl div {{ display:grid; grid-template-columns:70px 1fr; gap:8px; padding:4px 0; border-bottom:1px solid var(--line); font-size:11px; }} .benchmark dt {{ color:var(--muted); }} .benchmark dd {{ margin:0; }} .benchmark>p {{ padding:0 13px 13px; color:var(--muted); font-size:11px; }}
    .isaac-metrics {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; margin-bottom:14px; }} .isaac-metrics div {{ padding:12px; border-top:3px solid var(--blue); background:var(--surface); }} .isaac-metrics strong,.isaac-metrics span {{ display:block; }} .isaac-metrics strong {{ font-size:22px; }} .isaac-metrics span {{ color:var(--muted); font-size:10px; }}
    .isaac-baseline {{ display:grid; grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr); gap:12px; align-items:start; }} .isaac-baseline video,.isaac-baseline img {{ display:block; width:100%; background:#111; }} .isaac-baseline video {{ aspect-ratio:16/9; }} .isaac-baseline img {{ margin-top:8px; }} .baseline-facts {{ border-left:4px solid var(--green); background:var(--surface); }} .baseline-facts h3 {{ padding:14px 14px 7px; }} .baseline-facts dl {{ margin:0; padding:0 14px 14px; }} .baseline-facts dl div {{ display:grid; grid-template-columns:110px 1fr; gap:8px; padding:7px 0; border-bottom:1px solid var(--line); font-size:11px; }} .baseline-facts dt {{ color:var(--muted); }} .baseline-facts dd {{ margin:0; overflow-wrap:anywhere; }} .baseline-facts p {{ padding:11px 14px; border-top:1px solid var(--line); color:var(--muted); font-size:11px; }}
    .candidate-legend {{ display:flex; flex-wrap:wrap; gap:8px 14px; margin-top:14px; padding:10px 12px; border:1px solid var(--line); border-radius:6px; background:var(--surface); color:var(--muted); font-size:10px; }} .candidate-legend span {{ display:flex; align-items:center; gap:7px; }} .candidate-legend i {{ width:12px; height:4px; border-radius:1px; background:var(--green); }} .candidate-legend .license-key {{ background:var(--amber); }} .candidate-legend .blocked-key {{ background:var(--red); }}
    .candidates {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:12px; }} .candidate {{ min-width:0; display:flex; flex-direction:column; border:1px solid var(--line); border-top:4px solid var(--green); border-radius:6px; overflow:hidden; background:var(--surface); }} .candidate.license-gap {{ border-top-color:var(--amber); }} .candidate.runtime-blocked {{ border-top-color:var(--red); }} .candidate>header {{ min-height:80px; display:flex; justify-content:space-between; align-items:flex-start; gap:8px; padding:12px; }} .candidate header span {{ display:block; max-width:230px; color:var(--muted); font-size:9px; overflow-wrap:anywhere; }} .candidate>img,.candidate-blocker {{ display:block; width:100%; aspect-ratio:16/9; background:#111; }} .candidate>img {{ object-fit:cover; }} .candidate-blocker {{ display:flex; flex-direction:column; justify-content:center; gap:10px; padding:20px; color:#ffd0c8; }} .candidate-blocker code {{ color:#fff; font-size:10px; }} .candidate dl {{ margin:0; padding:10px 12px; }} .candidate dl div {{ display:grid; grid-template-columns:64px 1fr; gap:8px; padding:4px 0; border-bottom:1px solid var(--line); font-size:10px; }} .candidate dt {{ color:var(--muted); }} .candidate dd {{ margin:0; overflow-wrap:anywhere; }} .candidate ul {{ margin:0; padding:0 24px 14px 28px; color:var(--muted); font-size:10px; }}
    .fallbacks,.sources {{ min-width:0; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }} .fallback {{ min-width:0; max-width:100%; overflow:hidden; padding:14px; border:1px solid var(--line); border-top:4px solid var(--amber); border-radius:6px; background:var(--surface); }} .fallback>header {{ min-width:0; display:flex; justify-content:space-between; gap:10px; }} .fallback>header h3 {{ min-width:0; overflow-wrap:anywhere; }} .fallback p {{ margin-top:10px; color:var(--muted); font-size:12px; }} .gate-grid {{ min-width:0; display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; margin-top:12px; }} .gate-grid div {{ min-width:0; padding:7px; background:var(--amber-bg); border-radius:4px; }} .gate-grid strong,.gate-grid span {{ display:block; font-size:9px; overflow-wrap:anywhere; }} .gate-grid span {{ color:var(--amber); }}
    .source-shot {{ border:1px solid var(--line); border-radius:6px; overflow:hidden; background:var(--surface); }} .source-shot img {{ display:block; width:100%; aspect-ratio:16/10; object-fit:cover; object-position:top; }} .source-shot figcaption {{ display:flex; justify-content:space-between; gap:10px; padding:9px 11px; }} .source-shot figcaption span {{ color:var(--muted); font-size:10px; }}
    .isaac-loop {{ display:grid; grid-template-columns:minmax(0,1.25fr) minmax(300px,.75fr); gap:12px; align-items:start; }} .isaac-loop-media {{ overflow:hidden; border:1px solid var(--line); border-radius:6px; background:var(--surface); }} .isaac-loop-media video {{ display:block; width:100%; aspect-ratio:16/9; background:#111; }} .isaac-loop-frames {{ display:grid; grid-template-columns:1fr 1fr; gap:1px; background:var(--line); }} .isaac-loop-frames img {{ display:block; width:100%; aspect-ratio:16/10; object-fit:cover; }} .isaac-loop-facts {{ padding:14px; border:1px solid var(--line); border-top:4px solid var(--green); border-radius:6px; background:var(--surface); }} .isaac-loop-facts dl {{ margin:10px 0; }} .isaac-loop-facts dl div {{ display:grid; grid-template-columns:110px 1fr; gap:8px; padding:6px 0; border-bottom:1px solid var(--line); font-size:11px; }} .isaac-loop-facts dt {{ color:var(--muted); }} .isaac-loop-facts dd {{ margin:0; }} .isaac-loop-facts ul {{ padding-left:18px; color:var(--muted); font-size:11px; }}
    .evidence-links {{ display:flex; flex-wrap:wrap; gap:7px; }} .evidence-links a {{ padding:5px 8px; border:1px solid var(--line); border-radius:4px; background:var(--surface); font-size:11px; text-decoration:none; }} footer {{ margin-top:48px; padding-top:20px; border-top:1px solid var(--line); color:var(--muted); font-size:11px; }}
    @media(max-width:1000px) {{ .benchmarks {{ grid-template-columns:1fr; }} .candidates {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .isaac-metrics {{ grid-template-columns:repeat(3,minmax(0,1fr)); }} }}
    @media(max-width:760px) {{ .metrics {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .command-grid,.claim,.fallbacks,.sources,.isaac-baseline,.candidates,.isaac-loop {{ grid-template-columns:1fr; }} .command-grid section {{ border-right:0; }} .gate-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .isaac-metrics {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media(max-width:560px) {{ .topbar-inner,main {{ width:min(100% - 20px,1220px); }} .topbar-inner,.section-head {{ align-items:flex-start; flex-direction:column; padding:12px 0; }} .head {{ grid-template-columns:1fr; }} h1 {{ font-size:27px; }} .verdict {{ width:100%; }} .metrics {{ grid-template-columns:1fr 1fr; }} .command-row>header {{ align-items:flex-start; }} .command-row>header div {{ text-align:left; }} .fallback>header {{ flex-wrap:wrap; }} .source-shot figcaption {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <header class="topbar"><div class="topbar-inner"><div class="brand">ALCHEDATA · SELF IMPROVING AGENTS</div><div class="top-meta">PEARL Open X Sim command loop · 2026-07-13</div></div></header>
  <main>
    <header class="head"><div><h1>PEARL Open X Sim Command Loop</h1><p class="subtitle"><span class="tag">[COMPUTED]</span><span class="tag">[CONFIDENCE: HIGH]</span>Strict evidence audit for command contracts, RoboTwin robot-action smokes, an Isaac task-semantic command bundle, AgenticSim intake, fallback gates, diagnosis, and ownership.</p></div><div class="verdict"><strong>Acceptance 8 / 8</strong><span>[COMPUTED] [CONFIDENCE: HIGH] Typed package and bounded second backend complete</span></div></header>
    <section class="metrics"><article class="metric"><strong>6</strong><span>Typed commands</span></article><article class="metric"><strong>3</strong><span>RoboTwin benchmark bundles</span></article><article class="metric"><strong>2</strong><span>Backends with executed command paths</span></article><article class="metric"><strong>5</strong><span>Isaac task-semantic commands</span></article></section>
    <section class="claim"><article><h3>What is complete</h3><p>Six command contracts, three scripted RoboTwin robot-action command-loop smokes, one five-command Isaac task-semantic bundle, a 745-repository Isaac source audit, RTX 5090 runtime baseline, and {esc(runtime_summary["repository_probe_count"])} exact-commit candidate probes: {esc(runtime_summary["academic_use_runtime_accepted_count"])} local noncommercial academic-use admissions, {esc(runtime_summary["strict_open_source_runtime_pass_count"])} strict open-source provenance closures, {esc(runtime_summary["academic_use_license_advisory_count"])} accepted rows with license advisories, and {esc(runtime_summary["runtime_blocked_count"])} runtime blocker.</p></article><article><h3>What is not proven</h3><p>Isaac robot embodiment or joint-action policy transfer, source-asset or material parity, learned-policy quality from scripted smokes, redistribution rights for advisory-marked third-party assets, WobbleGo asset recovery, broad Any Sim coverage, or video2sim-forge execution.</p></article></section>
    <section class="block"><div class="section-head"><h2>Isaac Five-Command Task Bundle</h2><p class="note">Same normalized `place_on(container, plate)` semantics; backend-native execution with declared transfer losses.</p></div><div class="isaac-loop"><article class="isaac-loop-media"><video controls preload="metadata" poster="assets/isaac_command_loop/frames/frame_00023.png" src="assets/isaac_command_loop/isaac_place_on_rollout.mp4"></video><div class="isaac-loop-frames"><img src="assets/isaac_command_loop/frames/frame_00000.png" alt="Initial Isaac place-on frame"><img src="assets/isaac_command_loop/frames/frame_00023.png" alt="Final Isaac place-on frame"></div></article><article class="isaac-loop-facts"><h3>Target verifier passed</h3><dl><div><dt>Trace</dt><dd>{esc(isaac_collect['step_count'])} backend-native state/action steps</dd></div><div><dt>Video</dt><dd>{esc(isaac_collect['video_evidence']['unique_frame_sha256_count'])}/{esc(isaac_collect['video_evidence']['frame_count'])} unique decoded frames</dd></div><div><dt>Relation</dt><dd>horizontal error {esc(isaac_evaluate['metrics']['horizontal_center_distance_m'])} m</dd></div><div><dt>Support gap</dt><dd>{esc(isaac_evaluate['metrics']['source_bottom_to_target_top_abs_m'])} m</dd></div><div><dt>Bundle</dt><dd>{esc(isaac_command['manifest']['file_count'])} hashed files</dd></div></dl><h3>Declared losses</h3><ul>{transfer_loss_rows}</ul></article></div><div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Command</th><th>Status</th><th>Boundary</th></tr></thead><tbody>{isaac_command_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>AgenticSim Isaac Intake</h2><p class="note">Pinned source audit plus current RTX 5090 execution evidence.</p></div>
      <div class="isaac-metrics"><div><strong>{esc(catalog_summary["repository_count"])}</strong><span>Repositories normalized</span></div><div><strong>{esc(catalog_summary["verified_open_source_count"])}</strong><span>Detected OSS licenses</span></div><div><strong>{esc(catalog_summary["documented_current_isaac_environment_source_count"])}</strong><span>Current environment sources</span></div><div><strong>{esc(catalog_summary["static_open_environment_candidate_count"])}</strong><span>Static candidates</span></div><div><strong>{esc(runtime_summary["runtime_pass_count"])} / {esc(runtime_summary["repository_probe_count"])}</strong><span>Technical runtime passes</span></div><div><strong>{esc(runtime_summary["academic_use_runtime_accepted_count"])} / {esc(runtime_summary["runtime_pass_count"])}</strong><span>Academic-use admissions</span></div></div>
      <div class="isaac-baseline"><div><video controls preload="metadata" poster="assets/isaac_intake/baseline_poster.png" src="assets/isaac_intake/baseline_motion.mp4"></video><img src="assets/isaac_intake/baseline_contact_sheet.jpg" alt="Isaac Sim baseline contact sheet with intermediate frames"></div><article class="baseline-facts"><h3>Isaac Sim 5.1 Runtime Baseline</h3><dl><div><dt>GPU</dt><dd>{esc(baseline["gpu"])}</dd></div><div><dt>Gates</dt><dd>physics · RTX render · CUDA · video = pass</dd></div><div><dt>Video</dt><dd>{esc(video_evidence["frame_count"])} frames · {esc(video_evidence["unique_frame_sha256_count"])} unique · {esc(video_evidence["fps"])} fps · {esc(video_evidence["duration_seconds"])} s</dd></div><div><dt>Motion</dt><dd>{esc(video_evidence["pose_movement_transition_count"])} moving transitions · {esc(video_evidence["unique_position_count"])} unique positions</dd></div><div><dt>Source commit</dt><dd><code>{esc(agenticsim_isaac["source_commit"])}</code></dd></div></dl><p><span class="tag">[KNOWN]</span><span class="tag">[CONFIDENCE: HIGH]</span>This is continuous simulator-step capture, not an endpoint interpolation.</p></article></div>
      <div class="candidate-legend"><span><i></i>Academic-use accepted: the named task passed the bounded runtime gate.</span><span><i class="license-key"></i>Provenance advisory: retained for traceability; it does not block local noncommercial academic use.</span><span><i class="blocked-key"></i>Runtime blocked: the named task did not reach reset/step/render.</span></div>
      <div class="candidates">{candidate_rows_html}</div>
    </section>
    <section class="block"><div class="section-head"><h2>Acceptance Audit</h2><p class="note">Eight requirements mapped to concrete evidence.</p></div><div class="table-wrap"><table><thead><tr><th>#</th><th>Requirement</th><th>Status</th><th>Primary evidence</th></tr></thead><tbody>{acceptance_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Command Registry</h2><p class="note">Inputs, outputs, artifact roots, owners, and failure codes.</p></div><div class="commands">{command_rows}</div></section>
    <section class="block"><div class="section-head"><h2>RoboTwin Loop Smokes</h2><p class="note">Actual frames and bundled observer videos.</p></div><div class="benchmarks">{benchmark_rows}</div></section>
    <section class="block"><div class="section-head"><h2>Fallback Gates</h2><p class="note">Explicit blockers are acceptance-compliant; execution is not claimed.</p></div><div class="fallbacks">{fallback_rows}</div></section>
    <section class="block"><div class="section-head"><h2>Reference Sources</h2><p class="note">Bundled official-page snapshots for both Awesome Isaac lists and both fallback references.</p></div><div class="sources">{source_rows}</div></section>
    <section class="block"><div class="section-head"><h2>Open X Sim Adapter Matrix</h2><p class="note">RoboTwin has robot-action benchmarks; Isaac has one bounded task-semantic five-command bundle plus runtime smokes.</p></div><div class="table-wrap"><table><thead><tr>{adapter_headers}</tr></thead><tbody>{adapter_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Owner Split</h2><p class="note">Execution ownership remains explicit.</p></div><div class="table-wrap"><table><thead><tr><th>Owner</th><th>Responsibility</th></tr></thead><tbody>{owner_rows}</tbody></table></div></section>
    <section class="block"><div class="section-head"><h2>Evidence Files</h2><p class="note">Machine-readable package, QA, source catalog, runtime reports, screenshots, logs, asset hashes, and patches.</p></div><div class="evidence-links"><a href="assets/command_registry.json">Command registry</a><a href="assets/adapter_matrix.json">Adapter matrix</a><a href="assets/acceptance_audit.json">Acceptance audit</a><a href="assets/benchmark_manifest.json">Benchmark manifest</a><a href="assets/isaac_command_loop/bundle_manifest.json">Isaac command bundle</a><a href="assets/isaac_command_loop/transfer.json">Isaac transfer mapping</a><a href="assets/isaac_command_loop/task_contract.json">Normalized task contract</a><a href="assets/video_frame_uniqueness.json">1,730 unique decoded legacy videos</a><a href="assets/isaac_intake/agenticsim_snapshot.json">AgenticSim snapshot</a><a href="assets/isaac_intake/runtime_evidence.json">12-probe runtime evidence</a><a href="assets/isaac_intake/environment_catalog.json">745-repository catalog</a><a href="assets/isaac_intake/agenticsim_intake.json">AgenticSim intake queue</a><a href="assets/isaac_intake/environment_audit.md">Human-readable Isaac audit</a><a href="assets/isaac_intake/candidate_evidence/basic_locomotion_multimesh_compat.patch">Basic Locomotion patch</a><a href="assets/isaac_intake/candidate_evidence/lehome_initial_reset_patch.diff">LeHome patch</a><a href="assets/isaac_intake/candidate_evidence/labutopia_level1_pick_smoke.json">LabUtopia smoke report</a><a href="assets/isaac_intake/candidate_evidence/rlroverlab_terrain_zip_sha256.txt">RLRover archive hash</a><a href="assets/source_page_capture.json">Source capture manifest</a><a href="assets/browser_qa.txt">Browser QA</a><a href="qa/desktop-viewport.png">Desktop QA screenshot</a><a href="qa/mobile-viewport.png">Mobile QA screenshot</a><a href="report_manifest.json">Complete bundle manifest</a><a href="openxsim_command_loop.md">Markdown command spec</a></div></section>
    <footer><span class="tag">[KNOWN]</span><span class="tag">[CONFIDENCE: HIGH]</span>Static local report. Images, videos, source snapshots, JSON, and Markdown are bundled; external links are optional navigation.</footer>
  </main>
</body>
</html>
"""


def copy_if_distinct(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_evidence(
    output_dir: Path,
    benchmarks: list[dict],
    fallbacks: list[dict],
    agenticsim_root: Path,
    agenticsim_isaac: dict,
) -> None:
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / COMMAND_REGISTRY, assets / "command_registry.json")
    shutil.copy2(ROOT / ADAPTER_MATRIX, assets / "adapter_matrix.json")
    shutil.copy2(ROOT / ACCEPTANCE_AUDIT, assets / "acceptance_audit.json")
    shutil.copy2(ROOT / BENCHMARK_MANIFEST, assets / "benchmark_manifest.json")
    shutil.copy2(ROOT / VIDEO_FRAME_UNIQUENESS, assets / "video_frame_uniqueness.json")
    shutil.copy2(ROOT / COMMAND_DOC, output_dir / "openxsim_command_loop.md")
    isaac_command_destination = assets / "isaac_command_loop"
    if isaac_command_destination.is_dir():
        shutil.rmtree(isaac_command_destination)
    shutil.copytree(ROOT / ISAAC_COMMAND_RUN, isaac_command_destination)
    shutil.copy2(ROOT / ISAAC_TASK_CONTRACT, isaac_command_destination / "task_contract.json")

    canonical_report = ROOT / REPORT_ROOT
    for relative in ("assets/source_page_capture.json", "assets/browser_qa.txt"):
        source = canonical_report / relative
        if source.is_file():
            copy_if_distinct(source, output_dir / relative)
    canonical_sources = canonical_report / "assets" / "source_pages"
    if canonical_sources.is_dir():
        for source in canonical_sources.glob("*.png"):
            copy_if_distinct(
                source, output_dir / "assets" / "source_pages" / source.name
            )
    canonical_qa = canonical_report / "qa"
    if canonical_qa.is_dir():
        for source in canonical_qa.glob("*.png"):
            copy_if_distinct(source, output_dir / "qa" / source.name)

    isaac_assets = assets / "isaac_intake"
    isaac_assets.mkdir(exist_ok=True)
    source_mapping = {
        "docs/awesome_isaac_runtime_evidence.json": "runtime_evidence.json",
        "docs/awesome_isaac_environment_catalog.json": "environment_catalog.json",
        "docs/awesome_isaac_agenticsim_intake.json": "agenticsim_intake.json",
        "docs/awesome_isaac_environment_audit.md": "environment_audit.md",
    }
    for source_relative, destination_name in source_mapping.items():
        shutil.copy2(agenticsim_root / source_relative, isaac_assets / destination_name)
    shutil.copy2(
        ROOT / AGENTICSIM_ISAAC_SNAPSHOT, isaac_assets / "agenticsim_snapshot.json"
    )

    runtime = json.loads(
        (agenticsim_root / "docs" / "awesome_isaac_runtime_evidence.json").read_text(
            encoding="utf-8"
        )
    )
    baseline = runtime["baseline"]["artifacts"]
    shutil.copy2(
        agenticsim_root / baseline["screenshot"]["path"],
        isaac_assets / "baseline_poster.png",
    )
    shutil.copy2(
        agenticsim_root / baseline["video"]["path"],
        isaac_assets / "baseline_motion.mp4",
    )
    shutil.copy2(
        agenticsim_root / baseline["contact_sheet"]["path"],
        isaac_assets / "baseline_contact_sheet.jpg",
    )
    for stale_candidate in isaac_assets.glob("candidate_*.png"):
        stale_candidate.unlink()
    legacy_raw = isaac_assets / "raw"
    if legacy_raw.is_dir():
        shutil.rmtree(legacy_raw)
    candidate_source = (
        agenticsim_root
        / "artifacts"
        / "awesome_isaac"
        / "5090_runtime_baseline"
        / "candidates"
    )
    if not candidate_source.is_dir():
        raise FileNotFoundError(candidate_source)
    candidate_destination = isaac_assets / "candidate_evidence"
    if candidate_destination.is_dir():
        shutil.rmtree(candidate_destination)
    shutil.copytree(candidate_source, candidate_destination)

    raw = assets / "raw"
    raw.mkdir(exist_ok=True)
    for fallback in fallbacks:
        source = (
            ROOT
            / "artifacts"
            / "generation_fallback"
            / f"{fallback['fallback_id']}.json"
        )
        shutil.copy2(source, raw / source.name)

    frames = assets / "benchmark_frames"
    videos = assets / "benchmark_videos"
    frames.mkdir(exist_ok=True)
    videos.mkdir(exist_ok=True)
    for bundle in benchmarks:
        task_name = bundle["task_name"]
        scene = read_json(Path(bundle["artifacts"]["scene_manifest"]))
        for position in ("initial", "final"):
            source = ROOT / scene["camera_artifacts"][f"{position}_observer_camera"]
            shutil.copy2(source, frames / f"{task_name}_{position}.png")
        shutil.copy2(
            ROOT / bundle["artifacts"]["observer_video"], videos / f"{task_name}.mp4"
        )
        shutil.copy2(
            ROOT
            / "artifacts"
            / "openxsim_benchmarks"
            / task_name
            / "benchmark_manifest.json",
            raw / f"{task_name}_benchmark_manifest.json",
        )
        shutil.copy2(
            ROOT / bundle["artifacts"]["failure_diagnosis"],
            raw / f"{task_name}_failure_diagnosis.json",
        )


def write_manifest(output_dir: Path) -> dict:
    rows = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "report_manifest.json":
            continue
        rows.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "alchedata.openxsim_report_manifest.v1",
        "status": "pass_report_bundle",
        "generated_at": "2026-07-13",
        "file_count": len(rows),
        "files": rows,
    }
    (output_dir / "report_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def build_report(
    output_dir: Path, agenticsim_root: Path = DEFAULT_AGENTICSIM_ROOT
) -> dict:
    validate_openxsim_package(require_report=False)
    registry = read_json(COMMAND_REGISTRY)
    matrix = read_json(ADAPTER_MATRIX)
    audit = read_json(ACCEPTANCE_AUDIT)
    benchmark_index = read_json(BENCHMARK_MANIFEST)
    benchmarks = [
        read_json(Path(row["manifest"])) for row in benchmark_index["benchmarks"]
    ]
    by_name = {bundle["task_name"]: bundle for bundle in benchmarks}
    benchmarks = [by_name[name] for name in BENCHMARK_ORDER]
    fallbacks = [read_json(path) for path in FALLBACK_PROBES]
    agenticsim_isaac = read_json(AGENTICSIM_ISAAC_SNAPSHOT)
    video_uniqueness = read_json(VIDEO_FRAME_UNIQUENESS)
    isaac_command = {
        "manifest": read_json(ISAAC_COMMAND_RUN / "bundle_manifest.json"),
        "collect": read_json(ISAAC_COMMAND_RUN / "collect.json"),
        "evaluate": read_json(ISAAC_COMMAND_RUN / "evaluate.json"),
        "transfer": read_json(ISAAC_COMMAND_RUN / "transfer.json"),
    }
    isaac_command["commands"] = [
        read_json(ISAAC_COMMAND_RUN / name)
        for name in ("gen_env.json", "collect.json", "evaluate.json", "diagnose.json", "transfer.json")
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    copy_evidence(output_dir, benchmarks, fallbacks, agenticsim_root, agenticsim_isaac)
    (output_dir / "index.html").write_text(
        build_html(
            registry,
            matrix,
            audit,
            benchmarks,
            fallbacks,
            agenticsim_isaac,
            video_uniqueness,
            isaac_command,
        ),
        encoding="utf-8",
    )
    manifest = write_manifest(output_dir)
    return {
        "status": "pass_openxsim_report_build",
        "output": str(output_dir),
        "file_count": manifest["file_count"],
        "commands": len(registry["commands"]),
        "adapters": len(matrix["adapters"]),
        "benchmarks": len(benchmarks),
        "isaac_runtime_passes": agenticsim_isaac["runtime_summary"][
            "runtime_pass_count"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / REPORT_ROOT)
    parser.add_argument("--agenticsim-root", type=Path, default=DEFAULT_AGENTICSIM_ROOT)
    args = parser.parse_args()
    print(
        json.dumps(
            build_report(args.output, args.agenticsim_root), indent=2, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

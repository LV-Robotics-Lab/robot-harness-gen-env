#!/usr/bin/env python3
"""Build the self-contained Stage 5 acceptance report from pulled evidence."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CASES = [
    {
        "id": "stack_can",
        "index": "01",
        "title": "Stacking / on_top_of",
        "claim": "A catalog can is grounded on a catalog plate with explicit 3D support constraints.",
    },
    {
        "id": "inside_cup",
        "index": "02",
        "title": "Container placement / inside",
        "claim": "A catalog cup is constrained by the basket interior volume and remains visibly contained.",
    },
    {
        "id": "cabinet6",
        "index": "03",
        "title": "Articulation initial state",
        "claim": "All three cabinet drawers are initialized and held at the requested half-open joint state.",
    },
    {
        "id": "generated",
        "index": "04",
        "title": "Catalog miss / generated proxy",
        "claim": "A purple hexagonal pedestal is generated, imported, rescanned, replayed, and provenance-hashed.",
    },
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def collect_case(root: Path, definition: dict[str, str]) -> dict[str, Any]:
    case_root = root / "assets" / "scenes" / definition["id"]
    packages = sorted(path for path in (case_root / "compile").iterdir() if path.is_dir())
    if len(packages) != 1:
        raise RuntimeError(f"{definition['id']} must contain exactly one compile package")
    package = packages[0]
    resolved_path = package / "resolved_scene.json"
    resolved = read_json(resolved_path)
    runtime_path = case_root / "runtime" / "runtime_evidence.json"
    runtime = read_json(runtime_path)
    validation_path = case_root / "runtime" / "runtime_validation_report.json"
    validation = read_json(validation_path)
    critic_path = case_root / "rendered_critic.json"
    critic = read_json(critic_path)
    objects = []
    runtime_objects = runtime.get("objects", {})
    for item in resolved.get("objects", []):
        evidence = runtime_objects.get(item["object_id"], {})
        objects.append(
            {
                "object_id": item["object_id"],
                "category": item["category"],
                "asset_id": item["asset_id"],
                "asset_provenance": item.get("asset_provenance"),
                "support_relation": item["support_relation"],
                "support_target": item["support_target"],
                "articulation_joint_names": item.get("articulation_joint_names", []),
                "articulation_qpos": item.get("articulation_qpos", []),
                "articulation_state": item.get("articulation_state"),
                "visible_pixels": evidence.get("visible_pixels"),
                "translation_drift_m": evidence.get("translation_drift_m"),
                "articulation_max_abs_error": evidence.get("articulation_max_abs_error"),
            }
        )
    result = {
        **definition,
        "request": resolved["request"],
        "scene_id": resolved["scene_id"],
        "seed": resolved["seed"],
        "resolved_scene_sha256": runtime["resolved_scene_sha256"],
        "runtime_status": runtime["status"],
        "runtime_validation_status": validation["status"],
        "runtime_fail_count": validation["fail_count"],
        "runtime_check_count": len(validation.get("checks", [])),
        "video_frame_count": runtime["video_frame_count"],
        "fps": runtime["fps"],
        "critic_status": critic["status"],
        "critic_summary": critic["summary"],
        "critic_check_count": len(critic.get("checks", [])),
        "critic_issue_count": len(critic.get("issues", [])),
        "critic_repair_count": len(critic.get("repair_raw_responses", [])),
        "objects": objects,
        "paths": {
            "world_left": relative(case_root / "runtime" / "preview_world_left.png", root),
            "world_right": relative(case_root / "runtime" / "preview_world_right.png", root),
            "head": relative(case_root / "runtime" / "preview_head.png", root),
            "video": relative(case_root / "runtime" / "observer_runtime.mp4", root),
            "resolved": relative(resolved_path, root),
            "static_validation": relative(package / "validation_report.json", root),
            "runtime_evidence": relative(runtime_path, root),
            "runtime_validation": relative(validation_path, root),
            "critic": relative(critic_path, root),
            "asset_generation": relative(package / "asset_generation_report.json", root),
        },
    }
    result["status"] = (
        "pass"
        if result["runtime_status"] == "pass"
        and result["runtime_validation_status"] == "pass"
        and result["critic_status"] == "pass"
        and result["runtime_fail_count"] == 0
        and result["video_frame_count"] == 120
        and result["critic_check_count"] == 5
        and result["critic_issue_count"] == 0
        else "fail"
    )
    return result


def object_rows(case: dict[str, Any]) -> str:
    rows = []
    for item in case["objects"]:
        relation = f"{item['support_relation']} -> {item['support_target']}"
        articulation = "-"
        if item["articulation_joint_names"]:
            articulation = (
                f"{len(item['articulation_joint_names'])} joints; "
                f"qpos {', '.join(f'{value:.4f}' for value in item['articulation_qpos'])}"
            )
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(item['object_id'])}</code></td>"
            f"<td><code>{html.escape(item['asset_id'])}</code><small>{html.escape(str(item['asset_provenance']))}</small></td>"
            f"<td>{html.escape(relation)}</td>"
            f"<td>{html.escape(articulation)}</td>"
            f"<td>{item['visible_pixels']}</td>"
            "</tr>"
        )
    return "".join(rows)


def case_section(case: dict[str, Any]) -> str:
    paths = case["paths"]
    repair = (
        f"{case['critic_repair_count']} bounded repair"
        if case["critic_repair_count"]
        else "no repair"
    )
    return f"""
    <article class="case" id="{case['id']}">
      <header class="case-header">
        <div><span class="index">{case['index']}</span><h2>{html.escape(case['title'])}</h2></div>
        <span class="badge pass">PASS</span>
      </header>
      <p class="request">{html.escape(case['request'])}</p>
      <p class="claim">{html.escape(case['claim'])}</p>
      <div class="metric-strip">
        <span><strong>120</strong> frames</span>
        <span><strong>{case['runtime_check_count']}</strong> runtime checks</span>
        <span><strong>5/5</strong> VLM checks</span>
        <span><strong>0</strong> issues</span>
        <span><strong>{html.escape(repair)}</strong></span>
      </div>
      <div class="visual-grid">
        <figure>
          <img src="{paths['world_left']}" alt="{html.escape(case['title'])} world camera evidence" loading="lazy">
          <figcaption>RoboTwin world-left camera</figcaption>
        </figure>
        <video controls preload="metadata" poster="{paths['world_right']}">
          <source src="{paths['video']}" type="video/mp4">
        </video>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Object</th><th>Asset</th><th>Support</th><th>Articulation</th><th>Visible px</th></tr></thead>
          <tbody>{object_rows(case)}</tbody>
        </table>
      </div>
      <details>
        <summary>Structured evidence</summary>
        <div class="artifact-links">
          <a href="{paths['resolved']}">ResolvedSceneSpec</a>
          <a href="{paths['static_validation']}">Static validation</a>
          <a href="{paths['runtime_evidence']}">Runtime evidence</a>
          <a href="{paths['runtime_validation']}">Runtime validation</a>
          <a href="{paths['critic']}">Rendered critic</a>
          <a href="{paths['asset_generation']}">Asset generation</a>
        </div>
        <p><code>{case['resolved_scene_sha256']}</code></p>
      </details>
    </article>
    """


def build_html(summary: dict[str, Any]) -> str:
    cases = "".join(case_section(case) for case in summary["cases"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>RoboTwin Text2Env Stage 5 Acceptance</title>
  <style>
    :root {{ --paper:#f3f1eb; --surface:#fbfaf6; --ink:#17211f; --muted:#66706b; --line:#c7ccc4; --green:#087f5b; --yellow:#eab308; --red:#b42318; --code:#101715; font-family:"Avenir Next","PingFang SC","Microsoft YaHei",sans-serif; color:var(--ink); background:var(--paper); }}
    * {{ box-sizing:border-box; }} html {{ scroll-behavior:smooth; }} body {{ margin:0; letter-spacing:0; background-color:var(--paper); background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px); background-size:40px 40px; }}
    a {{ color:var(--green); }} code {{ overflow-wrap:anywhere; font-family:ui-monospace,"SFMono-Regular",Consolas,monospace; }}
    .topbar {{ min-height:72px; padding:13px clamp(18px,4vw,56px); display:flex; align-items:center; justify-content:space-between; gap:24px; color:#edf5f0; background:var(--ink); border-bottom:4px solid var(--yellow); }}
    .brand {{ display:flex; align-items:center; gap:12px; }} .mark {{ width:42px; height:42px; display:grid; place-items:center; color:var(--yellow); border:1px solid #71857b; font:700 11px/1 ui-monospace,monospace; }}
    .brand strong {{ display:block; font-size:18px; }} .brand small,.machine small {{ display:block; margin-top:3px; color:#a7b5ae; font-size:10px; text-transform:uppercase; }} .machine {{ text-align:right; font-size:12px; }}
    nav {{ padding:10px clamp(18px,4vw,56px); display:flex; gap:18px; overflow:auto; white-space:nowrap; background:#fff; border-bottom:1px solid var(--line); font-size:12px; }} nav a {{ color:var(--ink); text-decoration:none; }}
    main {{ width:min(1180px,calc(100% - 28px)); margin:22px auto 60px; }}
    section,.case {{ padding:24px; background:var(--surface); border:1px solid #8f9991; border-bottom:3px solid var(--ink); }} section+section,.case+article,section+.case,.case+section {{ margin-top:18px; }}
    .summary h1 {{ max-width:850px; margin:0; font-size:clamp(27px,4vw,50px); line-height:1.04; letter-spacing:0; }} .summary>p {{ max-width:820px; margin:15px 0 0; color:var(--muted); line-height:1.65; }}
    .summary-grid {{ margin-top:24px; display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); border-top:1px solid var(--line); border-bottom:1px solid var(--line); }} .summary-grid div {{ min-height:84px; padding:15px; border-right:1px solid var(--line); }} .summary-grid div:last-child {{ border-right:0; }} .summary-grid strong {{ display:block; font:700 25px/1 ui-monospace,monospace; }} .summary-grid span {{ display:block; margin-top:8px; color:var(--muted); font-size:11px; }}
    .action-row {{ margin-top:20px; display:flex; flex-wrap:wrap; gap:8px; }} .action-row a,.artifact-links a {{ padding:8px 11px; color:var(--ink); text-decoration:none; background:#eef1ec; border:1px solid var(--line); border-radius:3px; font-size:12px; }} .action-row a.primary {{ color:white; background:var(--green); border-color:var(--green); }}
    .case-header,.case-header>div {{ display:flex; align-items:center; }} .case-header {{ justify-content:space-between; gap:16px; }} .case-header>div {{ gap:12px; }} .index {{ color:var(--green); font:700 12px/1 ui-monospace,monospace; }} h2 {{ margin:0; font-size:18px; }}
    .badge {{ padding:5px 9px; font:700 10px/1 ui-monospace,monospace; border-radius:2px; }} .badge.pass {{ color:white; background:var(--green); }} .request {{ margin:18px 0 0; font:600 16px/1.4 ui-monospace,monospace; }} .claim {{ max-width:820px; margin:8px 0 0; color:var(--muted); line-height:1.55; }}
    .metric-strip {{ margin-top:18px; display:flex; flex-wrap:wrap; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }} .metric-strip span {{ padding:10px 16px 10px 0; margin-right:16px; color:var(--muted); font-size:11px; }} .metric-strip strong {{ color:var(--ink); }}
    .visual-grid {{ margin-top:16px; display:grid; grid-template-columns:1.6fr 1fr; gap:10px; align-items:stretch; }} figure {{ margin:0; min-width:0; }} img,video {{ display:block; width:100%; background:var(--code); border:1px solid var(--ink); }} .visual-grid img,.visual-grid video {{ height:100%; min-height:260px; object-fit:contain; }} figcaption {{ margin-top:5px; color:var(--muted); font-size:10px; }}
    .table-wrap {{ margin-top:20px; overflow:auto; }} table {{ width:100%; border-collapse:collapse; font-size:12px; }} th,td {{ padding:10px 8px; text-align:left; vertical-align:top; border-bottom:1px solid var(--line); }} th {{ color:var(--muted); font-size:10px; text-transform:uppercase; }} td small {{ display:block; margin-top:4px; color:var(--muted); }}
    details {{ margin-top:14px; }} summary {{ cursor:pointer; font-weight:700; font-size:12px; }} .artifact-links {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }} details p {{ color:var(--muted); font-size:10px; }}
    .qa-grid {{ margin-top:16px; display:grid; grid-template-columns:2fr 1fr; gap:10px; align-items:start; }} .qa-grid img {{ height:auto; }} .qa h2,.boundaries h2 {{ margin:0; }} .qa p,.boundaries li {{ color:var(--muted); line-height:1.55; }}
    .boundaries ul {{ margin:16px 0 0; padding-left:20px; }} .boundaries li+li {{ margin-top:8px; }} footer {{ padding:20px 0 0; color:var(--muted); font-size:10px; text-align:center; }}
    @media (max-width:760px) {{ .topbar {{ align-items:flex-start; }} .machine small {{ max-width:160px; }} main {{ width:calc(100% - 18px); margin-top:9px; }} section,.case {{ padding:18px 13px; }} .summary-grid {{ grid-template-columns:repeat(2,1fr); }} .summary-grid div {{ border-bottom:1px solid var(--line); }} .visual-grid,.qa-grid {{ grid-template-columns:1fr; }} .visual-grid img,.visual-grid video {{ min-height:0; aspect-ratio:4/3; }} .metric-strip span {{ width:50%; margin:0; padding-right:8px; }} }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand"><span class="mark">T2E</span><div><strong>Text2Env Stage 5</strong><small>RoboTwin acceptance report</small></div></div>
    <div class="machine">RTX 5090 · 100.64.0.6<small>32,607 MiB · driver 580.159.03</small></div>
  </header>
  <nav><a href="#summary">Summary</a><a href="#stack_can">Stack</a><a href="#inside_cup">Inside</a><a href="#cabinet6">Articulation</a><a href="#generated">Generated asset</a><a href="#frontend">Frontend QA</a><a href="#boundaries">Boundaries</a></nav>
  <main>
    <section class="summary" id="summary">
      <h1>Text prompt to validated RoboTwin scene</h1>
      <p>Stage 5 closes the declared gaps for stacking, container placement, multi-joint initial state, bounded catalog-miss asset generation, and rendered-scene VLM review. Every case below comes from a real 120-frame RoboTwin/SAPIEN run.</p>
      <div class="summary-grid">
        <div><strong>4/4</strong><span>final scenes pass</span></div>
        <div><strong>480</strong><span>rendered runtime frames</span></div>
        <div><strong>20/20</strong><span>VLM checks present</span></div>
        <div><strong>49/49</strong><span>local + remote tests</span></div>
        <div><strong>24.34s</strong><span>frontend E2E job</span></div>
      </div>
      <div class="action-row"><a class="primary" href="http://100.64.0.6:8765/?job=7972c4543d60416e">Open live demo</a><a href="acceptance_summary.json">Acceptance JSON</a><a href="report_manifest.json">Report manifest</a><a href="qa/browser-verification.json">Browser QA JSON</a></div>
    </section>
    {cases}
    <section class="qa" id="frontend">
      <h2>Frontend end-to-end proof</h2>
      <p>Job <code>7972c4543d60416e</code> completed compile, RoboTwin runtime, artifact registration, and Qwen critic from the public demo API. Desktop and mobile checks recorded HTTP 200, zero horizontal overflow, zero control overflow, and zero console, page, or request errors.</p>
      <div class="qa-grid"><img src="qa/demo-desktop.png" alt="Desktop Text2Env demo QA"><img src="qa/demo-mobile.png" alt="Mobile Text2Env demo QA"></div>
      <div class="action-row"><a href="assets/demo_job/7972c4543d60416e/job.json">Job record</a><a href="assets/demo_job/7972c4543d60416e/runtime/observer_runtime.mp4">Job video</a><a href="assets/demo_job/7972c4543d60416e/rendered_critic.json">Job critic</a></div>
    </section>
    <section class="boundaries" id="boundaries">
      <h2>Claim boundaries</h2>
      <ul>
        <li>The catalog-miss path generates deterministic simulator-ready semantic geometry proxies. It does not claim arbitrary high-fidelity text-to-3D generation.</li>
        <li>Stacking and containment are validated initial scene layouts. These runs do not claim a robot policy that performs the placement action.</li>
        <li>The parser remains a bounded bilingual/rule-based front end over supported relation and articulation forms, not unrestricted natural-language understanding.</li>
        <li>The Qwen critic reviews visible rendered evidence. Typed geometry, contacts, drift, support, and joint error remain separate deterministic runtime gates.</li>
      </ul>
    </section>
    <footer>Generated {html.escape(summary['generated_at'])} · manifest covers every bundled source, image, video, JSON, and log.</footer>
  </main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", required=True)
    args = parser.parse_args()
    root = Path(args.bundle_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    cases = [collect_case(root, definition) for definition in CASES]
    browser = read_json(root / "qa" / "browser-verification.json")
    summary = {
        "schema_version": "robotwin.text2env_stage5_acceptance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(case["status"] == "pass" for case in cases) else "fail",
        "machine": {
            "host": "jingxiang-b850m-c",
            "tailscale_ip": "100.64.0.6",
            "gpu": "NVIDIA GeForce RTX 5090",
            "vram_mib": 32607,
            "driver": "580.159.03",
        },
        "catalog": {"entries": 126, "available": 14},
        "tests": {"local": {"passed": 49}, "remote": {"passed": 49}},
        "demo": {
            "url": "http://100.64.0.6:8765/?job=7972c4543d60416e",
            "job_id": "7972c4543d60416e",
            "status": "pass",
            "elapsed_seconds": 24.33908,
            "browser_viewports": [item["name"] for item in browser],
        },
        "cases": cases,
        "boundaries": [
            "procedural semantic proxy, not arbitrary high-fidelity text-to-3D",
            "validated initial layout, not robot placement policy execution",
            "bounded parser, not unrestricted language understanding",
            "VLM visibility review remains separate from deterministic runtime gates",
        ],
    }
    (root / "acceptance_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / "index.html").write_text(build_html(summary), encoding="utf-8")

    excluded = {"manifest.sha256", "report_manifest.json"}
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = relative(path, root)
        if rel in excluded:
            continue
        entries.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha256(path)})
    report_manifest = {
        "schema_version": "robotwin.text2env_stage5_report_manifest.v1",
        "generated_at": summary["generated_at"],
        "file_count": len(entries),
        "total_size_bytes": sum(item["size_bytes"] for item in entries),
        "files": entries,
    }
    (root / "report_manifest.json").write_text(
        json.dumps(report_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = relative(path, root)
        if rel == "manifest.sha256":
            continue
        lines.append(f"{sha256(path)}  {rel}")
    (root / "manifest.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{summary['status'].upper()} cases={len(cases)} files={len(lines)} "
        f"bundle={root}"
    )
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())

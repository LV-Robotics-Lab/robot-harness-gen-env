#!/usr/bin/env python3
"""One-off (work/oneoff/): 属性检索矩阵 —— 颜色 × 材质 × 类目 全流程实跑。

每个 prompt 走完整链路（scene_acquire -> 求解 -> 运行时物理校验），记录：
  · 是否落到已有资产（复用）还是触发采购（新资产入库）
  · 选中的资产/模型、它被实测出的颜色、与请求颜色是否一致
  · 运行时 fail_count

判据：颜色/材质请求必须体现在"选中的是哪一个资产"上，而不只是运行时把它
染成那个颜色 —— 后者上游一直会做，前者才是这次要验证的检索能力。
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")
WT = Path("/home/jingxiang/yuxin/wt-main/1_asset_reuse")
UP = Path("/home/jingxiang/yuxin/env-gen-github")
PY = sys.executable
OUT = Path("/home/jingxiang/yuxin/attr_matrix_out")
CATALOG = DEV / "data/scene_gen_ext/asset_catalog.json"
ATTRS = DEV / "data/scene_gen_ext/asset_attributes.json"

ENV = {
    **os.environ,
    "PYTHONPATH": f"{WT}:{WT.parent}/shared/openxsim/source/agenticsim:{UP}",
}


def catalog_colors():
    """asset_id -> 实测颜色（用于判断"选中的资产是否真是那个颜色"）。"""
    if not ATTRS.exists():
        return {}
    data = json.loads(ATTRS.read_text()).get("models", {})
    out = {}
    for aid, models in data.items():
        for mid, row in models.items():
            if row.get("colors"):
                out[(aid, int(mid))] = row["colors"]
    return out


def run_case(tag, prompt, want_color=None, want_material=None, do_runtime=True):
    t0 = time.time()
    d = OUT / tag
    shutil.rmtree(d, ignore_errors=True)
    row = {
        "tag": tag,
        "prompt": prompt,
        "want_color": want_color,
        "want_material": want_material,
    }
    r = subprocess.run(
        [
            PY,
            str(WT / "scripts/1_search/scene_acquire.py"),
            "--prompt",
            prompt,
            "--seed",
            "42",
            "--catalog",
            str(CATALOG),
            "--providers",
            str(WT / "configs/providers.json"),
            "--dev-root",
            str(DEV),
            "--out",
            str(d),
        ],
        cwd=str(WT),
        capture_output=True,
        text=True,
        timeout=2400,
        env=ENV,
    )
    row["acquire_rc"] = r.returncode
    tail = (r.stdout + r.stderr)[-500:]
    if "Traceback" in r.stdout + r.stderr:
        row["crash"] = tail
    cov = d / "coverage_report.json"
    if cov.exists():
        objs = json.loads(cov.read_text())["objects"]
        row["coverage"] = [
            (o["object_id"], o["status"], o.get("asset_id")) for o in objs
        ]
        row["acquired"] = [o.get("asset_id") for o in objs if o.get("acquired")]
    scenes = sorted(d.glob("scenes/*/resolved_scene.json"))
    if scenes:
        spec = json.loads(scenes[-1].read_text())
        picked = []
        for o in spec["objects"]:
            picked.append(
                {
                    "object_id": o["object_id"],
                    "asset_id": o["asset_id"],
                    "model_id": o["model_id"],
                    "runtime_color": o.get("color"),
                }
            )
        row["picked"] = picked
        colors = catalog_colors()
        if want_color and picked:
            measured = colors.get((picked[0]["asset_id"], picked[0]["model_id"]))
            row["picked_measured_colors"] = measured
            row["color_truth"] = (
                "unknown"
                if not measured
                else "match"
                if want_color in measured
                else "mismatch"
            )
    if r.returncode == 0 and scenes and do_runtime:
        try:
            rr = subprocess.run(
                [
                    PY,
                    str(UP / "script/run_scene_runtime.py"),
                    "--robotwin-root",
                    str(DEV / "data/robotwin_shadow"),
                    "--resolved-scene",
                    str(scenes[-1]),
                    "--asset-catalog",
                    str(CATALOG),
                    "--out-dir",
                    str(d / "runtime"),
                    "--settle-steps",
                    "1500",
                    "--contact-window-steps",
                    "60",
                    "--video-frames",
                    "6",
                    "--fps",
                    "6",
                ],
                cwd=str(UP),
                capture_output=True,
                text=True,
                timeout=1200,
                env={**ENV, "CUDA_LAUNCH_BLOCKING": "1"},
            )
            rep = d / "runtime/runtime_validation_report.json"
            if rep.exists():
                rj = json.loads(rep.read_text())
                row["runtime_fail"] = rj["fail_count"]
                row["failing"] = [
                    c["name"]
                    for c in rj["checks"]
                    if c["status"] not in ("pass", "not_applicable")
                ]
            else:
                row["runtime_fail"] = -1
                row["crash_rt"] = (rr.stdout + rr.stderr)[-300:]
        except subprocess.TimeoutExpired:
            row["runtime_fail"] = -2
    row["seconds"] = round(time.time() - t0, 1)
    shutil.rmtree(d / "acquire", ignore_errors=True)
    shutil.rmtree(d / "runtime" / "observer_runtime.mp4", ignore_errors=True)
    return row


COLORS = ["red", "blue", "green", "yellow", "white", "black", "brown", "orange"]
MATERIALS = ["wood", "metal", "plastic", "glass", "ceramic"]
BASE_NOUNS = ["cup", "bowl", "bottle", "box", "block"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    rp = OUT / "attr_matrix_report.json"
    prev = {}
    if rp.exists():
        try:
            prev = {r["tag"]: r for r in json.loads(rp.read_text())["rows"]}
            print(f"续跑：沿用 {len(prev)} 行", flush=True)
        except Exception:  # noqa: BLE001
            prev = {}

    def flush():
        rp.write_text(json.dumps({"rows": rows}, indent=1, ensure_ascii=False))

    cases = []
    # 1) 颜色 × 常见类目（英文）
    for c in COLORS:
        for n in BASE_NOUNS[:3]:
            cases.append((f"C_{c}_{n}", f"Place a {c} {n} on the table.", c, None))
    # 2) 材质 × 类目
    for m in MATERIALS:
        for n in BASE_NOUNS[:2]:
            cases.append((f"M_{m}_{n}", f"Place a {m} {n} on the table.", None, m))
    # 3) 颜色+材质组合
    for c, m, n in [
        ("red", "plastic", "cup"),
        ("brown", "wood", "bowl"),
        ("white", "ceramic", "bowl"),
        ("black", "metal", "box"),
        ("blue", "glass", "bottle"),
    ]:
        cases.append((f"CM_{c}_{m}_{n}", f"Place a {c} {m} {n} on the table.", c, m))
    # 4) 中文
    for zh, c, n in [
        ("把红色的杯子放在桌子上", "red", "cup"),
        ("把蓝色的碗放在桌子上", "blue", "bowl"),
        ("把木质的碗放在桌子上", None, "wood"),
    ]:
        cases.append((f"ZH_{c or 'wood'}_{n}", zh, c, None if c else "wood"))
    # 5) 关系式带属性
    cases.append(
        ("REL_red_apple_bowl", "Put a red apple inside the bowl.", "red", None)
    )
    cases.append(
        (
            "REL_blue_cup_table",
            "Place a blue cup to the left of the plate.",
            "blue",
            None,
        )
    )

    print(f"矩阵：{len(cases)} 条", flush=True)
    for i, (tag, prompt, wc, wm) in enumerate(cases):
        if tag in prev:
            rows.append(prev[tag])
            flush()
            continue
        row = run_case(tag, prompt, wc, wm)
        rows.append(row)
        flush()
        ok = row.get("runtime_fail") == 0
        print(
            f"[{i + 1}/{len(cases)}] {tag}: rc={row['acquire_rc']} "
            f"rt={row.get('runtime_fail', '-')} color={row.get('color_truth', '-')} "
            f"{'OK' if ok else '<<'} ({row['seconds']}s)",
            flush=True,
        )
    n_green = sum(1 for r in rows if r.get("runtime_fail") == 0)
    n_cmatch = sum(1 for r in rows if r.get("color_truth") == "match")
    n_cmis = sum(1 for r in rows if r.get("color_truth") == "mismatch")
    print(
        f"\nATTRMATRIXDONE 总{len(rows)} 全绿{n_green} 颜色命中{n_cmatch} 颜色错配{n_cmis}",
        flush=True,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the overrides fragment (external_overrides_fragment_merged.yml
shape) from the authoritative per-asset ledgers under --library-dir.

Filtering: a model is included only if its latest (backend=sapien, check=X)
verification is present, digest-fresh, and verdict == pass, where X is
kind-aware: asset-level kind=="articulated" ledgers (s13b pipeline) check
"joint_sweep" -- its 120-step settle-then-sweep already subsumes a bare
settle check -- while kind=="rigid" ledgers check "settle" as before.
lib.ledger.latest_verification already encodes "latest-per-(backend,check)
and digest still matches reps_digest" (stale digest -> None); this module
never re-derives that with any(v["verdict"] == "pass" for v in verification).
--license-gate additionally requires source.license.status == "declared".

PyYAML is not used for the write path -- the output is hand-formatted to
match the existing merged fragment's exact style (2-space indent, flow-style
lists, quoted model-id keys) so the generator doesn't introduce style drift.
lib/ stays pure stdlib; this script is the (only) place PyYAML would be used,
and even here only the test round-trips through yaml.safe_load to check the
result, not this module.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import ledger


def _default_pose(model):
    poses = model["physical"]["conventions"]["stable_poses"]
    for pose in poses:
        if pose.get("is_default"):
            return pose
    raise ValueError(f"model {model.get('model_id')!r} has no is_default stable pose")


def _project_model(model):
    conv = model["physical"]["conventions"]
    pose = _default_pose(model)
    out = {
        "stable_pose_id": pose["pose_id"],
        "stable_orientation_wxyz": pose["orientation_wxyz"],
        "z_policy": conv["z_policy"],
        "footprint_shape": conv["footprint_shape"],
    }
    if conv.get("is_static") is True:
        out["is_static"] = True
    return out


def _project_asset(led, models_out, measured_colors):
    out = {
        "category": led["category"],
        "aliases": list(led["semantics"]["aliases"]),
        "models": models_out,
    }
    colors = led["semantics"].get("colors") or []
    if colors:
        out["colors"] = list(colors)
    elif measured_colors is not None:
        # v3: measured appearance is the authority for colour when no hand
        # declaration exists. Same publication rule the shadow build used
        # when this fact lived in a sidecar file: publish only when every
        # projected model agrees -- entry.colors is asset-level upstream and
        # a non-empty value REJECTS mismatching queries, so a multi-colour
        # asset claiming one colour would hide its other models forever.
        out["colors"] = list(measured_colors)
    # materials: projected since v3. Through v2 this line was missing -- the
    # upstream grounder consults entry.materials for material queries, so a
    # "wooden bowl" could never match ANY external asset (measured
    # 2026-08-15, dialectic round).
    materials = led["semantics"].get("materials") or []
    if materials:
        out["materials"] = list(materials)
    return out


def _agreed_measured_colors(led, model_ids):
    """The colour every projected model agrees on, else None."""
    seen = []
    for model in led.get("models", []):
        if str(model.get("model_id")) not in model_ids:
            continue
        colors = (model.get("appearance") or {}).get("colors_measured")
        seen.append(tuple(colors) if colors else None)
    if not seen or any(c is None for c in seen):
        return None
    return list(seen[0]) if len(set(seen)) == 1 else None


def generate(library_dir, *, license_gate=False):
    """Project the per-asset ledgers under library_dir into an overrides
    fragment dict, filtering on latest-verification-pass (+ optional
    license gate). The verification check is kind-aware: "joint_sweep" for
    asset-level kind=="articulated" ledgers, "settle" for kind=="rigid"
    ledgers (see module docstring). Returns (fragment, stats).

    stats["unknown_license_models"] counts verification-passing models
    whose source.license.status != "declared" -- computed the same way
    regardless of license_gate (it answers "how many need a license
    decision", not "how many the gate happened to eat this run")."""
    library_dir = Path(library_dir)
    frag = {}
    stats = {"unknown_license_models": 0}

    for ledger_file in sorted(library_dir.glob("*/ledger.json")):
        asset_key = ledger_file.parent.name
        led = json.loads(ledger_file.read_text())
        check = "joint_sweep" if led.get("kind") == "articulated" else "settle"
        models_out = {}
        for model in led.get("models", []):
            latest = ledger.latest_verification(model, "sapien", check)
            if latest is None or latest.get("verdict") != "pass":
                continue
            license_status = model.get("source", {}).get("license", {}).get("status")
            if license_status != "declared":
                stats["unknown_license_models"] += 1
            if license_gate and license_status != "declared":
                continue
            models_out[str(model["model_id"])] = _project_model(model)
        if models_out:
            measured = _agreed_measured_colors(led, set(models_out))
            frag[asset_key] = _project_asset(led, models_out, measured)

    return frag, stats


def _fmt_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _fmt_flow_list(items):
    return "[" + ", ".join(_fmt_scalar(v) for v in items) + "]"


def write_yaml(frag, path):
    """Hand-formatted writer (deliberately not yaml.dump, to avoid style
    drift from the existing merged fragment): 2-space indent per level,
    flow-style `[a, b]` lists, quoted `"<model_id>"` keys, `colors` omitted
    when empty. Semantics are what matter -- yaml.safe_load(output) should
    equal the equivalent hand-built dict."""
    lines = []
    for asset_key in sorted(frag):
        entry = frag[asset_key]
        lines.append(f"  {asset_key}:")
        lines.append(f"    category: {entry['category']}")
        lines.append(f"    aliases: {_fmt_flow_list(entry['aliases'])}")
        if entry.get("colors"):
            lines.append(f"    colors: {_fmt_flow_list(entry['colors'])}")
        if entry.get("materials"):
            lines.append(f"    materials: {_fmt_flow_list(entry['materials'])}")
        lines.append("    models:")
        for model_id in sorted(entry["models"], key=int):
            m = entry["models"][model_id]
            lines.append(f'      "{model_id}":')
            lines.append(f"        stable_pose_id: {m['stable_pose_id']}")
            lines.append(
                "        stable_orientation_wxyz: "
                f"{_fmt_flow_list(m['stable_orientation_wxyz'])}"
            )
            lines.append(f"        z_policy: {m['z_policy']}")
            lines.append(f"        footprint_shape: {m['footprint_shape']}")
            if m.get("is_static"):
                lines.append("        is_static: true")

    text = "\n".join(lines) + ("\n" if lines else "")
    Path(path).write_text(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--license-gate", action="store_true")
    args = parser.parse_args(argv)

    frag, stats = generate(args.library_dir, license_gate=args.license_gate)
    write_yaml(frag, args.out)

    n = stats["unknown_license_models"]
    if args.license_gate:
        print(
            f"WARNING: {n} models with unknown license excluded by license gate",
            file=sys.stderr,
        )
    else:
        print(f"WARNING: {n} models with unknown license in view", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
# probe s7: validate the adaptive settle horizon, both directions.
#
# A) regression -- flag off must reproduce the fixed-horizon numbers exactly
#    (321_dustbin late_rot 0.5861 / still_moving True at 900 steps).
# B) effect -- flag on must rescue the objects that were only slow
#    (321_dustbin, 035_apple) and must NOT rescue the ones that keep moving
#    (002_bowl, 021_cup, 304_bottle). Reporting only one side would be a
#    guaranteed false win.
set -uo pipefail
R=/home/yuhang/workspace/robot-harness-gen-env
A=$R/self_improving/asset_pipeline/active
P=$A/work/probe_20260818_dustbin_render
CAT=$A/data/scene_gen_ext/asset_catalog.json
SHADOW=$A/data/robotwin_shadow
PY=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
OUT=$P/out/s7
mkdir -p "$OUT"

SCENES=(
  "dustbin|$P/out/s2/scenes/place_a_dustbin_on_the_table_f70e98a43e/resolved_scene.json"
  "apple_basket|$A/results/web_runs/20260812_200211_put_an_apple_inside_the_basket"
  "bottle_bowl|$A/results/web_runs/20260812_200304_place_a_bottle_near_the_bowl"
  "mug_laptop|$A/results/web_runs/20260812_195926_put_a_mug_on_the_laptop"
)

cd "$R"
for ENTRY in "${SCENES[@]}"; do
  TAG="${ENTRY%%|*}"; LOC="${ENTRY#*|}"
  if [ -f "$LOC" ]; then RS="$LOC"; else RS=$(find "$LOC" -name resolved_scene.json | head -1); fi
  [ -z "${RS:-}" ] && { echo "NO_SCENE $TAG"; continue; }
  for MODE in off on; do
    if [ "$MODE" = off ]; then EXTRA=0; else EXTRA=3600; fi
    D="$OUT/${TAG}_${MODE}"
    mkdir -p "$D"
    CUDA_LAUNCH_BLOCKING=1 $PY script/run_scene_runtime.py \
      --robotwin-root "$SHADOW" --resolved-scene "$RS" --asset-catalog "$CAT" \
      --out-dir "$D" --settle-steps 900 --contact-window-steps 120 \
      --settle-converge-max "$EXTRA" \
      --video-frames 12 --fps 12 > "$D/run.log" 2>&1
    $PY - "$D/runtime_evidence.json" "$TAG" "$MODE" <<'EOF'
import json, sys
try:
    ev = json.load(open(sys.argv[1]))
except Exception as exc:
    print("MISSING %s %s (%s)" % (sys.argv[2], sys.argv[3], exc)); raise SystemExit
extra = ev.get("settle_extra_steps")
for oid, o in (ev.get("objects") or {}).items():
    print("V7 %-12s %-3s obj=%-10s asset=%-16s late_rot=%9.4f late_trans=%.6f moving=%-5s extra=%s"
          % (sys.argv[2], sys.argv[3], oid, o.get("asset_id"),
             o.get("late_window_rotation_deg", -1), o.get("late_window_translation_m", -1),
             o.get("still_moving"), extra))
EOF
  done
done
echo "S7_DONE"

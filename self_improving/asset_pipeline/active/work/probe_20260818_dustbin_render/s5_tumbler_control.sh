#!/usr/bin/env bash
# probe s5: the true-negative control the fix argument still lacks.
#
# s2 showed a decaying rocker (321_dustbin) drops under the gate when the
# settle horizon triples. That alone is a one-sided result: it would also be
# consistent with "a longer horizon lets everything pass". This replays scenes
# whose objects genuinely tumble (late_rot 73-168 deg, late_trans 27-358x the
# threshold) at the same two horizons. If they stay flagged at 2700, a longer
# horizon rescues the converging without rescuing the moving -- which is what a
# fix has to prove.
set -uo pipefail
R=/home/yuhang/workspace/robot-harness-gen-env
A=$R/self_improving/asset_pipeline/active
P=$A/work/probe_20260818_dustbin_render
CAT=$A/data/scene_gen_ext/asset_catalog.json
SHADOW=$A/data/robotwin_shadow
PY=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
OUT=$P/out/s5
mkdir -p "$OUT"

SCENES=(
  "$A/results/web_runs/20260812_200211_put_an_apple_inside_the_basket"
  "$A/results/web_runs/20260812_200304_place_a_bottle_near_the_bowl"
  "$A/results/web_runs/20260812_195926_put_a_mug_on_the_laptop"
)

cd "$R"
for SD in "${SCENES[@]}"; do
  RS=$(find "$SD" -name resolved_scene.json 2>/dev/null | head -1)
  [ -z "$RS" ] && { echo "NO_SCENE $SD"; continue; }
  TAG=$(basename "$SD")
  for STEPS in 900 2700; do
    D="$OUT/${TAG}_${STEPS}"
    mkdir -p "$D"
    CUDA_LAUNCH_BLOCKING=1 $PY script/run_scene_runtime.py \
      --robotwin-root "$SHADOW" --resolved-scene "$RS" --asset-catalog "$CAT" \
      --out-dir "$D" --settle-steps "$STEPS" --contact-window-steps 120 \
      --video-frames 12 --fps 12 > "$D/run.log" 2>&1
    $PY - "$D/runtime_evidence.json" "$TAG" "$STEPS" <<'EOF'
import json, sys
try:
    ev = json.load(open(sys.argv[1]))
except Exception as exc:
    print("EVIDENCE_MISSING %s steps=%s (%s)" % (sys.argv[2], sys.argv[3], exc)); raise SystemExit
for oid, o in (ev.get("objects") or {}).items():
    print("CTRL %s steps=%s obj=%s asset=%s late_rot=%.4f late_trans=%.6f still_moving=%s"
          % (sys.argv[2][:38], sys.argv[3], oid, o.get("asset_id"),
             o.get("late_window_rotation_deg", -1), o.get("late_window_translation_m", -1),
             o.get("still_moving")))
EOF
  done
done
echo "S5_DONE"

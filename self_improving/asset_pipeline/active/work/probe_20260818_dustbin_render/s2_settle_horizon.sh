#!/usr/bin/env bash
# probe s2: is a `still_moving` flag a real "never settles", or just a
# premature cutoff?
#
# Ground truth by construction: run the SAME single-asset scene at the
# production settle horizon (900 steps) and at 3x that (2700). An object that
# is genuinely tumbling keeps a large late-window rotation at both horizons; an
# object that was merely still decaying at 900 falls under the gate at 2700.
#
# Both directions are measured on purpose: BORDERLINE assets (flagged today)
# tell us about false positives, CONTROL assets (passing today, incl. one real
# tumbler) tell us we are not about to open the gate for genuinely moving ones.
set -uo pipefail
R=/home/yuhang/workspace/robot-harness-gen-env
A=$R/self_improving/asset_pipeline/active
P=$A/work/probe_20260818_dustbin_render
CAT=$A/data/scene_gen_ext/asset_catalog.json
SHADOW=$A/data/robotwin_shadow
PY=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
OUT=$P/out/s2
mkdir -p "$OUT"

# category prompts -> the asset each grounds to is recorded from resolved_scene
PROMPTS=(
  "Place a dustbin on the table."
  "Place a bottle on the table."
  "Place a sleigh on the table."
  "Place a teddy bear on the table."
  "Place a panel on the table."
  "Place a ceiling fan on the table."
  "Place a cup on the table."
  "Place a bowl on the table."
)

cd "$R"
for PROMPT in "${PROMPTS[@]}"; do
  SLUG=$(echo "$PROMPT" | tr -cd '[:alnum:] ' | tr ' ' '_' | cut -c1-40)
  $PY script/generate_scene.py --prompt "$PROMPT" --seed 42 \
      --asset-catalog "$CAT" --out-root "$OUT/scenes" > "$OUT/gen_$SLUG.log" 2>&1
  SCENE=$(grep -oE "scene_id=[^ ]+" "$OUT/gen_$SLUG.log" | head -1 | cut -d= -f2)
  if [ -z "$SCENE" ]; then echo "GEN_FAIL $PROMPT"; continue; fi
  ASSET=$($PY -c "import json;d=json.load(open('$OUT/scenes/$SCENE/resolved_scene.json'));print(d['objects'][0]['asset_id'])" 2>/dev/null)
  for STEPS in 900 2700; do
    D="$OUT/run_${ASSET}_${STEPS}"
    mkdir -p "$D"
    CUDA_LAUNCH_BLOCKING=1 $PY script/run_scene_runtime.py \
      --robotwin-root "$SHADOW" \
      --resolved-scene "$OUT/scenes/$SCENE/resolved_scene.json" \
      --asset-catalog "$CAT" --out-dir "$D" \
      --settle-steps "$STEPS" --contact-window-steps 120 \
      --video-frames 12 --fps 12 > "$D/run.log" 2>&1
    $PY - "$D/runtime_evidence.json" "$ASSET" "$STEPS" <<'EOF'
import json, sys
try:
    ev = json.load(open(sys.argv[1]))
except Exception as exc:
    print("EVIDENCE_MISSING %s steps=%s (%s)" % (sys.argv[2], sys.argv[3], exc)); raise SystemExit
for oid, o in (ev.get("objects") or {}).items():
    print("ROW %s steps=%s late_rot=%.4f late_trans=%.6f total_rot=%.3f still_moving=%s"
          % (sys.argv[2], sys.argv[3], o.get("late_window_rotation_deg", -1),
             o.get("late_window_translation_m", -1), o.get("rotation_drift_deg", -1),
             o.get("still_moving")))
EOF
  done
done
echo "S2_DONE"

#!/usr/bin/env bash
# Mask RoboTwin assets (external-only catalog): each promptable category must
# ground to OUR asset and pass replay + full validation.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ACTIVE_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
REPO_ROOT=$(cd -- "$ACTIVE_ROOT/../../.." && pwd)
PY=${SAPIEN_PYTHON:-python3}
DEV=${ASSET_PIPELINE_ROOT:-$ACTIVE_ROOT}
UP=${GEN_ENV_ROOT:-$REPO_ROOT}
SHADOW=${ROBOTWIN_SHADOW_ROOT:-$DEV/data/robotwin_shadow}
CAT=${ASSET_CATALOG_EXTERNAL_ONLY:-$DEV/data/scene_gen_ext/asset_catalog_external_only.json}
OUT=${S12_OUT:-$DEV/results/_test/$(date +%Y%m%d)_ext_only_e2e}
mkdir -p "$OUT/scenes"
PROMPTS=("Place a can on the table." "Place a box on the table." "Place a bottle on the table." "Place a bowl on the table." "Place a block on the table.")
pass=0; total=0
cd "$UP"
for PROMPT in "${PROMPTS[@]}"; do
  total=$((total+1))
  $PY script/generate_scene.py --prompt "$PROMPT" --seed 42 --asset-catalog "$CAT" --out-root "$OUT/scenes" > /tmp/gen.out 2>&1
  SCENE=$(grep -oE "scene_id=[^ ]+" /tmp/gen.out | head -1 | cut -d= -f2)
  if [ -z "$SCENE" ]; then
    echo "RESULT prompt=[$PROMPT] asset=GEN_FAIL runtime=SKIP val=SKIP ok=no"
    continue
  fi
  ASSET=$($PY -c "import json;d=json.load(open(\"$OUT/scenes/$SCENE/resolved_scene.json\"));print(d[\"objects\"][0][\"asset_id\"])" 2>/dev/null || echo COMPILE_FAIL)
  mkdir -p "$OUT/runtime/$SCENE"
  # CUDA_LAUNCH_BLOCKING: vendored curobo's planner warmup crashes on sm_120
  # under async launches (2026-08-13)
  CUDA_LAUNCH_BLOCKING=1 $PY script/run_scene_runtime.py --robotwin-root "$SHADOW" --resolved-scene "$OUT/scenes/$SCENE/resolved_scene.json" --asset-catalog "$CAT" --out-dir "$OUT/runtime/$SCENE" --settle-steps 900 --contact-window-steps 120 --video-frames 120 --fps 12 --settle-converge-max 1800 > /tmp/rt.out 2>&1
  RT=$(grep -oE "^PASS scene=[^ ]+ fail=0" /tmp/rt.out | head -1)
  $PY -m scene_gen.validator --resolved-scene "$OUT/scenes/$SCENE/resolved_scene.json" --asset-catalog "$CAT" --package-root "$OUT/scenes/$SCENE" --runtime-evidence "$OUT/runtime/$SCENE/runtime_evidence.json" --require-runtime --out "$OUT/runtime/$SCENE/validation_full.json" > /tmp/val.out 2>&1
  VAL=$(tail -1 /tmp/val.out)
  ok=no
  case "$ASSET" in 3*) if [ -n "$RT" ] && [ "${VAL#PASS}" != "$VAL" ]; then ok=yes; pass=$((pass+1)); fi;; esac
  echo "RESULT prompt=[$PROMPT] asset=$ASSET runtime=${RT:-FAIL} val=$VAL ok=$ok"
done
echo "EXT_ONLY_E2E $pass/$total"

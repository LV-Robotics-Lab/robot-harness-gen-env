#!/usr/bin/env bash
# C line acceptance: text prompt -> scene grounds OUR external mug -> SAPIEN
# runtime replay -> full validation (fail=0 not_run=0). Content-judged.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ACTIVE_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
REPO_ROOT=$(cd -- "$ACTIVE_ROOT/../../.." && pwd)
PY=${SAPIEN_PYTHON:-python3}
DEV=${ASSET_PIPELINE_ROOT:-$ACTIVE_ROOT}
UP=${GEN_ENV_ROOT:-$REPO_ROOT}
SHADOW=${S10_SHADOW:-${ROBOTWIN_SHADOW_ROOT:-$DEV/data/robotwin_shadow}}
CAT=${S10_CATALOG:-${ASSET_CATALOG:-$DEV/data/scene_gen_ext/asset_catalog.json}}
OUT=${S10_OUT:-$DEV/results/_test/20260803_smoke_usd2envgen}
PROMPT="Place a red mug on the table."

echo "=== [s10.1] compile scene ($(TZ=Asia/Singapore date +%T) SGT)"
cd "$UP"
$PY script/generate_scene.py --prompt "$PROMPT" --seed 42 \
  --asset-catalog "$CAT" --out-root "$OUT/scenes" | tail -2

SCENE=$(ls -t "$OUT/scenes" | head -1)
echo "scene: $SCENE"

echo "=== [s10.2] grounding check: red-measured model selected?"
# Content-judged, not a frozen winner: the 08-03 criterion pinned 301_cup as
# THE red mug, which went stale the day natives gained model-level measured
# colors (039_mug m10 is 40.9% red and outranks an alias match -- correctly).
# The prompt asks for a red mug; the check now verifies exactly that.
$PY - "$OUT/scenes/$SCENE" "$DEV/data/scene_gen_ext/asset_attributes.json" << 'EOF'
import json, sys
from pathlib import Path
scene, attrs_path = Path(sys.argv[1]), Path(sys.argv[2])
resolved = json.loads((scene / "resolved_scene.json").read_text())
objs = resolved.get("objects", resolved)
attrs = json.loads(attrs_path.read_text())["models"]
ok = False
for o in objs:
    aid, mid = o.get("asset_id"), str(o.get("model_id"))
    colors = ((attrs.get(aid) or {}).get(mid) or {}).get("colors") or []
    print(f"picked: {o.get('object_id')} -> {aid} m{mid} measured={colors}")
    ok = ok or "red" in colors
print("PASS grounding: red-measured model selected" if ok
      else "FAIL grounding: selected model not measured red")
sys.exit(0 if ok else 1)
EOF
GROUND_OK=$?

echo "=== [s10.3] SAPIEN runtime replay"
mkdir -p "$OUT/runtime/$SCENE"
# CUDA_LAUNCH_BLOCKING: vendored curobo's planner warmup crashes on sm_120
# under async launches (2026-08-13)
CUDA_LAUNCH_BLOCKING=1 $PY script/run_scene_runtime.py \
  --robotwin-root "$SHADOW" \
  --resolved-scene "$OUT/scenes/$SCENE/resolved_scene.json" \
  --asset-catalog "$CAT" \
  --out-dir "$OUT/runtime/$SCENE" \
  --settle-steps 900 --contact-window-steps 120 --video-frames 120 --fps 12 \
  --settle-converge-max 1800 \
  2>/dev/null | grep -vE "svulkan2|OIDN" | tail -2

echo "=== [s10.4] full validation"
$PY -m scene_gen.validator \
  --resolved-scene "$OUT/scenes/$SCENE/resolved_scene.json" \
  --asset-catalog "$CAT" \
  --package-root "$OUT/scenes/$SCENE" \
  --runtime-evidence "$OUT/runtime/$SCENE/runtime_evidence.json" \
  --require-runtime \
  --out "$OUT/runtime/$SCENE/validation_full.json" | tail -1
VAL_OK=$?

if [ "$GROUND_OK" -eq 0 ] && [ "$VAL_OK" -eq 0 ]; then
  echo "S10 PASS scene=$SCENE"
else
  echo "S10 FAIL ground=$GROUND_OK val=$VAL_OK"
  exit 1
fi

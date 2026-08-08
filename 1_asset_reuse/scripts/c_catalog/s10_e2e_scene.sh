#!/usr/bin/env bash
# C line acceptance: text prompt -> scene grounds OUR external mug -> SAPIEN
# runtime replay -> full validation (fail=0 not_run=0). Content-judged.
set -uo pipefail
PY=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
DEV=$HOME/yuxin/env-gen-dev
UP=$DEV/external/env-gen-github
SHADOW=$DEV/data/robotwin_shadow
CAT=$DEV/data/scene_gen_ext/asset_catalog.json
OUT=$DEV/results/_test/20260803_smoke_usd2envgen
PROMPT="Place a red mug on the table."

echo "=== [s10.1] compile scene ($(TZ=Asia/Singapore date +%T) SGT)"
cd "$UP"
$PY script/generate_scene.py --prompt "$PROMPT" --seed 42 \
  --asset-catalog "$CAT" --out-root "$OUT/scenes" | tail -2

SCENE=$(ls -t "$OUT/scenes" | head -1)
echo "scene: $SCENE"

echo "=== [s10.2] grounding check: did it pick 301_cup?"
$PY - "$OUT/scenes/$SCENE" << 'EOF'
import json, sys
from pathlib import Path
scene = Path(sys.argv[1])
resolved = json.loads((scene / "resolved_scene.json").read_text())
objs = resolved.get("objects", resolved)
picked = {o.get("object_id"): o.get("asset_id") for o in objs}
print("picked:", picked)
cup = [a for a in picked.values() if a == "301_cup"]
print("PASS grounding: external mug selected" if cup
      else "FAIL grounding: 301_cup not selected")
sys.exit(0 if cup else 1)
EOF
GROUND_OK=$?

echo "=== [s10.3] SAPIEN runtime replay"
mkdir -p "$OUT/runtime/$SCENE"
$PY script/run_scene_runtime.py \
  --robotwin-root "$SHADOW" \
  --resolved-scene "$OUT/scenes/$SCENE/resolved_scene.json" \
  --asset-catalog "$CAT" \
  --out-dir "$OUT/runtime/$SCENE" \
  --settle-steps 900 --contact-window-steps 120 --video-frames 120 --fps 12 \
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

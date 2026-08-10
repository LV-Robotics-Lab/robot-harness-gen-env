#!/usr/bin/env bash
# Asset-reuse smoke: external USD (YCB 025_mug) -> RoboTwin layout asset ->
# shadow root + extended catalog -> text prompt grounds OUR asset (e2e).
# Line A (RoboTwin -> Isaac USD forward conversion) was archived 2026-08-10;
# its 7 steps are no longer part of this smoke -- see archive/1_forward_convert/.
set -uo pipefail
SPIKE="$(cd "$(dirname "$0")" && pwd)"
PY_SAP=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
PY_ISA=$HOME/miniconda3/envs/isaac-smoke/bin/python
export OMNI_KIT_ACCEPT_EULA=YES
fail=0
step() {
  echo "=== [$1] $2 ($(TZ=Asia/Singapore date +%T) SGT) ==="
  shift 2
  "$@" || { echo "STEP_FAILED"; fail=1; return 1; }
}
OUT2=/home/jingxiang/yuxin/env-gen-dev/results/_test/20260803_smoke_usd2envgen
SRC=/home/jingxiang/yuxin/env-gen-dev/data/asset_library/_source/ycb_025_mug
LIB=/home/jingxiang/yuxin/env-gen-dev/data/asset_library
step 1/4 "fetch+convert usd" "$PY_ISA" -u "$SPIKE/2_convert/s8a_fetch_convert_usd.py" --source-dir "$SRC" --out "$OUT2" || true
step 2/4 "materialize+val"   "$PY_SAP" "$SPIKE/3_materialize/s8b_materialize_validate.py" --glb "$OUT2/mug_visual.glb" --source-dir "$SRC" --library-dir "$LIB" --out "$OUT2"
step 3/4 "shadow+catalog"    "$PY_SAP" "$SPIKE/5_catalog/s9_build_shadow_root.py" --library-dir "$LIB" --shadow /home/jingxiang/yuxin/env-gen-dev/data/robotwin_shadow --ext-dir /home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext
step 4/4 "e2e text->scene"   bash "$SPIKE/5_catalog/s10_e2e_scene.sh"
if [ "$fail" -eq 0 ]; then echo "SMOKE PASS"; else echo "SMOKE FAIL"; exit 1; fi

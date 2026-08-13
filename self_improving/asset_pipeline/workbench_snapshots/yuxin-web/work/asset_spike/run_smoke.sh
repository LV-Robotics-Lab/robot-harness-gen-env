#!/usr/bin/env bash
# Asset-reuse smoke: 001_bottle (rigid) + 036_cabinet (articulated)
# RoboTwin -> AssetBundle -> Isaac USD conversion -> dual-backend settle -> IR check
set -uo pipefail
SPIKE="$(cd "$(dirname "$0")" && pwd)"
OUT=/home/jingxiang/yuxin/env-gen-dev/results/_test/20260802_smoke_bottle_cabinet_glb2usd
PY_SAP=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
PY_ISA=$HOME/miniconda3/envs/isaac-smoke/bin/python
export OMNI_KIT_ACCEPT_EULA=YES
mkdir -p "$OUT"
fail=0
step() {
  echo "=== [$1] $2 ($(TZ=Asia/Singapore date +%T) SGT) ==="
  shift 2
  "$@" || { echo "STEP_FAILED"; fail=1; return 1; }
}
step 1/11 "bundles"          "$PY_SAP" "$SPIKE/robotwin_asset.py" --out "$OUT" || exit 1
step 2/11 "rigid convert"    "$PY_ISA" -u "$SPIKE/s1_convert_rigid.py" --bundle "$OUT/bottle_bundle.json" --out-dir "$OUT" || true
step 3/11 "artic convert"    "$PY_ISA" -u "$SPIKE/s2_convert_articulated.py" --bundle "$OUT/cabinet_bundle.json" --out-dir "$OUT" || true
step 4/11 "sapien validate"  "$PY_SAP" "$SPIKE/s3_validate_sapien.py" --out "$OUT"
step 5/11 "isaac validate"   "$PY_ISA" -u "$SPIKE/s4_validate_isaac.py" --out "$OUT" || true
step 6/11 "IR check"         "$PY_SAP" "$SPIKE/s5_check_ir.py" "$OUT/bottle_bundle.json" "$OUT/cabinet_bundle.json"
step 7/11 "verdict A"        "$PY_SAP" "$SPIKE/s6_verdict.py" "$OUT"
OUT2=/home/jingxiang/yuxin/env-gen-dev/results/_test/20260803_smoke_usd2envgen
SRC=/home/jingxiang/yuxin/env-gen-dev/data/asset_library/_source/ycb_025_mug
LIB=/home/jingxiang/yuxin/env-gen-dev/data/asset_library
step 8/11 "fetch+convert usd" "$PY_ISA" -u "$SPIKE/s8a_fetch_convert_usd.py" --source-dir "$SRC" --out "$OUT2" || true
step 9/11 "materialize+val"   "$PY_SAP" "$SPIKE/s8b_materialize_validate.py" --glb "$OUT2/mug_visual.glb" --source-dir "$SRC" --library-dir "$LIB" --out "$OUT2"
step 10/11 "shadow+catalog"   "$PY_SAP" "$SPIKE/s9_build_shadow_root.py" --library-dir "$LIB" --shadow /home/jingxiang/yuxin/env-gen-dev/data/robotwin_shadow --ext-dir /home/jingxiang/yuxin/env-gen-dev/data/scene_gen_ext
step 11/11 "e2e text->scene"  bash "$SPIKE/s10_e2e_scene.sh"
if [ "$fail" -eq 0 ]; then echo "SMOKE PASS"; else echo "SMOKE FAIL"; exit 1; fi

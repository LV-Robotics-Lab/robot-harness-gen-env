#!/usr/bin/env bash
# Asset-reuse smoke: external USD (YCB 025_mug) -> RoboTwin layout asset ->
# shadow root + extended catalog -> text prompt grounds OUR asset (e2e).
# Line A (RoboTwin -> Isaac USD forward conversion) was archived 2026-08-10;
# its 7 steps are no longer part of this smoke -- see archive/1_forward_convert/.
set -uo pipefail
SPIKE="$(cd "$(dirname "$0")" && pwd)"
DEV=/home/jingxiang/yuxin/env-gen-dev
PY_SAP=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
PY_ISA=$HOME/miniconda3/envs/isaac-smoke/bin/python
export OMNI_KIT_ACCEPT_EULA=YES

# --- 安全闸（2026-08-10 加）------------------------------------------------
# 步 2 的 s8b_materialize_validate 走线 B 单件打样通路：它会把 <LIB>/301_cup
# **整目录重建**，后果实测过一次：
#   - 510KB 的 coacd 分解碰撞体被覆盖成 7MB 视觉网格的副本
#   - 该资产已提交的 ledger.json 被删除（s8b 仍写 pre-v1 bundle，不写 v1 账本）
# 所以资产库路径不再默认指向生产库，必须显式给。
LIB=${SMOKE_LIB:-}
if [ -z "$LIB" ]; then
  cat <<MSG
REFUSED: 未指定 SMOKE_LIB，拒绝运行。

原因：步 2 (s8b) 会整目录重建 <LIB>/301_cup，覆盖 coacd 碰撞体并删除 ledger.json。
本脚本因此不再默认写生产资产库。

对生产库跑（会破坏 301_cup，跑完必须重新导入）：
  SMOKE_LIB=$DEV/data/asset_library bash scripts/run_smoke.sh

重新导入命令（清单条目带 collision: coacd）：
  见 1_asset_reuse/results/20260810_repair_301cup_coacd/manifest_301_cup.json
  scripts/2_convert/import_fetch_convert.py -> scripts/3_materialize/import_materialize.py

根治方向（未做，待定）：让 smoke 全程跑隔离库，或改用批量管线替代 s8b。
MSG
  exit 2
fi
SHADOW=${SMOKE_SHADOW:-$DEV/data/robotwin_shadow}
EXT=${SMOKE_EXT:-$DEV/data/scene_gen_ext}
# s9 缺 --extra-overrides 会让外部资产丢掉 category/aliases/stable_orientation，
# catalog 的 available 从 28 掉到 14（2026-08-10 实测）。用法与 README 一致。
FRAG=${SMOKE_FRAGMENT:-$EXT/external_overrides_fragment.yml}
# ---------------------------------------------------------------------------

fail=0
step() {
  echo "=== [$1] $2 ($(TZ=Asia/Singapore date +%T) SGT) ==="
  shift 2
  "$@" || { echo "STEP_FAILED"; fail=1; return 1; }
}
OUT2=$DEV/results/_test/20260803_smoke_usd2envgen
SRC=$DEV/data/asset_library/_source/ycb_025_mug
step 1/4 "fetch+convert usd" "$PY_ISA" -u "$SPIKE/2_convert/s8a_fetch_convert_usd.py" --source-dir "$SRC" --out "$OUT2" || true
step 2/4 "materialize+val"   "$PY_SAP" "$SPIKE/3_materialize/s8b_materialize_validate.py" --glb "$OUT2/mug_visual.glb" --source-dir "$SRC" --library-dir "$LIB" --out "$OUT2"
if [ -f "$FRAG" ]; then
  step 3/4 "shadow+catalog"  "$PY_SAP" "$SPIKE/5_catalog/s9_build_shadow_root.py" --library-dir "$LIB" --shadow "$SHADOW" --ext-dir "$EXT" --extra-overrides "$FRAG"
else
  echo "WARN: overrides fragment 不存在（$FRAG），s9 将不带 --extra-overrides 运行"
  echo "     -> 外部资产会丢失 category/aliases/stable_orientation，catalog available 会偏低"
  step 3/4 "shadow+catalog"  "$PY_SAP" "$SPIKE/5_catalog/s9_build_shadow_root.py" --library-dir "$LIB" --shadow "$SHADOW" --ext-dir "$EXT"
fi
step 4/4 "e2e text->scene"   bash "$SPIKE/5_catalog/s10_e2e_scene.sh"
if [ "$fail" -eq 0 ]; then echo "SMOKE PASS"; else echo "SMOKE FAIL"; exit 1; fi

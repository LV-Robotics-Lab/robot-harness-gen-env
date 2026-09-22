#!/usr/bin/env bash
# probe s8: extend "no declaration goes unverified" to the 3XX externals.
#
# The calibration file covers 111 native models; every 3XX asset's
# stable_pose_id came from convention inheritance and has never been re-proved
# by the drop-settle + reverify campaign the natives went through (README's
# realest open gap, measured 2026-08-20). calibrate_native_origins already
# accepts --only with external ids and MERGES into the existing file
# (result seeds from `done`), so this is a targeted campaign, not new code.
#
# The tool self-checkpoints and exits on render-cache exhaustion; this wrapper
# restarts it until a full pass adds nothing new (its own designed protocol).
set -uo pipefail
A=/home/yuhang/workspace/robot-harness-gen-env/self_improving/asset_pipeline/active
PY=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
ONLY="301_brick 301_cup 302_bowl 302_can 303_box 304_bottle 304_kettle 305_bowl 305_teacup 306_block 307_banana 308_pitcher 309_power_drill 311_marker 315_crate 316_duck 317_tissuebox 318_scissors 319_snowman 320_teddy_bear 321_dustbin 322_raccoon 324_knife 325_hubcap 327_sphere 328_fusebox 329_tripod 330_ball 331_vinyl 333_cherry 334_exit 335_gasoline 337_caution 338_tank 339_panel 340_skull 341_tool 343_ceiling 345_alarm 346_taxi 347_light 348_omniverse 349_airconditioner 350_umbrella 351_grid 352_handicap 354_stone 355_television 357_racks 358_leaves 360_merge 361_leaf 364_hanger 365_tv 366_ball"

cd "$A/1_asset_reuse"
for round in $(seq 1 12); do
  echo "=== round $round ($(date +%H:%M:%S))"
  CUDA_LAUNCH_BLOCKING=1 $PY scripts/5_catalog/calibrate_native_origins.py \
    --only $ONLY > /tmp/s8_round.log 2>&1
  tail -4 /tmp/s8_round.log
  # done when the last full pass ran to completion (no cache-exhaustion break)
  if ! grep -q "渲染缓存耗尽" /tmp/s8_round.log; then
    $PY - <<'EOF'
import json
d = json.load(open("/home/yuhang/workspace/robot-harness-gen-env/self_improving/asset_pipeline/active/data/scene_gen_ext/native_origin_calibration.json"))
ext = {k: v for k, v in d["models"].items() if k[:3].isdigit() and int(k[:3]) >= 300}
n_ok = sum(1 for v in ext.values() for r in v.values() if r.get("verdict") == "ok")
n_all = sum(len(v) for v in ext.values())
print("EXT_CAL assets=%d models=%d ok=%d" % (len(ext), n_all, n_ok))
EOF
    break
  fi
done
echo "S8_DONE"

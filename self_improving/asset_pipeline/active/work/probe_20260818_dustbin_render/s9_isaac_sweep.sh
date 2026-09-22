#!/usr/bin/env bash
# probe s9-sweep: discharge the isaacsim verification write-back obligation.
#
# v3 contract clause: an Isaac run that does not write back is not finished --
# yet ~50 models carry isaacsim representations with zero isaacsim
# verification records (only the 08-15 first-run pair had any). For every such
# model: compile a standard per-category scene against the external-only
# catalog, run the real Isaac settle harness, and append the resulting facts
# via the single write-back path. A model whose category grounds to a
# DIFFERENT model is skipped honestly (recorded), not faked.
set -uo pipefail
R=/home/yuhang/workspace/robot-harness-gen-env
A=$R/self_improving/asset_pipeline/active
PY=$HOME/miniconda3/envs/env-gen-yuxin/bin/python
CAT=$A/data/scene_gen_ext/asset_catalog_external_only.json
OUT=$A/results/20260822_isaac_writeback
mkdir -p "$OUT/scenes" "$OUT/settle"

$PY - <<'EOF' > "$OUT/targets.tsv"
import json, glob, pathlib
for f in sorted(glob.glob("/home/yuhang/workspace/robot-harness-gen-env/self_improving/asset_pipeline/active/data/asset_library/*/*/ledger.json")):
    d = json.loads(pathlib.Path(f).read_text())
    for m in d.get("models", []):
        reps = {r.get("backend") for r in m.get("representations", []) if r.get("role") != "snapshot"}
        has_ver = any(v.get("backend") == "isaacsim" for v in m.get("verification", []))
        if "isaacsim" in reps and not has_ver:
            print("%s\t%s\t%s" % (pathlib.Path(f).parent.name, m.get("model_id"), d.get("category")))
EOF
echo "targets: $(wc -l < "$OUT/targets.tsv")"

cd "$R"
while IFS=$'\t' read -r ASSET MODEL CATEGORY; do
  TAG="${ASSET}_m${MODEL}"
  # one scene per category is enough for every target that category grounds to
  SCENE_DIR="$OUT/scenes/$CATEGORY"
  if [ ! -d "$SCENE_DIR" ]; then
    $PY script/generate_scene.py --prompt "Place a $CATEGORY on the table." --seed 42 \
        --asset-catalog "$CAT" --out-root "$SCENE_DIR" > "$OUT/gen_$CATEGORY.log" 2>&1 || true
  fi
  RS=$(find "$SCENE_DIR" -name resolved_scene.json 2>/dev/null | head -1)
  if [ -z "$RS" ]; then echo "SKIP $TAG gen_fail($CATEGORY)"; continue; fi
  GOT=$($PY -c "import json;o=json.load(open('$RS'))['objects'][0];print(o['asset_id'],o.get('model_id'))" 2>/dev/null)
  if [ "$GOT" != "$ASSET $MODEL" ]; then echo "SKIP $TAG grounded=($GOT)"; continue; fi
  D="$OUT/settle/$TAG"
  mkdir -p "$D"
  timeout 900 $PY "$A/2_sim_migration/scripts/isaac_settle_check.py" \
    --resolved-scene "$RS" --asset "$ASSET" --model "$MODEL" \
    --out-dir "$D" --run-id isaac-writeback-20260822 > "$D/driver.log" 2>&1
  V=$(grep -oE "verdict=[a-z_]+" "$D/driver.log" | head -1)
  echo "RAN $TAG ${V:-no_verdict}"
done < "$OUT/targets.tsv"
echo "SWEEP_DONE"

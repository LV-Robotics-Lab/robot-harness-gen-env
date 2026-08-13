#!/usr/bin/env bash
# Asset-reuse smoke (B+C lines), fully isolated.
#
#   manifest -> Kit convert -> materialize into an ISOLATED library -> shadow
#   root + extended catalog -> text prompt grounds OUR asset -> SAPIEN replay ->
#   full validation. Content-judged (Kit swallows exit codes).
#
# ISOLATION (2026-08-10): every artifact -- source mirror, staging, asset
# library, shadow root, extended catalog, scenes -- lands under
# results/_test/<run>/. Production data/ is never written, only read
# (real RoboTwin root via s9's default, and the upstream reference catalog).
#
# WHY: this smoke used to call s8b_materialize_validate against the *production*
# asset library. s8b rebuilds the asset directory from scratch, which destroyed
# 301_cup's coacd collision mesh (510KB -> a 7MB copy of the visual mesh) and
# deleted its committed ledger.json -- s8b predates the v1 ledger. s8a/s8b are
# now archived; the batch pipeline does the same job, honours `collision: coacd`,
# writes a v1 ledger, and emits the overrides fragment the catalog needs.
set -uo pipefail
SPIKE="$(cd "$(dirname "$0")" && pwd)"
ACTIVE_ROOT="$(cd "$SPIKE/../.." && pwd)"
DEV=${ASSET_PIPELINE_ROOT:-$ACTIVE_ROOT}
PY_SAP=${SAPIEN_PYTHON:-python3}
PY_ISA=${ISAAC_PYTHON:-python3}
export OMNI_KIT_ACCEPT_EULA=YES

RUN=${SMOKE_RUN:-$DEV/results/_test/$(TZ=Asia/Singapore date +%Y%m%d)_smoke_batch_e2e}
case "$RUN" in
  */results/_test/*) : ;;
  *) echo "REFUSED: SMOKE_RUN 必须落在 results/_test/ 下（当前: $RUN）"; exit 2 ;;
esac

MANIFEST=${SMOKE_MANIFEST:-$SPIKE/../configs/smoke_manifest.json}
LIB=$RUN/asset_library
SRC=$RUN/_source            # own source mirror -- production _source untouched
SHADOW=$RUN/robotwin_shadow
EXT=$RUN/scene_gen_ext
STAGING=$RUN/staging
FRAG=$RUN/overrides_fragment.yml

rm -rf "$RUN"
mkdir -p "$SRC" "$LIB" "$STAGING"

fail=0
step() {
  echo "=== [$1] $2 ($(TZ=Asia/Singapore date +%T) SGT) ==="
  shift 2
  "$@" || { echo "STEP_FAILED"; fail=1; return 1; }
}

step 1/4 "fetch+convert (Kit)"   "$PY_ISA" -u "$SPIKE/2_convert/import_fetch_convert.py" \
  --manifest "$MANIFEST" --source-root "$SRC" --staging "$STAGING"
step 2/4 "materialize+validate"  "$PY_SAP" "$SPIKE/3_materialize/import_materialize.py" \
  --staging "$STAGING" --library-dir "$LIB" --out "$RUN/mat" --overrides-fragment "$FRAG" \
  --identity-basis manifest_human --identity-evidence "$MANIFEST"
step 3/4 "shadow+catalog"        "$PY_SAP" "$SPIKE/5_catalog/s9_build_shadow_root.py" \
  --library-dir "$LIB" --shadow "$SHADOW" --ext-dir "$EXT" --extra-overrides "$FRAG"
step 4/4 "e2e text->scene"       env \
  S10_SHADOW="$SHADOW" S10_CATALOG="$EXT/asset_catalog.json" S10_OUT="$RUN/e2e" \
  bash "$SPIKE/5_catalog/s10_e2e_scene.sh"

echo "run dir: $RUN"
if [ "$fail" -eq 0 ]; then echo "SMOKE PASS"; else echo "SMOKE FAIL"; exit 1; fi

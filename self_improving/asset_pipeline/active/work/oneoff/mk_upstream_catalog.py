"""Build the upstream-only catalog view: the exact complement of
asset_catalog_external_only.json, i.e. entries whose source provider IS
robotwin. backfill_upstream ingests every entry it is handed, so scoping is
done by handing it a scoped catalog rather than by teaching it a filter."""
import json
import pathlib
import sys

A = pathlib.Path("/home/yuhang/workspace/robot-harness-gen-env/self_improving/asset_pipeline/active")
LIB = (A / "data/asset_library").resolve()
full = json.loads((A / "data/scene_gen_ext/asset_catalog.json").read_text())
keep = []
for e in full["entries"]:
    p = pathlib.Path(e["asset_path"]).resolve()
    try:
        provider = p.relative_to(LIB).parts[0]
    except ValueError:
        continue
    if provider == "robotwin":
        keep.append(e)
out = pathlib.Path(sys.argv[1])
out.write_text(json.dumps({**full, "entries": keep}, indent=2, ensure_ascii=False) + "\n")
usable = sum(1 for e in keep for m in e.get("models", []) if m.get("usable"))
print("upstream-only catalog: %d entries, %d usable models -> %s" % (len(keep), usable, out))

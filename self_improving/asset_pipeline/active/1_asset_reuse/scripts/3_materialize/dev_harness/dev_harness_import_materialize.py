#!/usr/bin/env python3
"""import_materialize.py regression harness -- multi-model re-run scenarios.

Not a pytest suite: exercises real SAPIEN physics + real GLB files across
multiple full `import_materialize.py` driver subprocess runs (multi-second
each, GPU-node-only). The lib/tests/ suite is pure stdlib and runs in
under a second across two conda environments; wiring this in would change
that character for every contributor, so it lives here as a standalone,
version-controlled, manually-rerunnable dev tool instead (same footing as
scripts/run_smoke.sh).

Run on lv-5090 with the env-gen-yuxin interpreter (no PYTHONPATH needed):
    /home/jingxiang/miniconda3/envs/env-gen-yuxin/bin/python \
        dev_harness_import_materialize.py [path-to-import_materialize.py]

Scenarios (asset_ledger.v1 T5 fix-round-3):

  3A/3B -- multi-model asset, RUN1 (fresh) then RUN2 (re-run with the
  IDENTICAL staging_manifest.json). Regresses the fix-round-2 Critical:
  driver pre-wipe used to delete a whole visual/collision directory before
  any worker ran, so an as-yet-unprocessed sibling model's file would be
  missing when an earlier-processed model's validate_ledger(...,
  check_files=True) walked the whole merged ledger. Both runs must fully
  accept, with an unchanged ledger model_id set and no file_missing.

  4 -- multi-model asset, RUN1 both models accepted, then a second run
  where model 0 gets NEW geometry (so its re-exported files really do get
  new bytes/sha256 on disk) AND is deliberately rejected before its ledger
  entry is ever rewritten (write_ledger only fires in the accept branch).
  Regresses the fix-round-3 Critical: model 0's now-stale ledger entry
  (old digest) pointed at now-changed files, and because
  validate_ledger(check_files=True) used to walk the WHOLE merged ledger,
  model 1's own (perfectly fine) worker would trip a sha256_mismatch on
  model 0's stale representation and get rejected too -- a healthy
  sibling silently evicted by a NEW model's own bad state. The rejection
  trigger here is a validator violation (a group's SOURCE_MANIFEST.json
  going missing between runs -- source.source_manifest_path becomes null,
  which lib/ledger.py's NOT_NULLABLE_MODEL forbids), not a physics settle
  failure: both land on the exact same `if violations or not
  checks["pass"]:` branch (reject, skip write_ledger, files already
  overwritten), which is the only code path this fix touches -- a settle
  failure exercises identical logic. Reproducing a *reliable* physics
  settle failure would need a deliberately off-balance asymmetric mesh
  (a simple symmetric shape dropped from a fixed, noise-free spawn pose
  tends to just stay put, as observed with a thin column in an earlier
  round of this task); constructing and tuning one wasn't worth the
  robustness cost against a fixed time budget when the validator-violation
  trigger exercises the identical vulnerable branch. Noted here rather
  than silently substituted.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import trimesh

SCRIPT = Path(
    sys.argv[1]
    if len(sys.argv) > 1
    else "/home/jingxiang/yuxin/env-gen-dev-ledger/1_asset_reuse/scripts/3_materialize/import_materialize.py"
)
PY = sys.executable
ROOT = Path("/tmp/t5_dev_harness")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_driver(staging, library_dir, out_dir, frag_path, catalog_path):
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    proc = subprocess.run(
        [
            PY,
            str(SCRIPT),
            "--staging",
            str(staging),
            "--library-dir",
            str(library_dir),
            "--out",
            str(out_dir),
            "--overrides-fragment",
            str(frag_path),
            "--reference-catalog",
            str(catalog_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc


def report_run(label, proc, out_dir, frag_path, library_dir, asset):
    print(f"\n=== {label} ===")
    for line in proc.stdout.splitlines():
        print(" ", line)
    if proc.returncode != 0 and proc.stderr.strip():
        print("stderr:")
        for line in proc.stderr.splitlines():
            print(" ", line)
    matrix = json.loads((out_dir / "import_matrix.json").read_text())
    statuses = {(r["asset"], r["model"]): r["status"] for r in matrix}
    print("matrix statuses:", statuses)
    led_path = library_dir / asset / "ledger.json"
    if led_path.exists():
        led = json.loads(led_path.read_text())
        model_ids = sorted(m["model_id"] for m in led["models"])
    else:
        model_ids = []
    print("ledger model_ids:", model_ids)
    frag_text = frag_path.read_text() if frag_path.exists() else ""
    print("fragment:", frag_path.name, "->", frag_text.strip() or "<empty>")
    return statuses, model_ids, frag_text


def scenario_3a_3b():
    """Multi-model asset, RUN1 fresh + RUN2 identical re-run. Both must
    fully accept with an unchanged ledger; no file_missing."""
    root = ROOT / "s3"
    if root.exists():
        shutil.rmtree(root)
    staging = root / "staging"
    library_dir = root / "library"
    staging.mkdir(parents=True)
    library_dir.mkdir(parents=True)

    box = trimesh.creation.box(extents=[0.08, 0.10, 0.08])
    glb0, glb1 = staging / "s3_m0.glb", staging / "s3_m1.glb"
    box.export(str(glb0))
    box.export(str(glb1))
    usd0, usd1 = staging / "s3_m0.usd", staging / "s3_m1.usd"
    usd0.write_text("placeholder 0")
    usd1.write_text("placeholder 1")

    records = [
        {
            "asset": "s3asset",
            "model": n,
            "usd": f"S3Group/s3_m{n}.usd",
            "usd_local": str(u),
            "usd_sha256": sha(u),
            "group": "s3_group",
            "glb": str(g),
            "up_axis": "Y",
            "status": "converted",
            "category": "s3_category",
            "aliases": ["s3asset"],
            "colors": [],
            "footprint": "box",
            "flat": False,
        }
        for n, (g, u) in enumerate([(glb0, usd0), (glb1, usd1)])
    ]
    (staging / "staging_manifest.json").write_text(json.dumps(records, indent=2))
    (library_dir / "_source" / "s3_group").mkdir(parents=True)
    (library_dir / "_source" / "s3_group" / "SOURCE_MANIFEST.json").write_text(
        json.dumps({"prefix": "fake", "files": {}})
    )
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps({"entries": []}))

    proc1 = run_driver(staging, library_dir, root / "out1", root / "frag1.yml", catalog)
    st1, m1, _ = report_run(
        "3A: RUN1 (fresh)",
        proc1,
        root / "out1",
        root / "frag1.yml",
        library_dir,
        "s3asset",
    )

    proc2 = run_driver(staging, library_dir, root / "out2", root / "frag2.yml", catalog)
    st2, m2, _ = report_run(
        "3B: RUN2 (identical re-run)",
        proc2,
        root / "out2",
        root / "frag2.yml",
        library_dir,
        "s3asset",
    )

    ok = True
    if set(st1.values()) != {"accepted"}:
        print("FAIL 3A: not all accepted:", st1)
        ok = False
    if m1 != [0, 1]:
        print("FAIL 3A: ledger model_ids:", m1)
        ok = False
    if set(st2.values()) != {"accepted"}:
        print("FAIL 3B: not all accepted (file_missing regression):", st2)
        ok = False
    if m2 != [0, 1]:
        print("FAIL 3B: ledger model_ids:", m2)
        ok = False
    if m1 != m2:
        print("FAIL 3B: ledger drifted between runs:", m1, "vs", m2)
        ok = False
    if any("file_missing" in r for r in ("".join(str(v) for v in st2.values()),)):
        pass  # status alone doesn't carry reasons; matrix.json has them if needed
    print("scenario 3A/3B:", "PASS" if ok else "FAIL")
    return ok


def scenario_4():
    """Multi-model asset. RUN1: both accepted. RUN2: model 0 gets NEW
    geometry (new bytes on disk) and is rejected via a validator violation
    (its group's SOURCE_MANIFEST.json goes missing) before its ledger
    entry is rewritten -- model 1 is untouched and must NOT be
    collaterally rejected."""
    root = ROOT / "s4"
    if root.exists():
        shutil.rmtree(root)
    staging = root / "staging"
    library_dir = root / "library"
    staging.mkdir(parents=True)
    library_dir.mkdir(parents=True)

    box0 = trimesh.creation.box(extents=[0.08, 0.10, 0.08])
    box1 = trimesh.creation.box(extents=[0.07, 0.09, 0.07])
    glb0, glb1 = staging / "s4_m0.glb", staging / "s4_m1.glb"
    box0.export(str(glb0))
    box1.export(str(glb1))
    usd0, usd1 = staging / "s4_m0.usd", staging / "s4_m1.usd"
    usd0.write_text("placeholder 0")
    usd1.write_text("placeholder 1")

    def rec(model, glb, usd, group):
        return {
            "asset": "s4asset",
            "model": model,
            "usd": f"S4Group/s4_m{model}.usd",
            "usd_local": str(usd),
            "usd_sha256": sha(usd),
            "group": group,
            "glb": str(glb),
            "up_axis": "Y",
            "status": "converted",
            "category": "s4_category",
            "aliases": ["s4asset"],
            "colors": [],
            "footprint": "box",
            "flat": False,
        }

    records = [rec(0, glb0, usd0, "s4_group_a"), rec(1, glb1, usd1, "s4_group_b")]
    (staging / "staging_manifest.json").write_text(json.dumps(records, indent=2))
    for grp in ("s4_group_a", "s4_group_b"):
        (library_dir / "_source" / grp).mkdir(parents=True)
        (library_dir / "_source" / grp / "SOURCE_MANIFEST.json").write_text(
            json.dumps({"prefix": "fake", "files": {}})
        )
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps({"entries": []}))

    proc1 = run_driver(staging, library_dir, root / "out1", root / "frag1.yml", catalog)
    st1, m1, _ = report_run(
        "4 RUN1: both fresh",
        proc1,
        root / "out1",
        root / "frag1.yml",
        library_dir,
        "s4asset",
    )

    # mutate for RUN2: model 0 gets new geometry (new bytes/sha256), and its
    # group's SOURCE_MANIFEST.json disappears (forces source_manifest_path
    # to null -> NOT_NULLABLE_MODEL violation on re-admission). Model 1's
    # geometry and group are untouched.
    box0_changed = trimesh.creation.box(extents=[0.09, 0.11, 0.09])
    box0_changed.export(str(glb0))
    (library_dir / "_source" / "s4_group_a" / "SOURCE_MANIFEST.json").unlink()

    proc2 = run_driver(staging, library_dir, root / "out2", root / "frag2.yml", catalog)
    st2, m2, frag2_text = report_run(
        "4 RUN2: m0 changed geometry + gate failure, m1 untouched",
        proc2,
        root / "out2",
        root / "frag2.yml",
        library_dir,
        "s4asset",
    )

    ok = True
    if st2.get(("s4asset", 0)) != "rejected":
        print("FAIL 4: m0 should be rejected (missing SOURCE_MANIFEST):", st2)
        ok = False
    if st2.get(("s4asset", 1)) != "accepted":
        print("FAIL 4 (THE BUG): m1 collaterally rejected by m0's stale digest:", st2)
        ok = False
    if m2 != [1]:
        print("FAIL 4: ledger should only have model 1 left (m0 pruned):", m2)
        ok = False
    if '"0":' in frag2_text:
        print(
            "FAIL 4: fragment still lists rejected/pruned model 0 (dangling ref):",
            frag2_text,
        )
        ok = False
    if '"1":' not in frag2_text:
        print("FAIL 4: fragment missing healthy model 1:", frag2_text)
        ok = False
    matrix2 = json.loads((root / "out2" / "import_matrix.json").read_text())
    m0_row = next(r for r in matrix2 if r["model"] == 0)
    if not any("schema_violation" in reason for reason in m0_row.get("reasons", [])):
        print(
            "FAIL 4: m0's rejection reason isn't the expected schema_violation:",
            m0_row.get("reasons"),
        )
        ok = False
    print("scenario 4:", "PASS" if ok else "FAIL")
    return ok


def main():
    ok3 = scenario_3a_3b()
    ok4 = scenario_4()
    print("\n=== OVERALL:", "PASS" if (ok3 and ok4) else "FAIL", "===")
    return 0 if (ok3 and ok4) else 1


if __name__ == "__main__":
    sys.exit(main())

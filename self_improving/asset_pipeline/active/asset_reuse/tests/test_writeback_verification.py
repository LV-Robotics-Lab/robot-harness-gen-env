import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ledger" / "writeback_verification.py"
sys.path.insert(0, str(ROOT))

from lib import ledger, ledger_writes  # noqa: E402

from tests.test_ledger import make_valid  # noqa: E402
from tests.trusted_fixtures import qualified_runtime_capability  # noqa: E402


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_valid_ledger(library):
    document = make_valid()
    asset = document["external_ids"]["env_gen"]
    asset_dir = library / asset
    asset_dir.mkdir(parents=True)
    model = document["models"][0]
    for index, representation in enumerate(model["representations"]):
        suffix = "png" if representation["role"] == "snapshot" else "ply"
        path = asset_dir / f"representation-{index}.{suffix}"
        path.write_bytes(f"payload-{index}".encode())
        digest = _sha256(path)
        representation.update(
            format=suffix,
            uri=str(path),
            sha256=digest,
            files=[{"uri": str(path), "sha256": digest, "bytes": path.stat().st_size}],
        )
    model["verification"][0]["verified_digest"] = ledger.reps_digest(model, "sapien")
    assert ledger.validate_ledger(document, check_files=True) == []
    ledger_path = asset_dir / "ledger.json"
    ledger.write_ledger(ledger_path, document)
    return ledger_path, document


def _run(tmp_path, library, fact, *, backend="sapien"):
    results = tmp_path / "facts.json"
    results.write_text(json.dumps(fact if isinstance(fact, list) else [fact]))
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--results",
            str(results),
            "--library",
            str(library),
            "--upstream",
            str(tmp_path / "empty-upstream"),
            "--backend",
            backend,
        ],
        capture_output=True,
        text=True,
    )


def test_writeback_requires_an_explicit_currently_qualified_backend(tmp_path):
    results = tmp_path / "facts.json"
    results.write_text("[]\n")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--results", str(results)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--backend" in result.stderr
    assert "required" in result.stderr


def _trusted_fact(tmp_path, document, *, verdict="pass", run_id="bound-runtime"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    asset = document["external_ids"]["env_gen"]
    model = document["models"][0]
    digest = ledger.reps_digest(model, "sapien")
    source_asset_dir = Path(model["representations"][0]["uri"]).parent
    snapshot = ledger_writes.publish_execution_snapshot(
        tmp_path / "execution-snapshots",
        source_asset_dir=source_asset_dir,
        asset_key=asset,
        model_entry=model,
    )
    capability = qualified_runtime_capability(
        tmp_path / "runtime",
        "asset.s11_runtime_load.v1",
        ledger,
        ledger_writes,
    )
    passed = verdict == "pass"
    payload = ledger_writes.issue_qualified_verification(
        issuer="asset.s11_runtime_load.v1",
        asset_key=asset,
        model_id=model["model_id"],
        run_id=run_id,
        timestamp="2026-08-31T12:00:00",
        reps_digest=digest,
        inputs={
            "runtime_report": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": asset, "model_id": model["model_id"]},
        },
        thresholds={"max_late_drift_m": 0.002, "min_final_z_m": -0.005},
        result={
            "schema": "asset_runtime_load_result.v3",
            "loaded": passed,
            "finite": passed,
            "late_drift_m": 0.0 if passed else None,
            "final_z_m": 0.01 if passed else None,
            "spawn_pose_id": model["physical"]["conventions"]["stable_poses"][0]["pose_id"],
            "spawn_orientation_wxyz": list(
                model["physical"]["conventions"]["stable_poses"][0]["orientation_wxyz"]
            ),
            "observed_spawn_orientation_wxyz": list(
                model["physical"]["conventions"]["stable_poses"][0]["orientation_wxyz"]
            ),
            "observed_spawn_origin_z_m": 0.0,
            "fix_root_link": model["physical"]["conventions"]["is_static"],
            "z_policy": model["physical"]["conventions"]["z_policy"],
            "details": {"fixture": "writeback-runtime-v1"},
        },
        model_entry=model,
        execution_snapshot=snapshot,
        runtime_capability=capability,
    )
    record = ledger_writes.publish_verification_evidence(tmp_path / "producer-evidence", payload)
    return {"asset_dir": asset, "model_id": model["model_id"], "evidence": record}


@pytest.mark.parametrize("supplied_digest", [None, "f" * 64])
def test_writeback_refuses_unbound_or_stale_physical_fact(tmp_path, supplied_digest):
    library = tmp_path / "library"
    ledger_path, document = _write_valid_ledger(library)
    before = ledger_path.read_bytes()
    fact = {
        "asset_dir": document["external_ids"]["env_gen"],
        "model_id": 0,
        "check": "runtime_load",
        "verdict": "pass",
        "run_id": "unbound-attack",
        "timestamp": "2026-08-31T12:00:00",
    }
    if supplied_digest is not None:
        fact["verified_digest"] = supplied_digest

    result = _run(tmp_path, library, fact)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "evidence" in result.stdout.lower()
    assert ledger_path.read_bytes() == before


def test_writeback_refuses_arbitrary_fact_even_when_digest_matches_current_representations(
    tmp_path,
):
    library = tmp_path / "library"
    ledger_path, document = _write_valid_ledger(library)
    digest = ledger.reps_digest(document["models"][0], "sapien")
    fact = {
        "asset_dir": document["external_ids"]["env_gen"],
        "model_id": 0,
        "check": "runtime_load",
        "verdict": "pass",
        "run_id": "bound-runtime",
        "timestamp": "2026-08-31T12:00:00",
        "verified_digest": digest,
    }

    before = ledger_path.read_bytes()
    result = _run(tmp_path, library, fact)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "evidence" in result.stdout.lower()
    assert ledger_path.read_bytes() == before


def test_writeback_accepts_only_preexisting_strict_evidence(tmp_path):
    library = tmp_path / "library"
    ledger_path, document = _write_valid_ledger(library)
    fact = _trusted_fact(tmp_path, document)

    result = _run(tmp_path, library, fact)

    assert result.returncode == 0, result.stdout + result.stderr
    written = json.loads(ledger_path.read_text())
    receipt = written["models"][0]["verification"][-1]
    assert receipt["evidence"] == fact["evidence"]
    assert (
        ledger.latest_trusted_verification(
            written["models"][0], "sapien", "runtime_load", fact["asset_dir"]
        )
        == receipt
    )


def test_writeback_rejects_duplicate_batch_before_any_receipt_is_written(tmp_path):
    library = tmp_path / "library"
    ledger_path, document = _write_valid_ledger(library)
    before = ledger_path.read_bytes()
    fact = _trusted_fact(tmp_path, document)

    result = _run(tmp_path, library, [fact, fact])

    assert result.returncode != 0
    assert "duplicate" in result.stdout.lower()
    assert ledger_path.read_bytes() == before


def test_writeback_refuses_evidence_for_a_different_backend(tmp_path):
    library = tmp_path / "library"
    ledger_path, document = _write_valid_ledger(library)
    before = ledger_path.read_bytes()
    fact = _trusted_fact(tmp_path, document, run_id="wrong-backend")

    result = _run(tmp_path, library, fact, backend="isaacsim")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "invalid choice: 'isaacsim'" in result.stderr
    assert "sapien" in result.stderr
    assert ledger_path.read_bytes() == before


def test_writeback_rejects_pass_to_fail_conflict_for_same_run(tmp_path):
    library = tmp_path / "library"
    ledger_path, document = _write_valid_ledger(library)
    fact = _trusted_fact(tmp_path / "pass", document, run_id="immutable-run")
    assert _run(tmp_path, library, fact).returncode == 0
    before = ledger_path.read_bytes()

    failed_fact = _trusted_fact(tmp_path / "fail", document, verdict="fail", run_id="immutable-run")
    result = _run(tmp_path, library, failed_fact)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "conflict" in result.stdout.lower()
    assert ledger_path.read_bytes() == before

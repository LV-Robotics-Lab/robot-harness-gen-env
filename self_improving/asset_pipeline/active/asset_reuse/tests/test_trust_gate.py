import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import ledger, ledger_writes  # noqa: E402

from .test_ledger import make_valid  # noqa: E402
from .trusted_fixtures import qualified_runtime_capability  # noqa: E402


@pytest.mark.parametrize(
    "asset_key",
    [
        "",
        " ",
        " 301_cup",
        "301_cup ",
        ".",
        "..",
        "../301_cup",
        "/tmp/301_cup",
        "provider/301_cup",
        "provider\\301_cup",
        "301_cup\n",
        "301_cup\x7f",
        "x" * 129,
    ],
)
def test_public_asset_locator_rejects_noncanonical_asset_keys(tmp_path, asset_key):
    with pytest.raises(ledger.UnsafeAssetKeyError):
        ledger.asset_dir(tmp_path, asset_key)
    with pytest.raises(ledger.UnsafeAssetKeyError):
        ledger.ledger_path(tmp_path, asset_key)


@pytest.mark.parametrize("layout", ["flat", "grouped", "root"])
def test_public_asset_locator_rejects_every_symlink_component(tmp_path, layout):
    real_library = tmp_path / "real-library"
    real_library.mkdir()
    root = real_library
    if layout == "root":
        root = tmp_path / "linked-library"
        root.symlink_to(real_library, target_is_directory=True)
    elif layout == "flat":
        outside = tmp_path / "outside-flat"
        outside.mkdir()
        (real_library / "301_cup").symlink_to(outside, target_is_directory=True)
    else:
        outside = tmp_path / "outside-provider"
        outside.mkdir()
        (real_library / "nvidia").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ledger.UnsafeAssetPathError, match="symlink"):
        ledger.asset_dir(root, "301_cup")


def test_load_asset_ledger_supports_flat_and_grouped_and_checks_identity(tmp_path):
    flat = tmp_path / "flat"
    flat_asset = flat / "301_cup"
    flat_asset.mkdir(parents=True)
    (flat_asset / "ledger.json").write_text(json.dumps({"external_ids": {"env_gen": "301_cup"}}))
    loaded = ledger.load_asset_ledger(flat, "301_cup")
    assert loaded.asset_dir == flat_asset.absolute()
    assert loaded.ledger_path == flat_asset.absolute() / "ledger.json"
    assert loaded.document["external_ids"]["env_gen"] == "301_cup"

    grouped = tmp_path / "grouped"
    grouped_asset = grouped / "nvidia" / "302_can"
    grouped_asset.mkdir(parents=True)
    (grouped_asset / "ledger.json").write_text(
        json.dumps({"external_ids": {"env_gen": "wrong_asset"}})
    )
    with pytest.raises(ledger.LedgerIdentityError, match="external_ids.env_gen"):
        ledger.load_asset_ledger(grouped, "302_can")


def test_asset_locator_fail_closed_edges_and_safe_creation(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    with pytest.raises(ledger.UnsafeAssetPathError, match="escapes"):
        ledger._safe_asset_path(root, tmp_path / "outside")

    def fail_inspection(_path):
        raise ledger._ClosureInspectionError("inspection failed")

    with monkeypatch.context() as patcher:
        patcher.setattr(ledger, "_symlink_component", fail_inspection)
        with pytest.raises(ledger.UnsafeAssetPathError, match="inspection failed"):
            ledger._safe_asset_path(root, root / "315_shears")

    root_file = tmp_path / "not-a-directory"
    root_file.write_text("x")
    with pytest.raises(ledger.UnsafeAssetPathError, match="not a directory"):
        ledger.asset_dir(root_file, "315_shears")

    (root / "plain-file-provider").write_text("x")
    empty_provider = root / "empty-provider"
    empty_provider.mkdir()
    assert ledger.asset_dir(root, "315_shears") is None

    created = ledger.ensure_asset_directory(root, "315_shears")
    assert created == root / "315_shears"
    assert ledger.ensure_asset_directory(root, "315_shears") == created
    with pytest.raises(ledger.UnsafeAssetPathError, match="not a directory"):
        ledger.ensure_asset_directory(root_file, "315_shears")


def test_asset_directory_creation_handles_race_and_disappearance(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    existing = root / "315_shears"
    existing.mkdir()
    calls = 0

    def hidden_once(_root, _asset):
        nonlocal calls
        calls += 1
        return None if calls == 1 else existing

    with monkeypatch.context() as patcher:
        patcher.setattr(ledger, "asset_dir", hidden_once)
        assert ledger.ensure_asset_directory(root, "315_shears") == existing

    with monkeypatch.context() as patcher:
        patcher.setattr(ledger, "asset_dir", lambda _root, _asset: None)
        with pytest.raises(ledger.UnsafeAssetPathError, match="disappeared"):
            ledger.ensure_asset_directory(root, "316_missing")


def test_load_asset_ledger_distinguishes_absent_asset_and_missing_ledger(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    with pytest.raises(FileNotFoundError, match="absent"):
        ledger.load_asset_ledger(root, "315_shears")
    (root / "315_shears").mkdir()
    with pytest.raises(FileNotFoundError, match="no ledger"):
        ledger.load_asset_ledger(root, "315_shears")


@pytest.mark.parametrize("model_id", [True, False, -1, 0.0, "0", "00", None, [], {}])
def test_model_ids_must_be_canonical_nonnegative_integers(model_id):
    with pytest.raises(ledger.InvalidModelIdError):
        ledger.canonical_model_id(model_id)

    document = make_valid()
    document["models"][0]["model_id"] = model_id
    assert "bad_model_id" in {v.code for v in ledger.validate_ledger(document)}


def test_canonical_model_id_accepts_zero_and_positive_values():
    assert ledger.canonical_model_id(0) == 0
    assert ledger.canonical_model_id(42) == 42


def test_asset_model_claim_is_canonical_and_rejects_duplicates():
    seen = set()
    assert ledger.claim_asset_model(seen, "301_cup", 0) == ("301_cup", 0)
    with pytest.raises(ledger.DuplicateAssetModelError):
        ledger.claim_asset_model(seen, "301_cup", 0)
    assert ledger.claim_asset_model(seen, "301_cup", 1) == ("301_cup", 1)


def _evidence_payload(*, asset_key, model, check="settle", verdict="pass", **overrides):
    values = {
        "asset_key": asset_key,
        "model_id": model["model_id"],
        "backend": "sapien",
        "check": check,
        "verdict": verdict,
        "run_id": "physical-run-1",
        "timestamp": "2026-08-31T12:00:00",
        "reps_digest": ledger.reps_digest(model, "sapien"),
        "script_path": __file__,
        "inputs": {
            "report": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": asset_key, "model_id": model["model_id"]},
        },
        "thresholds": {"late_drift_m": 0.002},
        "result": {"settled": verdict == "pass"},
    }
    values.update(overrides)
    return ledger_writes.build_verification_evidence(**values)


def _file_back_model(workspace, model):
    asset_dir = workspace / "asset"
    asset_dir.mkdir(parents=True, exist_ok=True)
    for index, representation in enumerate(model["representations"]):
        suffix = representation.get("format") or "bin"
        path = asset_dir / f"representation-{index}.{suffix}"
        path.write_bytes(f"representation-{index}".encode())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        representation.update(
            uri=str(path),
            sha256=digest,
            files=[{"uri": str(path), "sha256": digest, "bytes": path.stat().st_size}],
        )
    return asset_dir


def _qualified_evidence_payload(
    *, workspace, asset_key, model, check="settle", verdict="pass", **overrides
):
    asset_dir = _file_back_model(workspace, model)
    if check == "settle":
        issuer = "asset.settle_repair.v1"
        thresholds = {
            "max_late_drift_m": 0.002,
            "min_support_z_m": -0.005,
            "max_tilt_deg": 181.0,
        }
        result = {
            "schema": "asset_settle_result.v2",
            "finite": verdict == "pass",
            "late_drift_m": 0.0,
            "support_z_m": 0.0,
            "tilt_deg": 0.0,
            "rest_orientation_wxyz": list(
                next(
                    pose
                    for pose in model["physical"]["conventions"]["stable_poses"]
                    if pose.get("is_default") is True
                )["orientation_wxyz"]
            ),
            "origin_z_m": 0.0 if verdict == "pass" else None,
            "derived_z_policy": "origin_on_table" if verdict == "pass" else None,
            "details": {},
        }
    elif check == "runtime_load":
        issuer = "asset.s11_runtime_load.v1"
        thresholds = {"max_late_drift_m": 0.002, "min_final_z_m": -0.005}
        default_pose = next(
            pose
            for pose in model["physical"]["conventions"]["stable_poses"]
            if pose.get("is_default") is True
        )
        result = {
            "schema": "asset_runtime_load_result.v3",
            "loaded": verdict == "pass",
            "finite": verdict == "pass",
            "late_drift_m": 0.0 if verdict == "pass" else None,
            "final_z_m": 0.0 if verdict == "pass" else None,
            "spawn_pose_id": default_pose["pose_id"],
            "spawn_orientation_wxyz": list(default_pose["orientation_wxyz"]),
            "observed_spawn_orientation_wxyz": list(default_pose["orientation_wxyz"]),
            "observed_spawn_origin_z_m": 0.0,
            "fix_root_link": model["physical"]["conventions"]["is_static"],
            "z_policy": model["physical"]["conventions"]["z_policy"],
            "details": {},
        }
    elif check == "joint_sweep":
        issuer = "asset.s13b_joint_sweep.v1"
        thresholds = {"min_screenshot_std": 1.0, "allow_free_joints": False}
        conventions = model["physical"]["conventions"]
        default_pose = next(
            pose for pose in conventions["stable_poses"] if pose.get("is_default") is True
        )
        result = {
            "schema": "asset_joint_sweep_result.v2",
            "loaded": verdict == "pass",
            "dof": 1,
            "expected_dof": 1,
            "limits_match": True,
            "settle_finite": True,
            "converged": True,
            "free_joint_count": 0,
            "sweep_finite": True,
            "screenshot_std": 2.0,
            "fix_root_link": True,
            "root_pose_id": default_pose["pose_id"],
            "root_orientation_wxyz": list(default_pose["orientation_wxyz"]),
            "z_policy": conventions["z_policy"],
            "details": {},
        }
    else:
        raise AssertionError(f"test helper has no qualified issuer for {check!r}")
    values = {
        "issuer": issuer,
        "asset_key": asset_key,
        "model_id": model["model_id"],
        "run_id": "physical-run-1",
        "timestamp": "2026-08-31T12:00:00",
        "reps_digest": ledger.reps_digest(model, "sapien"),
        "inputs": {
            "report": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": asset_key, "model_id": model["model_id"]},
        },
        "thresholds": thresholds,
        "result": result,
        "model_entry": model,
        "execution_snapshot": ledger_writes.publish_execution_snapshot(
            asset_dir / "verification_inputs",
            source_asset_dir=asset_dir,
            asset_key=asset_key,
            model_entry=model,
        ),
        "runtime_capability": qualified_runtime_capability(
            workspace / "runtime", issuer, ledger, ledger_writes
        ),
    }
    values.update(overrides)
    return ledger_writes.issue_qualified_verification(**values)


def _trusted_model(tmp_path, *, check="settle", verdict="pass"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    if check == "joint_sweep":
        model["physical"]["conventions"]["is_static"] = True
    payload = _qualified_evidence_payload(
        workspace=tmp_path / "execution",
        asset_key=asset_key,
        model=model,
        check=check,
        verdict=verdict,
    )
    record = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    model["verification"] = [ledger_writes.receipt_from_evidence(payload, record)]
    return asset_key, model, record


def test_trusted_verification_requires_and_rereads_canonical_hash_bound_artifact(tmp_path):
    asset_key, model, record = _trusted_model(tmp_path)

    trusted = ledger.latest_trusted_verification(model, "sapien", "settle", asset_key)

    assert trusted == model["verification"][0]
    artifact = Path(record["uri"])
    if not artifact.is_absolute():
        artifact = ledger.resolve_uri(record["uri"])
    assert artifact.name == f"{record['sha256']}.verification.json"
    assert record["bytes"] == artifact.stat().st_size
    artifact.chmod(0o644)  # simulate a local attacker bypassing the advisory read-only mode
    artifact.write_bytes(artifact.read_bytes() + b" ")
    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None


def test_trusted_verification_rereads_bound_input_report_on_every_decision(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    report = tmp_path / "runtime-report.json"
    report.write_bytes(b'{"status":"pass"}\n')
    payload = _qualified_evidence_payload(
        workspace=tmp_path / "execution",
        asset_key=asset_key,
        model=model,
        inputs={
            "report": ledger_writes.provenance_file_record(report),
            "task": {"asset_key": asset_key, "model_id": model["model_id"]},
        },
    )
    record = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    model["verification"] = [ledger_writes.receipt_from_evidence(payload, record)]
    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is not None

    report.write_bytes(b'{"status":"fail"}\n')

    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None


def test_trusted_verification_ignores_mutable_registry_script_override(tmp_path, monkeypatch):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    script = tmp_path / "physical-probe.py"
    script.write_bytes(b"result = 'pass'\n")
    report = tmp_path / "runtime-report.json"
    report.write_bytes(b'{"status":"pass"}\n')
    monkeypatch.setitem(
        ledger.QUALIFIED_VERIFICATION_ISSUERS,
        "asset.settle_repair.v1",
        {
            **ledger.QUALIFIED_VERIFICATION_ISSUERS["asset.settle_repair.v1"],
            "script": str(script),
        },
    )
    payload = _qualified_evidence_payload(
        workspace=tmp_path / "execution",
        asset_key=asset_key,
        model=model,
        inputs={
            "report": ledger_writes.provenance_file_record(report),
            "task": {"asset_key": asset_key, "model_id": model["model_id"]},
        },
    )
    record = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    model["verification"] = [ledger_writes.receipt_from_evidence(payload, record)]
    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is not None
    assert payload["capability"]["script"]["uri"] != str(script)

    script.write_bytes(b"result = 'fail'\n")

    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is not None

    model["verification"] = []
    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None


def test_legacy_and_generation_qc_receipts_never_become_trusted_physical_passes(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    model["verification"][0]["verified_digest"] = ledger.reps_digest(model, "sapien")

    assert ledger.latest_verification(model, "sapien", "settle") is not None
    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None

    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    payload = _evidence_payload(asset_key=asset_key, model=model, check="generation_qc")
    record = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    model["verification"] = [ledger_writes.receipt_from_evidence(payload, record)]
    assert ledger.latest_trusted_verification(model, "sapien", "generation_qc", asset_key) is None


def test_generic_builder_has_no_qualified_settle_issuing_authority(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    payload = ledger_writes.build_verification_evidence(
        asset_key=asset_key,
        model_id=model["model_id"],
        backend="sapien",
        check="settle",
        verdict="pass",
        run_id="generic-self-report",
        timestamp="2026-08-31T12:00:00",
        reps_digest=ledger.reps_digest(model, "sapien"),
        script_path=__file__,
        inputs={
            "report": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": asset_key, "model_id": model["model_id"]},
        },
        thresholds={
            "max_late_drift_m": 0.002,
            "min_support_z_m": -0.005,
            "max_tilt_deg": 181.0,
        },
        result={
            "schema": "asset_settle_result.v2",
            "finite": True,
            "late_drift_m": 0.0,
            "support_z_m": 0.0,
            "tilt_deg": 0.0,
            "rest_orientation_wxyz": list(
                next(
                    pose
                    for pose in model["physical"]["conventions"]["stable_poses"]
                    if pose.get("is_default") is True
                )["orientation_wxyz"]
            ),
            "origin_z_m": 0.0,
            "derived_z_policy": "origin_on_table",
            "details": {},
        },
    )
    record = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    model["verification"] = [ledger_writes.receipt_from_evidence(payload, record)]

    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None


def test_qualified_issuer_rejects_arbitrary_non_sapien_runtime_capability(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    asset_dir = _file_back_model(tmp_path / "execution", model)
    snapshot = ledger_writes.publish_execution_snapshot(
        asset_dir / "verification_inputs",
        source_asset_dir=asset_dir,
        asset_key=asset_key,
        model_entry=model,
    )
    fake_runtime = ledger_writes.capture_runtime_capability(
        loader_modules=[ledger_writes],
        sapien_module=ledger,
        entrypoint="not-sapien",
        config={"anything": True},
    )

    with pytest.raises(ledger_writes.VerificationEvidenceError, match="runtime capability"):
        ledger_writes.issue_qualified_verification(
            issuer="asset.settle_repair.v1",
            asset_key=asset_key,
            model_id=model["model_id"],
            run_id="forged-runtime",
            timestamp="2026-08-31T12:00:00",
            reps_digest=ledger.reps_digest(model, "sapien"),
            inputs={
                "report": ledger_writes.provenance_file_record(__file__),
                "task": {"asset_key": asset_key, "model_id": model["model_id"]},
            },
            thresholds={
                "max_late_drift_m": 0.002,
                "min_support_z_m": -0.005,
                "max_tilt_deg": 181.0,
            },
            result={
                "schema": "asset_settle_result.v2",
                "finite": True,
                "late_drift_m": 0.0,
                "support_z_m": 0.0,
                "tilt_deg": 0.0,
                "rest_orientation_wxyz": list(
                    model["physical"]["conventions"]["stable_poses"][0]["orientation_wxyz"]
                ),
                "origin_z_m": 0.0,
                "derived_z_policy": "origin_on_table",
                "details": {},
            },
            model_entry=model,
            execution_snapshot=snapshot,
            runtime_capability=fake_runtime,
        )


def test_qualified_issuer_has_no_authority_from_a_mutable_registry_entry(tmp_path, monkeypatch):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    asset_dir = _file_back_model(tmp_path / "execution", model)
    snapshot = ledger_writes.publish_execution_snapshot(
        asset_dir / "verification_inputs",
        source_asset_dir=asset_dir,
        asset_key=asset_key,
        model_entry=model,
    )
    fake_script = tmp_path / "arbitrary_issuer.py"
    fake_script.write_text("print('not a production issuer')\n")
    monkeypatch.setitem(
        ledger.QUALIFIED_VERIFICATION_ISSUERS,
        "asset.arbitrary_issuer.v1",
        {
            **ledger.QUALIFIED_VERIFICATION_ISSUERS["asset.settle_repair.v1"],
            "script": str(fake_script),
        },
    )

    with pytest.raises(ledger_writes.VerificationEvidenceError, match="not qualified"):
        ledger_writes.issue_qualified_verification(
            issuer="asset.arbitrary_issuer.v1",
            asset_key=asset_key,
            model_id=model["model_id"],
            run_id="mutable-registry",
            timestamp="2026-08-31T12:00:00",
            reps_digest=ledger.reps_digest(model, "sapien"),
            inputs={
                "report": ledger_writes.provenance_file_record(__file__),
                "task": {"asset_key": asset_key, "model_id": model["model_id"]},
            },
            thresholds={
                "max_late_drift_m": 0.002,
                "min_support_z_m": -0.005,
                "max_tilt_deg": 181.0,
            },
            result={
                "schema": "asset_settle_result.v2",
                "finite": True,
                "late_drift_m": 0.0,
                "support_z_m": 0.0,
                "tilt_deg": 0.0,
                "rest_orientation_wxyz": list(
                    model["physical"]["conventions"]["stable_poses"][0]["orientation_wxyz"]
                ),
                "origin_z_m": 0.0,
                "derived_z_policy": "origin_on_table",
                "details": {},
            },
            model_entry=model,
            execution_snapshot=snapshot,
            runtime_capability=qualified_runtime_capability(
                tmp_path / "runtime",
                "asset.settle_repair.v1",
                ledger,
                ledger_writes,
            ),
        )


def test_qualified_issuer_description_cannot_mutate_the_sealed_registry():
    first = ledger.qualified_verification_issuer("asset.s11_runtime_load.v1")
    original_steps = first["runtime"]["config"]["steps"]
    first["runtime"]["config"]["steps"] = original_steps + 1

    second = ledger.qualified_verification_issuer("asset.s11_runtime_load.v1")

    assert second["runtime"]["config"]["steps"] == original_steps


def test_qualified_issuer_derives_settle_verdict_from_strict_result(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    payload = _qualified_evidence_payload(
        workspace=tmp_path / "execution",
        asset_key=asset_key,
        model=model,
        inputs={
            "report": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": asset_key, "model_id": model["model_id"]},
        },
        thresholds={
            "max_late_drift_m": 0.002,
            "min_support_z_m": -0.005,
            "max_tilt_deg": 181.0,
        },
        result={
            "schema": "asset_settle_result.v2",
            "finite": True,
            "late_drift_m": 0.0,
            "support_z_m": 0.0,
            "tilt_deg": 0.0,
            "rest_orientation_wxyz": list(
                model["physical"]["conventions"]["stable_poses"][0]["orientation_wxyz"]
            ),
            "origin_z_m": 0.0,
            "derived_z_policy": "origin_on_table",
            "details": {},
        },
    )
    assert payload["verdict"] == "pass"
    record = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    model["verification"] = [ledger_writes.receipt_from_evidence(payload, record)]

    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is not None


@pytest.mark.parametrize(
    ("thresholds", "result"),
    [
        (
            {
                "max_late_drift_m": 0.002,
                "min_support_z_m": -0.005,
                "max_tilt_deg": 181.0,
            },
            {},
        ),
        (
            {},
            {
                "schema": "asset_settle_result.v2",
                "finite": True,
                "late_drift_m": 0.0,
                "support_z_m": 0.0,
                "tilt_deg": 0.0,
                "rest_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "origin_z_m": 0.0,
                "derived_z_policy": "origin_on_table",
                "details": {},
            },
        ),
    ],
)
def test_qualified_issuer_rejects_empty_decision_documents(tmp_path, thresholds, result):
    document = make_valid()
    model = document["models"][0]
    with pytest.raises(ledger_writes.VerificationEvidenceError):
        _qualified_evidence_payload(
            workspace=tmp_path / "execution",
            asset_key=document["external_ids"]["env_gen"],
            model=model,
            inputs={
                "report": ledger_writes.provenance_file_record(__file__),
                "task": {"asset_key": document["external_ids"]["env_gen"]},
            },
            thresholds=thresholds,
            result=result,
        )


def test_qualified_issuer_cannot_turn_failing_facts_into_pass(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    payload = _qualified_evidence_payload(
        workspace=tmp_path / "execution",
        asset_key=asset_key,
        model=model,
        thresholds={
            "max_late_drift_m": 0.002,
            "min_support_z_m": -0.005,
            "max_tilt_deg": 181.0,
        },
        result={
            "schema": "asset_settle_result.v2",
            "finite": True,
            "late_drift_m": 0.5,
            "support_z_m": 0.0,
            "tilt_deg": 0.0,
            "rest_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "origin_z_m": 0.0,
            "derived_z_policy": "origin_on_table",
            "details": {"producer_claimed_verdict": "pass"},
        },
    )

    assert payload["verdict"] == "fail"


def test_qualified_issuer_rejects_thresholds_outside_closed_policy(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]

    with pytest.raises(ledger_writes.VerificationEvidenceError, match="threshold policy"):
        _qualified_evidence_payload(
            workspace=tmp_path / "execution",
            asset_key=asset_key,
            model=model,
            thresholds={
                "max_late_drift_m": 1_000_000.0,
                "min_support_z_m": -1_000_000.0,
                "max_tilt_deg": 181.0,
            },
            result={
                "schema": "asset_settle_result.v2",
                "finite": True,
                "late_drift_m": 500_000.0,
                "support_z_m": -500_000.0,
                "tilt_deg": 180.0,
                "rest_orientation_wxyz": list(
                    model["physical"]["conventions"]["stable_poses"][0]["orientation_wxyz"]
                ),
                "origin_z_m": 0.0,
                "derived_z_policy": "origin_on_table",
                "details": {},
            },
        )


def test_qualified_issuer_rejects_self_consistent_snapshot_with_forged_role(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    asset_dir = _file_back_model(tmp_path / "authoritative", model)
    valid = ledger_writes.publish_execution_snapshot(
        tmp_path / "snapshots",
        source_asset_dir=asset_dir,
        asset_key=asset_key,
        model_entry=model,
    )
    valid_root = Path(ledger.resolve_uri(valid["root_uri"]))
    manifest = json.loads((valid_root / "manifest.json").read_text())
    manifest["roles"][0]["role"] = "collision"
    manifest_bytes = ledger.canonical_json_bytes(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    forged_root = tmp_path / "snapshots" / f"{manifest_digest}.execution"
    shutil.copytree(valid_root, forged_root)
    for path in forged_root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    (forged_root / "manifest.json").write_bytes(manifest_bytes)
    for path in forged_root.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    forged_root.chmod(0o555)
    forged = {
        **valid,
        "root_uri": ledger.to_portable_uri(forged_root),
        "manifest": ledger_writes.provenance_file_record(forged_root / "manifest.json"),
    }
    assert ledger.execution_snapshot_is_current(forged)

    with pytest.raises(ledger_writes.VerificationEvidenceError, match="snapshot"):
        _qualified_evidence_payload(
            workspace=tmp_path / "authoritative",
            asset_key=asset_key,
            model=model,
            execution_snapshot=forged,
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("z_policy", "center_on_table"),
        ("orientation_wxyz", [1.0, 0.0, 0.0, 0.0]),
    ],
)
def test_trusted_settle_binds_promoted_pose_and_z_policy(tmp_path, field, replacement):
    asset_key, model, _record = _trusted_model(tmp_path)

    if field == "z_policy":
        model["physical"]["conventions"][field] = replacement
    else:
        model["physical"]["conventions"]["stable_poses"][0][field] = replacement

    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None


def test_qualified_settle_rejects_z_policy_inconsistent_with_measured_origin(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    model["physical"]["conventions"]["z_policy"] = "center_on_table"

    with pytest.raises(ledger_writes.VerificationEvidenceError, match="z_policy"):
        _qualified_evidence_payload(
            workspace=tmp_path / "execution",
            asset_key=asset_key,
            model=model,
            result={
                "schema": "asset_settle_result.v2",
                "finite": True,
                "late_drift_m": 0.0,
                "support_z_m": 0.0,
                "tilt_deg": 0.0,
                "rest_orientation_wxyz": list(
                    model["physical"]["conventions"]["stable_poses"][0]["orientation_wxyz"]
                ),
                "origin_z_m": 0.0,
                "derived_z_policy": "origin_on_table",
                "details": {},
            },
        )


def test_trusted_joint_sweep_binds_projected_placement_facts(tmp_path):
    asset_key, model, _record = _trusted_model(tmp_path, check="joint_sweep")

    model["physical"]["conventions"]["stable_poses"][0]["pose_id"] = "forged"

    assert ledger.latest_trusted_verification(model, "sapien", "joint_sweep", asset_key) is None


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("z_policy", "center_on_table"),
        ("orientation_wxyz", [1.0, 0.0, 0.0, 0.0]),
    ],
)
def test_trusted_runtime_load_binds_its_executed_stable_placement(tmp_path, field, replacement):
    asset_key, model, _record = _trusted_model(tmp_path, check="runtime_load")

    if field == "z_policy":
        model["physical"]["conventions"][field] = replacement
    else:
        model["physical"]["conventions"]["stable_poses"][0][field] = replacement

    assert ledger.latest_trusted_verification(model, "sapien", "runtime_load", asset_key) is None


@pytest.mark.parametrize(
    ("record_field", "replacement"),
    [
        ("sha256", "f" * 64),
        ("bytes", 1),
        ("schema", "asset_verification_evidence.v0"),
        ("run_id", "another-run"),
        ("invocation_digest", "e" * 64),
        ("capability_sha256", "d" * 64),
    ],
)
def test_trusted_verification_rejects_any_artifact_record_mismatch(
    tmp_path, record_field, replacement
):
    asset_key, model, _ = _trusted_model(tmp_path)
    model["verification"][0]["evidence"][record_field] = replacement

    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None


def test_trusted_verification_rejects_noncanonical_bytes_even_when_file_hash_matches(tmp_path):
    asset_key, model, record = _trusted_model(tmp_path)
    original = Path(record["uri"])
    if not original.is_absolute():
        original = ledger.resolve_uri(record["uri"])
    parsed = json.loads(original.read_text())
    noncanonical = json.dumps(parsed, indent=2).encode() + b"\n"
    digest = hashlib.sha256(noncanonical).hexdigest()
    replacement = original.with_name(f"{digest}.verification.json")
    replacement.write_bytes(noncanonical)
    receipt_record = model["verification"][0]["evidence"]
    receipt_record.update(uri=str(replacement), sha256=digest, bytes=len(noncanonical))

    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None


def test_trusted_verification_rejects_artifact_symlink(tmp_path):
    asset_key, model, record = _trusted_model(tmp_path)
    original = Path(record["uri"])
    if not original.is_absolute():
        original = ledger.resolve_uri(record["uri"])
    linked_dir = tmp_path / "linked-evidence"
    linked_dir.symlink_to(original.parent, target_is_directory=True)
    model["verification"][0]["evidence"]["uri"] = str(linked_dir / original.name)

    assert ledger.latest_trusted_verification(model, "sapien", "settle", asset_key) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("backend", "unknown"),
        ("check", "unknown"),
        ("verdict", "unknown"),
        ("run_id", 7),
        ("run_id", ""),
        ("timestamp", "yesterday"),
        ("reps_digest", "short"),
        ("inputs", []),
        ("inputs", {}),
        ("thresholds", []),
        ("result", []),
    ],
)
def test_evidence_builder_rejects_noncanonical_contract_fields(field, value):
    document = make_valid()
    model = document["models"][0]
    with pytest.raises(ledger_writes.VerificationEvidenceError):
        _evidence_payload(
            asset_key=document["external_ids"]["env_gen"],
            model=model,
            **{field: value},
        )


def test_evidence_builder_rejects_nonregular_script(tmp_path):
    document = make_valid()
    with pytest.raises(ledger_writes.VerificationEvidenceError, match="not regular"):
        _evidence_payload(
            asset_key=document["external_ids"]["env_gen"],
            model=document["models"][0],
            script_path=tmp_path,
        )


def test_evidence_builder_requires_task_and_current_regular_file_inputs(tmp_path):
    document = make_valid()
    asset_key = document["external_ids"]["env_gen"]
    model = document["models"][0]
    report = tmp_path / "runtime-report.json"
    report.write_bytes(b'{"status":"pass"}\n')
    report_record = ledger_writes.provenance_file_record(report)

    bad_inputs = [
        {"report": report_record},
        {"task": {"asset_key": asset_key}},
        {"report": report_record, "task": {}},
        {"report": {"uri": str(report), "sha256": "a" * 64}, "task": {"run": 1}},
    ]
    linked_report = tmp_path / "linked-report.json"
    linked_report.symlink_to(report)
    bad_inputs.append(
        {
            "report": {**report_record, "uri": str(linked_report)},
            "task": {"asset_key": asset_key},
        }
    )
    report.write_bytes(b'{"status":"fail"}\n')
    bad_inputs.append({"report": report_record, "task": {"asset_key": asset_key}})

    for inputs in bad_inputs:
        with pytest.raises(ledger_writes.VerificationEvidenceError, match="inputs"):
            _evidence_payload(asset_key=asset_key, model=model, inputs=inputs)


def test_artifact_file_record_recheck_fails_closed_for_unsafe_or_changed_targets(tmp_path):
    artifact = tmp_path / "report.json"
    artifact.write_bytes(b"report")
    record = ledger_writes.provenance_file_record(artifact)
    assert ledger.artifact_file_record_is_current(record)

    for uri in ("https://example.test/report.json", "bad\\report.json", "bad\nreport.json"):
        assert not ledger.artifact_file_record_is_current({**record, "uri": uri})
    assert not ledger.artifact_file_record_is_current(
        {**record, "uri": str(tmp_path / "missing.json")}
    )
    assert not ledger.artifact_file_record_is_current({**record, "bytes": record["bytes"] + 1})
    assert not ledger.artifact_file_record_is_current({**record, "uri": str(tmp_path), "bytes": 0})


def test_evidence_publisher_rejects_symlink_parent_and_content_collision(tmp_path):
    document = make_valid()
    payload = _evidence_payload(
        asset_key=document["external_ids"]["env_gen"], model=document["models"][0]
    )
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ledger_writes.VerificationEvidenceError, match="symlink"):
        ledger_writes.publish_verification_evidence(linked_parent / "evidence", payload)

    record = ledger_writes.publish_verification_evidence(real_parent / "evidence", payload)
    # Exact retry is idempotent.
    assert ledger_writes.publish_verification_evidence(real_parent / "evidence", payload) == record
    artifact = Path(record["uri"])
    if not artifact.is_absolute():
        artifact = ledger.resolve_uri(record["uri"])
    artifact.chmod(0o644)
    artifact.write_bytes(b"x" * record["bytes"])
    with pytest.raises(ledger_writes.VerificationEvidenceError, match="different bytes"):
        ledger_writes.publish_verification_evidence(real_parent / "evidence", payload)


def test_receipt_builder_and_ledger_validator_reject_bad_artifact_record(tmp_path):
    document = make_valid()
    model = document["models"][0]
    payload = _evidence_payload(asset_key=document["external_ids"]["env_gen"], model=model)
    record = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    with pytest.raises(ledger_writes.VerificationEvidenceError):
        ledger_writes.receipt_from_evidence(payload, {**record, "run_id": "wrong"})

    model["verification"][0]["evidence"] = {"uri": "incomplete"}
    assert "bad_evidence_record" in {v.code for v in ledger.validate_ledger(document)}


def _replace_nested(document, path, value):
    node = document
    for field in path[:-1]:
        node = node[field]
    node[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema",), "asset_verification_evidence.v0"),
        (("asset_key",), "wrong_asset"),
        (("model_id",), 8),
        (("backend",), "bad"),
        (("check",), "bad"),
        (("check",), "generation_qc"),
        (("verdict",), "bad"),
        (("run_id",), 7),
        (("run_id",), ""),
        (("timestamp",), "bad"),
        (("reps_digest",), "bad"),
        (("result",), []),
        (("capability",), []),
        (("invocation",), []),
        (("capability", "schema"), "bad"),
        (("capability", "backend"), "portable"),
        (("capability", "check"), "runtime_load"),
        (("capability", "interpreter"), {}),
        (("capability", "script"), {}),
        (("capability", "thresholds"), []),
        (("invocation", "schema"), "bad"),
        (("invocation", "asset_key"), "wrong_asset"),
        (("invocation", "model_id"), 8),
        (("invocation", "backend"), "portable"),
        (("invocation", "check"), "runtime_load"),
        (("invocation", "reps_digest"), "e" * 64),
        (("invocation", "interpreter"), {}),
        (("invocation", "script"), {}),
        (("invocation", "thresholds"), {}),
        (("invocation", "inputs"), []),
        (("invocation", "inputs"), {}),
        (("invocation", "capability_sha256"), "d" * 64),
    ],
)
def test_trusted_verifier_cross_checks_every_envelope_binding(tmp_path, path, value):
    asset_key, model, record = _trusted_model(tmp_path / "original")
    original = Path(record["uri"])
    if not original.is_absolute():
        original = ledger.resolve_uri(record["uri"])
    payload = json.loads(original.read_text())
    _replace_nested(payload, path, value)
    mutated = ledger_writes.publish_verification_evidence(tmp_path / "mutated", payload)

    assert ledger.verification_from_trusted_evidence(model, mutated, asset_key) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("implementation", "ForgedPython"),
        ("version", "0.0.0"),
        ("executable", None),
    ],
)
def test_trusted_verifier_rejects_consistently_resigned_fake_interpreter(tmp_path, field, value):
    asset_key, model, record = _trusted_model(tmp_path / "original")
    artifact = Path(ledger.resolve_uri(record["uri"]))
    payload = json.loads(artifact.read_text())
    if field == "executable":
        value = ledger_writes.provenance_file_record(__file__)
    payload["capability"]["interpreter"][field] = value
    payload["invocation"]["interpreter"][field] = value
    capability_digest = hashlib.sha256(
        ledger.canonical_json_bytes(payload["capability"])
    ).hexdigest()
    payload["capability_sha256"] = capability_digest
    payload["invocation"]["capability_sha256"] = capability_digest
    payload["invocation_digest"] = hashlib.sha256(
        ledger.canonical_json_bytes(payload["invocation"])
    ).hexdigest()
    forged = ledger_writes.publish_verification_evidence(tmp_path / "forged", payload)

    assert ledger.verification_from_trusted_evidence(model, forged, asset_key) is None


def test_trusted_verifier_rejects_extra_fields_invalid_identity_and_representation_drift(
    tmp_path,
):
    asset_key, model, record = _trusted_model(tmp_path)
    artifact = Path(record["uri"])
    if not artifact.is_absolute():
        artifact = ledger.resolve_uri(record["uri"])
    payload = json.loads(artifact.read_text())
    payload["extra"] = True
    extra_record = ledger_writes.publish_verification_evidence(tmp_path / "extra", payload)
    assert ledger.verification_from_trusted_evidence(model, extra_record, asset_key) is None
    assert ledger.verification_from_trusted_evidence(model, record, "../bad") is None
    assert ledger.verification_from_trusted_evidence({}, record, asset_key) is None

    no_representation = json.loads(json.dumps(model))
    no_representation["representations"] = []
    assert ledger.verification_from_trusted_evidence(no_representation, record, asset_key) is None
    stale = json.loads(json.dumps(model))
    stale["representations"][0]["metadata"] = {"changed": True}
    assert ledger.verification_from_trusted_evidence(stale, record, asset_key) is None


@pytest.mark.parametrize("uri", ["https://example.test/evidence", "bad\\name", "bad\nname"])
def test_trusted_verifier_rejects_nonlocal_or_nonportable_artifact_uri(tmp_path, uri):
    asset_key, model, record = _trusted_model(tmp_path)
    record["uri"] = uri
    assert ledger.verification_from_trusted_evidence(model, record, asset_key) is None


def test_trusted_verifier_rejects_missing_nonregular_and_malformed_artifacts(tmp_path):
    asset_key, model, record = _trusted_model(tmp_path / "original")
    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    missing = {**record, "uri": str(missing_dir / f"{record['sha256']}.verification.json")}
    assert ledger.verification_from_trusted_evidence(model, missing, asset_key) is None

    directory_artifact = tmp_path / f"{record['sha256']}.verification.json"
    directory_artifact.mkdir()
    nonregular = {**record, "uri": str(directory_artifact)}
    assert ledger.verification_from_trusted_evidence(model, nonregular, asset_key) is None

    malformed = b"{not-json}\n"
    digest = hashlib.sha256(malformed).hexdigest()
    malformed_path = tmp_path / f"{digest}.verification.json"
    malformed_path.write_bytes(malformed)
    malformed_record = {
        **record,
        "uri": str(malformed_path),
        "sha256": digest,
        "bytes": len(malformed),
    }
    assert ledger.verification_from_trusted_evidence(model, malformed_record, asset_key) is None

    scalar = b"[]\n"
    scalar_digest = hashlib.sha256(scalar).hexdigest()
    scalar_path = tmp_path / f"{scalar_digest}.verification.json"
    scalar_path.write_bytes(scalar)
    scalar_record = {
        **record,
        "uri": str(scalar_path),
        "sha256": scalar_digest,
        "bytes": len(scalar),
    }
    assert ledger.verification_from_trusted_evidence(model, scalar_record, asset_key) is None


def test_trusted_verifier_rejects_same_size_content_tampering(tmp_path):
    asset_key, model, record = _trusted_model(tmp_path)
    artifact = Path(record["uri"])
    if not artifact.is_absolute():
        artifact = ledger.resolve_uri(record["uri"])
    payload = artifact.read_bytes()
    artifact.chmod(0o644)
    artifact.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])

    assert ledger.verification_from_trusted_evidence(model, record, asset_key) is None

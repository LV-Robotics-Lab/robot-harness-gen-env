import fcntl
import hashlib
import json
import os
import sys
import threading
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import ledger, ledger_writes  # noqa: E402
from tests.test_ledger import make_valid  # noqa: E402
from tests.trusted_fixtures import qualified_runtime_capability  # noqa: E402


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_backed_ledger(tmp_path):
    document = make_valid()
    model = document["models"][0]
    visual = tmp_path / "visual.ply"
    visual.write_text("ply\nformat ascii 1.0\nend_header\n")
    snapshot = tmp_path / "snapshot.png"
    snapshot.write_bytes(b"PNG-fixture")
    for representation, path in zip(model["representations"], (visual, snapshot)):
        representation["format"] = path.suffix.lstrip(".")
        representation["uri"] = str(path)
        representation["sha256"] = _sha256(path)
        representation["files"] = [
            {
                "uri": str(path),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
        ]
    model["verification"][0]["verified_digest"] = ledger.reps_digest(model, "sapien")
    assert ledger.validate_ledger(document, check_files=True) == []
    return document


def test_representation_files_captures_recursive_loader_closure_canonically(tmp_path):
    texture = tmp_path / "textures" / "albedo.png"
    texture.parent.mkdir()
    texture.write_bytes(b"pixels")
    material = tmp_path / "material.mtl"
    material.write_text("newmtl default\nmap_Kd textures/albedo.png\n")
    mesh = tmp_path / "mesh.obj"
    mesh.write_text("mtllib material.mtl\nv 0 0 0\n")

    files = ledger_writes.representation_files(mesh)

    assert [Path(member["uri"]).name for member in files] == [
        "material.mtl",
        "mesh.obj",
        "albedo.png",
    ]
    assert [member["uri"] for member in files] == sorted(member["uri"] for member in files)
    assert {member["sha256"] for member in files} == {
        _sha256(mesh),
        _sha256(material),
        _sha256(texture),
    }
    assert {member["bytes"] for member in files} == {
        mesh.stat().st_size,
        material.stat().st_size,
        texture.stat().st_size,
    }


def test_execution_snapshot_is_content_addressed_read_only_and_revalidated(tmp_path):
    (tmp_path / "asset").mkdir()
    document = _file_backed_ledger(tmp_path / "asset")
    model = document["models"][0]

    snapshot = ledger_writes.publish_execution_snapshot(
        tmp_path / "asset" / "verification_inputs",
        source_asset_dir=tmp_path / "asset",
        asset_key=document["external_ids"]["env_gen"],
        model_entry=model,
    )

    root = Path(snapshot["root_uri"])
    if not root.is_absolute():
        root = ledger.resolve_uri(snapshot["root_uri"])
    assert root.name == f"{snapshot['manifest']['sha256']}.execution"
    assert ledger.execution_snapshot_is_current(snapshot)
    copied = root / "assets" / "objects" / document["external_ids"]["env_gen"] / "visual.ply"
    assert copied.read_bytes() == (tmp_path / "asset" / "visual.ply").read_bytes()
    assert not copied.samefile(tmp_path / "asset" / "visual.ply")
    assert copied.stat().st_mode & 0o222 == 0

    copied.chmod(0o644)
    copied.write_bytes(b"tampered snapshot")
    assert not ledger.execution_snapshot_is_current(snapshot)


def test_execution_snapshot_rejects_representation_outside_authoritative_asset(tmp_path):
    asset_dir = tmp_path / "asset"
    asset_dir.mkdir()
    document = _file_backed_ledger(asset_dir)
    outside = tmp_path / "outside.ply"
    outside.write_bytes(b"outside")
    representation = document["models"][0]["representations"][0]
    representation.update(uri=str(outside), sha256=_sha256(outside))
    representation["files"] = [
        {"uri": str(outside), "sha256": _sha256(outside), "bytes": outside.stat().st_size}
    ]

    with pytest.raises(ledger_writes.VerificationEvidenceError, match="authoritative asset"):
        ledger_writes.publish_execution_snapshot(
            asset_dir / "verification_inputs",
            source_asset_dir=asset_dir,
            asset_key=document["external_ids"]["env_gen"],
            model_entry=document["models"][0],
        )


def test_execution_snapshot_rejects_source_mutation_during_publication(tmp_path, monkeypatch):
    asset_dir = tmp_path / "asset"
    asset_dir.mkdir()
    document = _file_backed_ledger(asset_dir)
    source = asset_dir / "visual.ply"
    original_copy = ledger_writes._copy_snapshot_member

    def mutate_after_copy(source_path, target_path, expected):
        original_copy(source_path, target_path, expected)
        Path(source_path).write_bytes(b"changed-after-copy")

    monkeypatch.setattr(ledger_writes, "_copy_snapshot_member", mutate_after_copy)

    with pytest.raises(ledger_writes.VerificationEvidenceError, match="changed after"):
        ledger_writes.publish_execution_snapshot(
            asset_dir / "verification_inputs",
            source_asset_dir=asset_dir,
            asset_key=document["external_ids"]["env_gen"],
            model_entry=document["models"][0],
        )

    assert not list((asset_dir / "verification_inputs").glob("*.execution"))
    assert source.read_bytes() == b"changed-after-copy"


def test_execution_snapshot_rejects_unmanifested_empty_directory(tmp_path):
    asset_dir = tmp_path / "asset"
    asset_dir.mkdir()
    document = _file_backed_ledger(asset_dir)
    snapshot = ledger_writes.publish_execution_snapshot(
        asset_dir / "verification_inputs",
        source_asset_dir=asset_dir,
        asset_key=document["external_ids"]["env_gen"],
        model_entry=document["models"][0],
    )
    root = Path(ledger.resolve_uri(snapshot["root_uri"]))
    root.chmod(0o755)
    unexpected = root / "unmanifested-empty-directory"
    unexpected.mkdir()
    unexpected.chmod(0o555)
    root.chmod(0o555)

    assert not ledger.execution_snapshot_is_current(snapshot)


def test_runtime_capability_binds_loaded_module_files_and_config():
    capability = ledger_writes.capture_runtime_capability(
        loader_modules=[ledger_writes],
        sapien_module=ledger,
        entrypoint="tests.fake_loader",
        config={"timestep_s": 0.01, "loader": "fixed-snapshot"},
    )

    assert capability["entrypoint"] == "tests.fake_loader"
    assert capability["config"]["loader"] == "fixed-snapshot"
    assert {module["name"] for module in capability["loader_modules"]} >= {"lib.ledger_writes"}
    assert {module["name"] for module in capability["sapien_modules"]} >= {"lib.ledger"}
    assert ledger.runtime_capability_is_current(capability)


def test_runtime_capability_requires_exact_native_module_tree(tmp_path, monkeypatch):
    package_file = tmp_path / "qualified_sapien.py"
    package_file.write_text("# package\n")
    native_file = tmp_path / "qualified_sapien_native.so"
    native_file.write_bytes(b"native-binary-fixture")
    package = types.ModuleType("qualified_sapien")
    package.__file__ = str(package_file)
    native = types.ModuleType("qualified_sapien._native")
    native.__file__ = str(native_file)
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, native.__name__, native)

    capability = ledger_writes.capture_runtime_capability(
        loader_modules=[package],
        sapien_module=package,
        entrypoint="tests.native_loader",
        config={"loader": "fixed-snapshot"},
    )

    assert capability["native_modules"] == ["qualified_sapien._native"]
    omitted = json.loads(json.dumps(capability))
    omitted["native_modules"] = []
    forged = json.loads(json.dumps(capability))
    forged["native_modules"] = ["qualified_sapien"]
    assert not ledger.runtime_capability_is_current(omitted)
    assert not ledger.runtime_capability_is_current(forged)


def test_qualified_runtime_fixture_restores_the_process_module_registry(tmp_path):
    module_names = ("sapien", "sapien._native", "envs", "envs.utils")
    missing = object()
    before = {name: sys.modules.get(name, missing) for name in module_names}

    capability = qualified_runtime_capability(
        tmp_path,
        "asset.s11_runtime_load.v1",
        ledger,
        ledger_writes,
    )

    assert capability["native_modules"] == ["sapien._native"]
    assert all(sys.modules.get(name, missing) is before[name] for name in module_names)


def test_write_validated_fails_closed_before_replacing_existing_ledger(tmp_path):
    destination = tmp_path / "ledger.json"
    destination.write_text('{"sentinel": true}\n')
    document = _file_backed_ledger(tmp_path)
    Path(document["models"][0]["representations"][0]["uri"]).write_text("tampered")

    with pytest.raises(ledger_writes.LedgerWriteError) as exc_info:
        ledger_writes.write_validated(destination, document)

    assert "sha256_mismatch" in {v.code for v in exc_info.value.violations}
    assert json.loads(destination.read_text()) == {"sentinel": True}


def test_write_validated_rolls_back_when_representation_changes_at_replace(tmp_path, monkeypatch):
    destination = tmp_path / "ledger.json"
    expected = _file_backed_ledger(tmp_path)
    ledger.write_ledger(destination, expected)
    before = destination.read_bytes()
    candidate = json.loads(json.dumps(expected))
    candidate["semantics"]["aliases"] = ["updated"]
    payload = Path(candidate["models"][0]["representations"][0]["uri"])
    real_replace = ledger.os.replace
    injected = False

    def drift_before_replace(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            payload.write_text("changed-after-final-pre-replace-gate")
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(ledger.os, "replace", drift_before_replace)

    with pytest.raises(ledger_writes.LedgerWriteError):
        ledger_writes.write_validated(destination, candidate, expected=expected)

    assert destination.read_bytes() == before


def test_new_ledger_is_removed_when_representation_changes_at_replace(tmp_path, monkeypatch):
    destination = tmp_path / "ledger.json"
    document = _file_backed_ledger(tmp_path)
    payload = Path(document["models"][0]["representations"][0]["uri"])
    real_replace = ledger.os.replace
    injected = False

    def drift_before_replace(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            payload.write_text("changed-at-new-ledger-publication")
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(ledger.os, "replace", drift_before_replace)

    with pytest.raises(ledger_writes.LedgerWriteError):
        ledger_writes.write_validated(destination, document)

    assert not destination.exists()


def test_append_validated_requires_evidence_digest_and_keeps_disk_unchanged(tmp_path):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, document)
    before = destination.read_bytes()
    entry = {
        "backend": "sapien",
        "check": "runtime_load",
        "verdict": "pass",
        "run_id": "runtime-attack",
        "timestamp": "2026-08-31T12:00:00",
        "verified_digest": "f" * 64,
    }

    with pytest.raises(ledger_writes.EvidenceDigestError):
        ledger_writes.append_validated_verification(destination, 0, entry)

    assert destination.read_bytes() == before


def _trusted_runtime_entry(tmp_path, document):
    model = document["models"][0]
    asset_key = document["external_ids"]["env_gen"]
    digest = ledger.reps_digest(model, "sapien")
    payload = ledger_writes.issue_qualified_verification(
        issuer="asset.s11_runtime_load.v1",
        asset_key=asset_key,
        model_id=model["model_id"],
        run_id="direct-append",
        timestamp="2026-08-31T12:00:00",
        reps_digest=digest,
        inputs={
            "fixture": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": asset_key, "model_id": model["model_id"]},
        },
        thresholds={"max_late_drift_m": 0.002, "min_final_z_m": -0.005},
        result={
            "schema": "asset_runtime_load_result.v3",
            "loaded": True,
            "finite": True,
            "late_drift_m": 0.0,
            "final_z_m": 0.0,
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
            "details": {},
        },
        model_entry=model,
        execution_snapshot=ledger_writes.publish_execution_snapshot(
            tmp_path / "verification_inputs",
            source_asset_dir=tmp_path,
            asset_key=asset_key,
            model_entry=model,
        ),
        runtime_capability=qualified_runtime_capability(
            tmp_path / "runtime",
            "asset.s11_runtime_load.v1",
            ledger,
            ledger_writes,
        ),
    )
    record = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    return ledger_writes.receipt_from_evidence(payload, record)


def test_evidence_write_failure_never_publishes_a_partial_digest_path(tmp_path, monkeypatch):
    document = _file_backed_ledger(tmp_path)
    receipt = _trusted_runtime_entry(tmp_path, document)
    payload = json.loads(Path(ledger.resolve_uri(receipt["evidence"]["uri"])).read_text())
    evidence_dir = tmp_path / "interrupted-evidence"
    digest = hashlib.sha256(ledger.canonical_json_bytes(payload)).hexdigest()
    final_path = evidence_dir / f"{digest}.verification.json"
    real_write = ledger_writes.os.write
    injected = False

    def fail_after_partial_write(fd, data):
        nonlocal injected
        if not injected:
            injected = True
            real_write(fd, data[:1])
            raise OSError("injected evidence write interruption")
        return real_write(fd, data)

    monkeypatch.setattr(ledger_writes.os, "write", fail_after_partial_write)
    with pytest.raises(OSError, match="interruption"):
        ledger_writes.publish_verification_evidence(evidence_dir, payload)

    assert not final_path.exists()
    monkeypatch.setattr(ledger_writes.os, "write", real_write)
    record = ledger_writes.publish_verification_evidence(evidence_dir, payload)
    assert Path(ledger.resolve_uri(record["uri"])).read_bytes() == ledger.canonical_json_bytes(
        payload
    )


def test_concurrent_identical_evidence_publishers_only_observe_complete_bytes(
    tmp_path, monkeypatch
):
    document = _file_backed_ledger(tmp_path)
    receipt = _trusted_runtime_entry(tmp_path, document)
    payload = json.loads(Path(ledger.resolve_uri(receipt["evidence"]["uri"])).read_text())
    evidence_dir = tmp_path / "concurrent-evidence"
    slow_started = threading.Event()
    release_slow = threading.Event()
    real_write = ledger_writes.os.write
    paused = False
    records = []
    errors = []

    def controlled_write(fd, data):
        nonlocal paused
        if threading.current_thread().name == "slow-evidence-writer" and not paused:
            paused = True
            written = real_write(fd, data[:1])
            slow_started.set()
            assert release_slow.wait(timeout=5)
            return written
        return real_write(fd, data)

    def publish():
        try:
            records.append(ledger_writes.publish_verification_evidence(evidence_dir, payload))
        except Exception as exc:  # noqa: BLE001 - thread propagates via assertion below
            errors.append(exc)

    monkeypatch.setattr(ledger_writes.os, "write", controlled_write)
    slow = threading.Thread(target=publish, name="slow-evidence-writer")
    fast = threading.Thread(target=publish, name="fast-evidence-writer")
    slow.start()
    assert slow_started.wait(timeout=5)
    fast.start()
    fast.join(timeout=5)
    release_slow.set()
    slow.join(timeout=5)

    assert not slow.is_alive() and not fast.is_alive()
    assert errors == []
    assert len(records) == 2
    assert records[0] == records[1]
    assert Path(ledger.resolve_uri(records[0]["uri"])).read_bytes() == ledger.canonical_json_bytes(
        payload
    )


def test_append_validated_enforces_location_identity_and_trusted_artifact(tmp_path):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, document)
    asset_key = document["external_ids"]["env_gen"]
    entry = _trusted_runtime_entry(tmp_path, document)

    with pytest.raises(ledger.UnsafeAssetKeyError):
        ledger_writes.append_validated_verification(destination, 0, entry, asset_key="../bad")
    with pytest.raises(ledger.LedgerIdentityError):
        ledger_writes.append_validated_verification(destination, 0, entry, asset_key="wrong_asset")
    with pytest.raises(ledger_writes.EvidenceDigestError, match="immutable"):
        ledger_writes.append_validated_verification(
            destination, 0, {**entry, "verdict": "fail"}, asset_key=asset_key
        )

    written = ledger_writes.append_validated_verification(
        destination, 0, entry, asset_key=asset_key
    )
    assert written["models"][0]["verification"][-1] == entry


def test_representation_files_rejects_missing_symlinked_and_opaque_members(tmp_path):
    with pytest.raises(ledger_writes.RepresentationClosureError, match="missing"):
        ledger_writes.representation_files(tmp_path / "missing.obj")

    target = tmp_path / "target.ply"
    target.write_text("mesh")
    linked = tmp_path / "linked.ply"
    linked.symlink_to(target)
    with pytest.raises(ledger_writes.RepresentationClosureError, match="symlink"):
        ledger_writes.representation_files(linked)

    opaque = tmp_path / "mesh.opaque"
    opaque.write_bytes(b"unknown")
    with pytest.raises(ledger_writes.RepresentationClosureError, match="unsupported"):
        ledger_writes.representation_files(opaque)


def test_representation_files_handles_cycles_and_declared_length_references(tmp_path):
    cyclic = tmp_path / "cyclic.obj"
    cyclic.write_text("mtllib cyclic.obj\n")
    assert [member["uri"] for member in ledger_writes.representation_files(cyclic)] == [str(cyclic)]

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"data")
    gltf = tmp_path / "scene.gltf"
    gltf.write_text(json.dumps({"buffers": [{"uri": "payload.bin", "byteLength": 4}]}))
    assert {Path(member["uri"]).name for member in ledger_writes.representation_files(gltf)} == {
        "scene.gltf",
        "payload.bin",
    }


def test_write_validated_writes_a_valid_candidate(tmp_path):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"

    ledger_writes.write_validated(destination, document)

    assert json.loads(destination.read_text()) == document


def test_write_validated_cas_preserves_concurrent_receipt(tmp_path):
    expected = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, expected)
    candidate = json.loads(json.dumps(expected))
    candidate["semantics"]["aliases"].append("updated-by-whole-writer")
    receipt = _runtime_entry(expected, run_id="concurrent-receipt")
    ledger_writes.append_validated_verification(destination, 0, receipt)

    with pytest.raises(ledger_writes.ConcurrentLedgerUpdateError):
        ledger_writes.write_validated(destination, candidate, expected=expected)

    written = json.loads(destination.read_text())
    assert receipt in written["models"][0]["verification"]
    assert "updated-by-whole-writer" not in written["semantics"]["aliases"]


def test_write_validated_requires_and_honors_expected_document(tmp_path):
    expected = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, expected)
    candidate = json.loads(json.dumps(expected))
    candidate["semantics"]["aliases"].append("updated")

    with pytest.raises(ledger_writes.ConcurrentLedgerUpdateError, match="requires"):
        ledger_writes.write_validated(destination, candidate)

    ledger_writes.write_validated(destination, candidate, expected=expected)
    assert json.loads(destination.read_text()) == candidate


def test_write_validated_rejects_disappeared_expected_document(tmp_path):
    expected = _file_backed_ledger(tmp_path)
    destination = tmp_path / "missing-ledger.json"

    with pytest.raises(ledger_writes.ConcurrentLedgerUpdateError, match="disappeared"):
        ledger_writes.write_validated(destination, expected, expected=expected)


def _runtime_entry(document, **updates):
    entry = {
        "backend": "sapien",
        "check": "runtime_load",
        "verdict": "pass",
        "run_id": "runtime-good",
        "timestamp": "2026-08-31T12:00:00",
        "verified_digest": ledger.reps_digest(document["models"][0], "sapien"),
    }
    entry.update(updates)
    return entry


def test_append_validated_writes_once_and_deduplicates(tmp_path):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, document)
    entry = _runtime_entry(document)

    updated = ledger_writes.append_validated_verification(destination, 0, entry)
    first_bytes = destination.read_bytes()
    duplicate = ledger_writes.append_validated_verification(destination, 0, entry)

    assert updated["models"][0]["verification"][-1] == entry
    assert duplicate == updated
    assert destination.read_bytes() == first_bytes


def test_append_validated_rejects_conflicting_retry_without_mutation(tmp_path):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, document)
    entry = _runtime_entry(document)
    ledger_writes.append_validated_verification(destination, 0, entry)
    before = destination.read_bytes()

    with pytest.raises(ledger.VerificationConflictError):
        ledger_writes.append_validated_verification(
            destination,
            0,
            {**entry, "verdict": "fail"},
        )

    assert destination.read_bytes() == before


def test_append_validated_rejects_backend_without_representation(tmp_path):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, document)
    before = destination.read_bytes()
    entry = _runtime_entry(document, backend="isaacsim")

    with pytest.raises(ledger.RepresentationDigestError):
        ledger_writes.append_validated_verification(destination, 0, entry)

    assert destination.read_bytes() == before


def test_whole_ledger_conflicting_receipts_are_invalid_and_never_latest(tmp_path):
    document = _file_backed_ledger(tmp_path)
    model = document["models"][0]
    original = _runtime_entry(document, run_id="conflicted-run")
    conflict = {**original, "verdict": "fail"}
    model["verification"].extend([original, conflict])

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(violation.code == "verification_conflict" for violation in violations)
    assert ledger.latest_verification(model, "sapien", "runtime_load") is None


def test_receipt_conflict_scan_is_total_and_exact_duplicates_are_not_conflicts():
    entry = {
        "backend": "sapien",
        "check": "runtime_load",
        "verdict": "pass",
        "run_id": "same-fact",
        "timestamp": "2026-08-31T12:00:00",
        "verified_digest": "a" * 64,
    }

    assert ledger._verification_conflict_indices([entry, dict(entry)]) == set()
    assert ledger._verification_conflict_indices([{**entry, "opaque": object()}]) == set()
    assert ledger._verification_conflict_indices([{**entry, "backend": []}]) == set()


def test_ledger_writer_rejects_symlinked_parent_directory(tmp_path):
    document = _file_backed_ledger(tmp_path)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(OSError):
        ledger_writes.write_validated(linked_parent / "ledger.json", document)

    assert not (real_parent / "ledger.json").exists()
    assert not (real_parent / "ledger.lock").exists()


def test_empty_asset_quarantine_atomically_removes_the_executable_shell(tmp_path):
    """A rejected asset shell disappears from the library even though its lock lives inside it."""

    asset_dir = tmp_path / "library" / "315_shears"
    for name in ("visual", "collision", "snapshots"):
        (asset_dir / name).mkdir(parents=True, exist_ok=True)

    assert ledger_writes.quarantine_empty_asset_directory(asset_dir / "ledger.json") is True
    assert not asset_dir.exists()
    assert not list(asset_dir.parent.glob("_315_shears.empty-*"))


@pytest.mark.parametrize(
    "state",
    ["nonempty", "stray", "ledger"],
    ids=["model-file", "unexpected-file", "authoritative-ledger"],
)
def test_empty_asset_quarantine_refuses_nonempty_or_authoritative_asset(tmp_path, state):
    """Quarantine is fail-closed: it never removes a model file or an authoritative ledger."""

    asset_dir = tmp_path / "library" / "315_shears"
    asset_dir.mkdir(parents=True)
    ledger_path = asset_dir / "ledger.json"
    if state == "nonempty":
        (asset_dir / "visual").mkdir()
        (asset_dir / "visual" / "base0.glb").write_bytes(b"partial-worker-output")
    elif state == "stray":
        (asset_dir / "unrecognized-output").write_bytes(b"unexpected")
    else:
        ledger_path.write_text("{}")

    assert ledger_writes.quarantine_empty_asset_directory(ledger_path) is False
    assert asset_dir.exists()
    if state == "nonempty":
        assert (asset_dir / "visual" / "base0.glb").read_bytes() == b"partial-worker-output"
    elif state == "stray":
        assert (asset_dir / "unrecognized-output").read_bytes() == b"unexpected"
    else:
        assert ledger_path.read_text() == "{}"


def test_empty_asset_quarantine_keeps_asset_hidden_when_private_reclaim_fails(
    tmp_path, monkeypatch
):
    """A cleanup I/O failure may leave private debris, never an executable library asset."""

    asset_dir = tmp_path / "library" / "315_shears"
    asset_dir.mkdir(parents=True)

    def fail_reclaim(_path):
        raise OSError("injected quarantine cleanup failure")

    monkeypatch.setattr(ledger_writes, "_remove_private_tree", fail_reclaim)

    assert ledger_writes.quarantine_empty_asset_directory(asset_dir / "ledger.json") is True
    assert not asset_dir.exists()
    assert len(list(asset_dir.parent.glob("_315_shears.empty-*"))) == 1


def test_empty_asset_quarantine_rejects_directory_replacement_before_move(tmp_path, monkeypatch):
    """A replacement asset cannot be accidentally quarantined through a stale directory handle."""

    asset_dir = tmp_path / "library" / "315_shears"
    asset_dir.mkdir(parents=True)
    entered_parent_open = threading.Event()
    allow_parent_open = threading.Event()
    outcome = []
    original_open_parent = ledger._open_parent_directory

    @contextmanager
    def pause_before_asset_parent_open(path):
        with original_open_parent(path) as opened:
            if Path(path) == asset_dir:
                entered_parent_open.set()
                assert allow_parent_open.wait(timeout=5)
            yield opened

    monkeypatch.setattr(ledger, "_open_parent_directory", pause_before_asset_parent_open)

    def waiting_quarantine():
        try:
            ledger_writes.quarantine_empty_asset_directory(asset_dir / "ledger.json")
        except Exception as exc:  # assertion below checks the public concurrency failure
            outcome.append(exc)

    worker = threading.Thread(target=waiting_quarantine, name="replaced-asset-quarantine")
    worker.start()
    assert entered_parent_open.wait(timeout=5)
    os.rename(asset_dir, asset_dir.with_name("315_shears.replaced"))
    asset_dir.mkdir()
    allow_parent_open.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], ledger_writes.ConcurrentLedgerUpdateError)
    assert "directory changed before empty-shell quarantine" in str(outcome[0])
    assert asset_dir.exists()


def test_writer_rejects_waiter_when_asset_directory_is_replaced_while_lock_is_held(
    tmp_path, monkeypatch
):
    """A waiting public write must not land in a replacement asset directory."""

    document = _file_backed_ledger(tmp_path)
    asset_dir = tmp_path / "library" / "315_shears"
    asset_dir.mkdir(parents=True)
    ledger_path = asset_dir / "ledger.json"
    lock_path = asset_dir / "ledger.lock"
    entered_lock = threading.Event()
    allow_lock = threading.Event()
    outcome = []
    actual_flock = ledger.fcntl.flock

    def pause_waiter(lock_file, operation):
        if threading.current_thread().name == "replaced-asset-writer" and operation & fcntl.LOCK_EX:
            entered_lock.set()
            assert allow_lock.wait(timeout=5)
        return actual_flock(lock_file, operation)

    monkeypatch.setattr(ledger.fcntl, "flock", pause_waiter)

    def waiting_write():
        try:
            ledger_writes.write_validated(ledger_path, document)
        except Exception as exc:  # assertion below checks the public failure type and message
            outcome.append(exc)

    with lock_path.open("w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        waiter = threading.Thread(target=waiting_write, name="replaced-asset-writer")
        waiter.start()
        assert entered_lock.wait(timeout=5)
        old_asset_dir = asset_dir.with_name("315_shears.replaced")
        os.rename(asset_dir, old_asset_dir)
        asset_dir.mkdir()
        allow_lock.set()
        fcntl.flock(holder, fcntl.LOCK_UN)
        waiter.join(timeout=5)

    assert not waiter.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], OSError)
    assert "directory changed while waiting for its lock" in str(outcome[0])
    assert not ledger_path.exists()


@pytest.mark.parametrize("missing_capability", ["O_NOFOLLOW", "O_DIRECTORY"])
def test_safe_parent_open_requires_platform_capabilities(tmp_path, monkeypatch, missing_capability):
    monkeypatch.delattr(ledger.os, missing_capability)

    with pytest.raises(OSError, match="safe ledger paths require"):
        with ledger._open_parent_directory(tmp_path / "ledger.json"):
            pass


def test_safe_parent_open_rejects_path_without_filename():
    with pytest.raises(OSError, match="no file name"):
        with ledger._open_parent_directory(Path("/")):
            pass


def test_locked_json_read_closes_descriptor_when_fdopen_fails(tmp_path, monkeypatch):
    (tmp_path / "ledger.json").write_text("{}")
    directory_fd = ledger.os.open(tmp_path, ledger.os.O_RDONLY | ledger.os.O_DIRECTORY)
    opened = []

    def fail_fdopen(fd, mode):
        del mode
        opened.append(fd)
        raise OSError("injected fdopen failure")

    monkeypatch.setattr(ledger.os, "fdopen", fail_fdopen)
    try:
        with pytest.raises(OSError, match="injected fdopen"):
            ledger._read_locked_json(ledger._LockedLedger(directory_fd, "ledger.json"))
        with pytest.raises(OSError):
            ledger.os.fstat(opened[0])
    finally:
        ledger.os.close(directory_fd)


def test_atomic_write_retries_temp_collision_and_exhaustion(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    collision = tmp_path / "ledger.json.collision.tmp"
    collision.write_text("occupied")
    names = iter(("collision", "fresh"))
    monkeypatch.setattr(ledger.secrets, "token_hex", lambda _size: next(names))

    ledger._atomic_write_json(target, {"written": True})

    assert json.loads(target.read_text()) == {"written": True}
    monkeypatch.setattr(ledger.secrets, "token_hex", lambda _size: "collision")
    with pytest.raises(FileExistsError, match="unique ledger temporary"):
        ledger._atomic_write_json(target, {"written": False})


def test_atomic_write_closes_new_descriptor_when_fdopen_fails(tmp_path, monkeypatch):
    opened = []

    def fail_fdopen(fd, mode):
        del mode
        opened.append(fd)
        raise OSError("injected fdopen failure")

    monkeypatch.setattr(ledger.os, "fdopen", fail_fdopen)

    with pytest.raises(OSError, match="injected fdopen"):
        ledger._atomic_write_json(tmp_path / "ledger.json", {"never": "published"})

    with pytest.raises(OSError):
        ledger.os.fstat(opened[0])
    assert list(tmp_path.glob("ledger.json.*.tmp")) == []


def test_write_validated_rechecks_files_at_atomic_publish_boundary(tmp_path, monkeypatch):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    representation_path = Path(document["models"][0]["representations"][0]["uri"])
    real_atomic_write = ledger._atomic_write_json

    def mutate_between_initial_gate_and_publish(path, candidate, *args, **kwargs):
        representation_path.write_bytes(b"mutated-after-initial-validation")
        return real_atomic_write(path, candidate, *args, **kwargs)

    monkeypatch.setattr(ledger, "_atomic_write_json", mutate_between_initial_gate_and_publish)

    with pytest.raises(ledger_writes.LedgerWriteError):
        ledger_writes.write_validated(destination, document)

    assert not destination.exists()


def test_commit_then_cleanup_preserves_files_on_stale_cas(tmp_path):
    document = _file_backed_ledger(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    ledger_writes.write_validated(ledger_path, document)
    expected = json.loads(ledger_path.read_text())
    receipt = _runtime_entry(document, run_id="concurrent-cleanup-race")
    ledger.append_verification(ledger_path, 0, receipt)
    victim = tmp_path / "rejected-model.glb"
    victim.write_bytes(b"must-survive-stale-cleanup")

    with pytest.raises(ledger_writes.ConcurrentLedgerUpdateError):
        ledger_writes.commit_then_cleanup(
            ledger_path,
            expected,
            expected=expected,
            cleanup=victim.unlink,
        )

    assert victim.read_bytes() == b"must-survive-stale-cleanup"
    assert json.loads(ledger_path.read_text())["models"][0]["verification"][-1] == receipt


def test_commit_then_cleanup_deletes_last_ledger_before_files(tmp_path):
    document = _file_backed_ledger(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    ledger_writes.write_validated(ledger_path, document)
    victim = tmp_path / "rejected-model.glb"
    victim.write_bytes(b"rejected")
    ledger_seen_by_cleanup = []

    def cleanup():
        ledger_seen_by_cleanup.append(ledger_path.exists())
        victim.unlink()

    ledger_writes.commit_then_cleanup(
        ledger_path,
        None,
        expected=document,
        cleanup=cleanup,
    )

    assert ledger_seen_by_cleanup == [False]
    assert not ledger_path.exists()
    assert not victim.exists()
    assert ledger_path.with_suffix(".lock").is_file()


def test_commit_then_cleanup_enforces_presence_cas_before_callback(tmp_path):
    document = _file_backed_ledger(tmp_path)
    existing = tmp_path / "existing.json"
    ledger_writes.write_validated(existing, document)
    callbacks = []

    with pytest.raises(ledger_writes.ConcurrentLedgerUpdateError, match="expected no ledger"):
        ledger_writes.commit_then_cleanup(
            existing,
            None,
            expected=None,
            cleanup=lambda: callbacks.append("existing"),
        )
    with pytest.raises(ledger_writes.ConcurrentLedgerUpdateError, match="disappeared"):
        ledger_writes.commit_then_cleanup(
            tmp_path / "missing.json",
            None,
            expected=document,
            cleanup=lambda: callbacks.append("missing"),
        )

    assert callbacks == []


def test_commit_then_cleanup_handles_expect_absent_and_valid_replacement(tmp_path):
    document = _file_backed_ledger(tmp_path)
    absent = tmp_path / "absent.json"
    callbacks = []
    ledger_writes.commit_then_cleanup(
        absent,
        None,
        expected=None,
        cleanup=lambda: callbacks.append("absent"),
    )

    existing = tmp_path / "existing.json"
    ledger_writes.write_validated(existing, document)
    ledger_writes.commit_then_cleanup(
        existing,
        document,
        expected=document,
        cleanup=lambda: callbacks.append("replacement"),
    )

    assert callbacks == ["absent", "replacement"]
    assert json.loads(existing.read_text()) == document


def test_append_validated_rejects_unknown_model_and_invalid_candidate(tmp_path):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, document)

    with pytest.raises(ValueError, match="model_id=99"):
        ledger_writes.append_validated_verification(destination, 99, _runtime_entry(document))

    invalid_entry = _runtime_entry(document)
    del invalid_entry["timestamp"]
    with pytest.raises(ledger_writes.LedgerWriteError) as exc_info:
        ledger_writes.append_validated_verification(destination, 0, invalid_entry)
    assert "missing" in {violation.code for violation in exc_info.value.violations}


def test_append_validated_rejects_an_invalid_existing_ledger(tmp_path):
    document = _file_backed_ledger(tmp_path)
    destination = tmp_path / "ledger.json"
    ledger.write_ledger(destination, document)
    Path(document["models"][0]["representations"][0]["uri"]).write_text("tampered")

    with pytest.raises(ledger_writes.LedgerWriteError):
        ledger_writes.append_validated_verification(destination, 0, _runtime_entry(document))

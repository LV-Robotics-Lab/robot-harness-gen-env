from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import self_improving.harness.package_store as package_module
from scene_gen import CompileRequest, compile_scene
from scene_gen.builder import verify_package
from self_improving.harness import LocalArtifactStore
from self_improving.harness.package_store import PackageStore, PackageStoreError

ROOT = Path(__file__).resolve().parents[3]


def _compiled_package(tmp_path: Path) -> Path:
    outcome = compile_scene(
        CompileRequest(
            request="Place a can on top of a plate.",
            seed=42,
            asset_catalog_path=ROOT / "tests/fixtures/asset_catalog.json",
            out_root=tmp_path / "compiled",
        )
    )
    return outcome.output_dir


def test_package_store_publishes_and_materializes_exact_manifest_members(
    tmp_path: Path,
) -> None:
    source = _compiled_package(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    package_store = PackageStore(artifact_store)

    published = package_store.publish(source)
    expected = {
        record["path"]: (source / record["path"]).read_bytes()
        for record in json.loads(
            (source / "package_manifest.json").read_text(encoding="utf-8")
        )["files"]
    }
    shutil.rmtree(source)
    restored = package_store.materialize(published.manifest, tmp_path / "restored")

    assert restored == (tmp_path / "restored").resolve()
    assert verify_package(restored)["status"] == "pass"
    assert {
        path: (restored / path).read_bytes() for path in expected
    } == expected
    assert (restored / "package_manifest.json").read_bytes() == artifact_store.resolve(
        published.manifest
    ).path.read_bytes()
    assert len(published.members) == len(expected)
    assert all(artifact_store.resolve(ref).path.is_file() for ref in published.members)


@pytest.mark.parametrize(
    ("member_path", "reason"),
    [
        ("/absolute.json", "unsafe_member_path"),
        ("../escape.json", "unsafe_member_path"),
        ("C:\\escape.json", "unsafe_member_path"),
    ],
)
def test_package_store_rejects_unsafe_member_paths(
    tmp_path: Path,
    member_path: str,
    reason: str,
) -> None:
    source = _compiled_package(tmp_path)
    manifest_path = source / "package_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = member_path
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PackageStoreError) as error:
        PackageStore(LocalArtifactStore(tmp_path / "cas")).publish(source)
    assert error.value.reason == reason


def test_package_store_rejects_duplicate_missing_tampered_and_symlink_members(
    tmp_path: Path,
) -> None:
    source = _compiled_package(tmp_path)
    manifest_path = source / "package_manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    store = PackageStore(LocalArtifactStore(tmp_path / "cas"))

    duplicate = json.loads(json.dumps(original))
    duplicate["files"].append(dict(duplicate["files"][0]))
    manifest_path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(PackageStoreError) as duplicate_error:
        store.publish(source)
    assert duplicate_error.value.reason == "duplicate_member"

    manifest_path.write_text(json.dumps(original), encoding="utf-8")
    member = source / original["files"][0]["path"]
    member.unlink()
    with pytest.raises(PackageStoreError) as missing_error:
        store.publish(source)
    assert missing_error.value.reason == "member_missing"

    source = _compiled_package(tmp_path / "tampered")
    request = source / "request.txt"
    original_request = request.read_bytes()
    request.write_bytes(b"X" + original_request[1:])
    with pytest.raises(PackageStoreError) as digest_error:
        store.publish(source)
    assert digest_error.value.reason == "member_digest_mismatch"

    source = _compiled_package(tmp_path / "symlink")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    request = source / "request.txt"
    request.unlink()
    request.symlink_to(outside)
    with pytest.raises(PackageStoreError) as symlink_error:
        store.publish(source)
    assert symlink_error.value.reason == "member_path_escape"


def test_package_store_materialize_fails_closed_for_contract_and_cas_errors(
    tmp_path: Path,
) -> None:
    source = _compiled_package(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    package_store = PackageStore(artifact_store)
    published = package_store.publish(source)

    wrong_schema = published.manifest.model_copy(update={"schema_version": "wrong.v1"})
    with pytest.raises(PackageStoreError) as schema_error:
        package_store.materialize(wrong_schema, tmp_path / "wrong_schema")
    assert schema_error.value.reason == "manifest_schema_mismatch"

    unavailable_manifest = published.manifest.model_copy(
        update={
            "sha256": "f" * 64,
            "uri": f"artifact://sha256/{'f' * 64}",
        }
    )
    with pytest.raises(PackageStoreError) as manifest_error:
        package_store.materialize(unavailable_manifest, tmp_path / "missing_manifest")
    assert manifest_error.value.reason == "manifest_unavailable"

    missing_member = published.members[0]
    artifact_store.resolve(missing_member).path.unlink()
    with pytest.raises(PackageStoreError) as missing_error:
        package_store.materialize(published.manifest, tmp_path / "missing_member")
    assert missing_error.value.reason == "member_unavailable"
    assert not (tmp_path / "missing_member").exists()

    republished = package_store.publish(source)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(PackageStoreError) as existing_error:
        package_store.materialize(republished.manifest, existing)
    assert existing_error.value.reason == "destination_exists"


def test_package_store_reports_size_and_json_member_corruption(tmp_path: Path) -> None:
    store = PackageStore(LocalArtifactStore(tmp_path / "cas"))
    source = _compiled_package(tmp_path / "size")
    (source / "request.txt").write_text("short", encoding="utf-8")
    with pytest.raises(PackageStoreError) as size_error:
        store.publish(source)
    assert size_error.value.reason == "member_size_mismatch"

    source = _compiled_package(tmp_path / "json")
    manifest_path = source / "package_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scene_record = next(
        record for record in manifest["files"] if record["path"] == "scene_spec.json"
    )
    scene_path = source / "scene_spec.json"
    scene_path.write_text("{broken", encoding="utf-8")
    scene_record["bytes"] = scene_path.stat().st_size
    scene_record["sha256"] = hashlib.sha256(scene_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageStoreError) as json_error:
        store.publish(source)
    assert json_error.value.reason == "member_invalid"


def test_package_store_normalizes_manifest_shape_and_identity_attacks(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(PackageStoreError) as missing_error:
        package_module._read_manifest(missing)
    assert missing_error.value.reason == "manifest_invalid"

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(PackageStoreError):
        package_module._read_manifest(malformed)
    malformed.write_text(json.dumps({"schema_version": "wrong.v1"}), encoding="utf-8")
    with pytest.raises(PackageStoreError):
        package_module._read_manifest(malformed)

    valid_sha = "a" * 64
    base = {"path": "member.bin", "sha256": valid_sha, "bytes": 1}
    invalid_manifests = [
        {"files": None},
        {"files": []},
        {"files": ["not-an-object"]},
        {"files": [{"path": "member.bin"}]},
        {"files": [{**base, "sha256": 1}]},
        {"files": [{**base, "sha256": "a" * 63}]},
        {"files": [{**base, "sha256": "g" * 64}]},
        {"files": [{**base, "bytes": "1"}]},
        {"files": [{**base, "bytes": True}]},
        {"files": [{**base, "bytes": -1}]},
    ]
    for manifest in invalid_manifests:
        with pytest.raises(PackageStoreError) as error:
            package_module._member_records(manifest)
        assert error.value.reason == "manifest_invalid"

    unsafe_paths = [None, "", "C:relative.json", "dir\\member.json"]
    for value in unsafe_paths:
        with pytest.raises(PackageStoreError) as error:
            package_module._safe_relative(value)
        assert error.value.reason == "unsafe_member_path"


def test_package_store_wraps_verifier_exceptions_and_fail_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _compiled_package(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    store = PackageStore(artifact_store)
    real_verify = package_module.verify_package

    def explode(root):
        raise ValueError("verification exploded")

    monkeypatch.setattr(package_module, "verify_package", explode)
    with pytest.raises(PackageStoreError) as publish_exception:
        store.publish(source)
    assert publish_exception.value.reason == "package_verification"

    monkeypatch.setattr(package_module, "verify_package", lambda root: {"status": "fail"})
    with pytest.raises(PackageStoreError) as publish_fail:
        store.publish(source)
    assert publish_fail.value.reason == "package_verification"

    monkeypatch.setattr(package_module, "verify_package", real_verify)
    published = store.publish(source)
    monkeypatch.setattr(package_module, "verify_package", explode)
    with pytest.raises(PackageStoreError) as materialize_exception:
        store.materialize(published.manifest, tmp_path / "exception")
    assert materialize_exception.value.reason == "package_verification"
    assert not (tmp_path / "exception").exists()

    monkeypatch.setattr(package_module, "verify_package", lambda root: {"status": "fail"})
    with pytest.raises(PackageStoreError) as materialize_fail:
        store.materialize(published.manifest, tmp_path / "fail")
    assert materialize_fail.value.reason == "package_verification"
    assert not (tmp_path / "fail").exists()


def test_package_store_member_type_helpers_cover_untyped_json_and_unknown_media(
    tmp_path: Path,
) -> None:
    untyped = tmp_path / "untyped.json"
    untyped.write_text("[]", encoding="utf-8")
    assert package_module._member_type(untyped) == ("application/json", None)
    unknown = tmp_path / "asset.bin"
    unknown.write_bytes(b"asset")
    assert package_module._member_type(unknown) == ("application/octet-stream", None)

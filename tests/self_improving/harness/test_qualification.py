from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

import self_improving.harness.qualification as module
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.qualification import (
    QualificationBundleError,
    load_qualification_bundle,
    publish_qualification_bundle,
    verify_qualification_bundle,
    verify_qualification_documents,
)
from self_improving.harness.schemas import ArtifactRef

SKILL_REF = "text2env.compile@1.0.0"
REPORT_SCHEMA = "harness.skill_qualification_report.v1"
MANIFEST_SCHEMA = "harness.skill_implementation_manifest.v1"
ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_BUNDLE = ROOT / "self_improving/harness/qualified_skills/text2env.compile/1.0.0"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha(value: object) -> str:
    return _sha(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _tree_sha(root: Path) -> str:
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_file():
            payload = path.read_bytes()
            files.append(
                {
                    "path": relative.as_posix(),
                    "bytes": len(payload),
                    "sha256": _sha(payload),
                }
            )
    return _canonical_sha({"exists": True, "files": files})


def _implementation_sha(manifest: dict[str, object]) -> str:
    files = sorted(manifest["files"], key=lambda item: item["path"])  # type: ignore[index]
    return _canonical_sha(
        {
            "schema_version": manifest["schema_version"],
            "skill_ref": manifest["skill_ref"],
            "files": files,
            "scene_gen_tree_sha256": manifest["scene_gen_tree_sha256"],
            "ledger_contract_tree_sha256": manifest["ledger_contract_tree_sha256"],
        }
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _make_bundle(tmp_path: Path, *, reverse_files: bool = False) -> tuple[Path, Path, Path]:
    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True)
    implementation = tmp_path / "implementation_root"
    implementation.mkdir(parents=True)
    (implementation / "handler.py").write_text("VALUE = 1\n", encoding="utf-8")
    (implementation / "adapter.json").write_text('{"adapter":"v1"}\n', encoding="utf-8")

    scene_gen = tmp_path / "scene_gen"
    scene_gen.mkdir()
    (scene_gen / "compiler.py").write_text("COMPILER = 1\n", encoding="utf-8")
    ignored = scene_gen / "__pycache__"
    ignored.mkdir()
    (ignored / "compiler.pyc").write_bytes(b"unstable-cache")

    ledger_contract = tmp_path / "ledger_contract"
    ledger_contract.mkdir()
    (ledger_contract / "schema.json").write_text('{"version":3}\n', encoding="utf-8")

    files = []
    for path in sorted(implementation.iterdir()):
        payload = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(implementation).as_posix(),
                "bytes": len(payload),
                "sha256": _sha(payload),
            }
        )
    if reverse_files:
        files.reverse()
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA,
        "skill_ref": SKILL_REF,
        "files": files,
        "bundle_sha256": "0" * 64,
        "scene_gen_tree_sha256": _tree_sha(scene_gen),
        "ledger_contract_tree_sha256": _tree_sha(ledger_contract),
    }
    manifest["bundle_sha256"] = _implementation_sha(manifest)
    _write_json(bundle / "manifest.json", manifest)

    report = {
        "schema_version": REPORT_SCHEMA,
        "skill_ref": SKILL_REF,
        "status": "pass",
        "deterministic_case_id": "compile-fixture-42",
        "regression_command": "pytest -q tests/self_improving/harness",
        "implementation_sha256": manifest["bundle_sha256"],
        "scene_gen_tree_sha256": manifest["scene_gen_tree_sha256"],
        "ledger_contract_tree_sha256": manifest["ledger_contract_tree_sha256"],
        "checks": [
            {
                "name": "compile.output",
                "status": "pass",
                "evidence": {"package_id": "package-42", "run_count": 3},
            },
            {
                "name": "ledger.validation",
                "status": "pass",
                "evidence": {"violations": 0},
            },
        ],
    }
    _write_json(bundle / "report.json", report)
    report_sha = _sha((bundle / "report.json").read_bytes())
    qualification = {
        "skill_ref": SKILL_REF,
        "status": "pass",
        "deterministic_case_id": report["deterministic_case_id"],
        "regression_command": report["regression_command"],
        "report_sha256": report_sha,
    }
    _write_json(bundle / "qualification.json", qualification)
    return bundle, scene_gen, ledger_contract


def _implementation_root(bundle: Path) -> Path:
    return bundle.parent / "implementation_root"


def _load(
    tmp_path: Path,
    bundle: Path,
    scene_gen: Path,
    ledger_contract: Path,
    *,
    store: LocalArtifactStore | None = None,
    skill_ref: str = SKILL_REF,
    implementation_root: Path | None = None,
):
    return load_qualification_bundle(
        bundle,
        skill_ref=skill_ref,
        artifact_store=store or LocalArtifactStore(tmp_path / "cas"),
        implementation_root=implementation_root or _implementation_root(bundle),
        scene_gen_root=scene_gen,
        ledger_contract_root=ledger_contract,
    )


def test_checked_in_compile_qualification_matches_current_implementation(
    tmp_path: Path,
) -> None:
    loaded = load_qualification_bundle(
        PRODUCTION_BUNDLE,
        skill_ref=SKILL_REF,
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        implementation_root=ROOT,
        scene_gen_root=ROOT / "scene_gen",
        ledger_contract_root=(ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib"),
    )

    assert loaded.implementation_sha256 == (
        "07501791e56e555e3b70c6a93dfd2e24ba00945fc3a27395e09b4faf17cb5e2d"
    )
    assert loaded.qualification.report_sha256 == (
        "eb3282cb671a134574b7ae1a565573b86a03e4fe9dd13310b52e930da5b8fdb9"
    )
    assert [check.name for check in loaded.report.checks] == [
        "admission.lifecycle",
        "artifacts.cas_resolution",
        "invocation.stability",
        "ledger.v3_files",
        "package.binding",
        "source.stability",
        "static_validation.boundary",
    ]


def test_loads_verified_bundle_publishes_report_before_receipt_and_is_order_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger_contract = _make_bundle(tmp_path / "one")
    store = LocalArtifactStore(tmp_path / "cas")
    real_link = module._link_qualification_snapshot
    linked_digests: list[str] = []

    def track_link(source: Path, destination: Path) -> None:
        linked_digests.append(destination.name)
        real_link(source, destination)

    monkeypatch.setattr(module, "_link_qualification_snapshot", track_link)
    loaded = _load(tmp_path, bundle, scene_gen, ledger_contract, store=store)

    assert linked_digests == [
        loaded.report_artifact.sha256,
        loaded.qualification_artifact.sha256,
    ]
    assert loaded.qualification.skill_ref == SKILL_REF
    assert loaded.report.skill_ref == SKILL_REF
    assert loaded.manifest.bundle_sha256 == loaded.implementation_sha256
    assert {item.path for item in loaded.manifest.files} == {"adapter.json", "handler.py"}
    assert loaded.report.implementation_sha256 == loaded.implementation_sha256
    assert loaded.report_artifact.sha256 == loaded.qualification.report_sha256
    assert loaded.report_artifact.schema_version == REPORT_SCHEMA
    assert loaded.qualification_artifact.schema_version == "harness.skill_qualification.v1"
    assert (
        store.resolve(loaded.report_artifact).path.read_bytes()
        == (bundle / "report.json").read_bytes()
    )
    assert (
        store.resolve(loaded.qualification_artifact).path.read_bytes()
        == (bundle / "qualification.json").read_bytes()
    )

    other, other_scene_gen, other_ledger = _make_bundle(tmp_path / "two", reverse_files=True)
    reordered = _load(tmp_path, other, other_scene_gen, other_ledger)
    assert reordered.implementation_sha256 == loaded.implementation_sha256


def test_verification_is_cas_pure_until_explicit_final_publication(tmp_path: Path) -> None:
    bundle, scene_gen, ledger_contract = _make_bundle(tmp_path)
    store = LocalArtifactStore(tmp_path / "cas")

    verified = verify_qualification_bundle(
        bundle,
        skill_ref=SKILL_REF,
        implementation_root=_implementation_root(bundle),
        scene_gen_root=scene_gen,
        ledger_contract_root=ledger_contract,
    )

    assert not store.root.exists()
    loaded = publish_qualification_bundle(
        bundle,
        skill_ref=SKILL_REF,
        artifact_store=store,
        implementation_root=_implementation_root(bundle),
        scene_gen_root=scene_gen,
        ledger_contract_root=ledger_contract,
    )
    assert store.resolve(loaded.report_artifact).path.read_bytes() == verified.report_bytes
    assert (
        store.resolve(loaded.qualification_artifact).path.read_bytes()
        == verified.qualification_bytes
    )


def test_public_publisher_accepts_raw_bundle_not_a_constructible_verification_token() -> None:
    assert not hasattr(module, "VerifiedQualificationBundle")
    assert not hasattr(module, "publish_verified_qualification")
    assert tuple(inspect.signature(module.publish_qualification_bundle).parameters) == (
        "bundle_root",
        "skill_ref",
        "artifact_store",
        "implementation_root",
        "scene_gen_root",
        "ledger_contract_root",
    )


def test_in_memory_verification_never_creates_a_publishable_temporary_receipt(
    tmp_path: Path,
) -> None:
    bundle, scene_gen, ledger_contract = _make_bundle(tmp_path)
    documents = {path.name: path.read_bytes() for path in bundle.iterdir()}
    store = LocalArtifactStore(tmp_path / "cas")

    verified = verify_qualification_documents(
        documents,
        skill_ref=SKILL_REF,
        implementation_root=_implementation_root(bundle),
        scene_gen_root=scene_gen,
        ledger_contract_root=ledger_contract,
    )

    assert verified.qualification_path is None
    assert verified.report_path is None
    assert not store.root.exists()
    with pytest.raises(QualificationBundleError) as captured:
        module._publish_qualification_inspection(verified, artifact_store=store)
    assert captured.value.reason == "unpublished_document_snapshot"

    with pytest.raises(QualificationBundleError) as captured:
        verify_qualification_documents(
            {"qualification.json": documents["qualification.json"]},
            skill_ref=SKILL_REF,
            implementation_root=_implementation_root(bundle),
            scene_gen_root=scene_gen,
            ledger_contract_root=ledger_contract,
        )
    assert captured.value.reason == "document_set_mismatch"


@pytest.mark.parametrize("missing", ["qualification.json", "report.json", "manifest.json"])
def test_rejects_missing_required_document(tmp_path: Path, missing: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    (bundle / missing).unlink()
    with pytest.raises(QualificationBundleError, match="required bundle file"):
        _load(tmp_path, bundle, scene_gen, ledger)


@pytest.mark.parametrize("filename", ["qualification.json", "report.json", "manifest.json"])
def test_rejects_malformed_json(tmp_path: Path, filename: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    (bundle / filename).write_text("{", encoding="utf-8")
    with pytest.raises(QualificationBundleError, match="invalid .* document"):
        _load(tmp_path, bundle, scene_gen, ledger)


@pytest.mark.parametrize("filename", ["qualification.json", "report.json", "manifest.json"])
def test_rejects_extra_json_fields(tmp_path: Path, filename: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    path = bundle / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    _write_json(path, payload)
    if filename == "report.json":
        qualification = json.loads((bundle / "qualification.json").read_text(encoding="utf-8"))
        qualification["report_sha256"] = _sha(path.read_bytes())
        _write_json(bundle / "qualification.json", qualification)
    with pytest.raises(QualificationBundleError, match="invalid .* document"):
        _load(tmp_path, bundle, scene_gen, ledger)


def test_rejects_receipt_report_hash_mismatch(tmp_path: Path) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    qualification = json.loads((bundle / "qualification.json").read_text(encoding="utf-8"))
    qualification["report_sha256"] = "f" * 64
    _write_json(bundle / "qualification.json", qualification)
    with pytest.raises(QualificationBundleError, match="report_sha256"):
        _load(tmp_path, bundle, scene_gen, ledger)


@pytest.mark.parametrize("mutation", ["missing", "empty", "unsorted", "duplicate"])
def test_rejects_missing_empty_or_ambiguous_report_checks(
    tmp_path: Path,
    mutation: str,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    report_path = bundle / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        report.pop("checks")
    elif mutation == "empty":
        report["checks"] = []
    elif mutation == "unsorted":
        report["checks"].reverse()
    else:
        report["checks"][1]["name"] = report["checks"][0]["name"]
    _write_json(report_path, report)
    qualification = json.loads((bundle / "qualification.json").read_text(encoding="utf-8"))
    qualification["report_sha256"] = _sha(report_path.read_bytes())
    _write_json(bundle / "qualification.json", qualification)

    with pytest.raises(QualificationBundleError, match="invalid report"):
        _load(tmp_path, bundle, scene_gen, ledger)


@pytest.mark.parametrize("mutation", ["bytes", "sha256", "content"])
def test_rejects_implementation_file_drift(tmp_path: Path, mutation: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "content":
        (_implementation_root(bundle) / manifest["files"][0]["path"]).write_text(
            "DRIFT = 1\n",
            encoding="utf-8",
        )
    else:
        manifest["files"][0][mutation] = (
            manifest["files"][0][mutation] + 1 if mutation == "bytes" else "e" * 64
        )
        manifest["bundle_sha256"] = _implementation_sha(manifest)
        _write_json(manifest_path, manifest)
    with pytest.raises(QualificationBundleError, match="implementation file"):
        _load(tmp_path, bundle, scene_gen, ledger)


def test_rejects_actual_implementation_drift_while_bundle_is_unchanged(tmp_path: Path) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    document_digests = {path.name: _sha(path.read_bytes()) for path in bundle.iterdir()}
    (_implementation_root(bundle) / "handler.py").write_text(
        "VALUE = 'actual-runtime-drift'\n",
        encoding="utf-8",
    )

    assert {path.name: _sha(path.read_bytes()) for path in bundle.iterdir()} == document_digests
    with pytest.raises(QualificationBundleError, match="implementation file"):
        _load(tmp_path, bundle, scene_gen, ledger)


@pytest.mark.parametrize("source", ["scene", "ledger"])
def test_rejects_source_tree_drift(tmp_path: Path, source: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    root = scene_gen if source == "scene" else ledger
    (root / "drift.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(QualificationBundleError, match="source tree"):
        _load(tmp_path, bundle, scene_gen, ledger)


def test_ignores_python_cache_files_in_source_tree_identity(tmp_path: Path) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    cache = scene_gen / "__pycache__"
    (cache / "later.pyc").write_bytes(b"new cache bytes")
    (scene_gen / "empty-source-directory").mkdir()
    loaded = _load(tmp_path, bundle, scene_gen, ledger)
    assert loaded.manifest.scene_gen_tree_sha256 == _tree_sha(scene_gen)


@pytest.mark.parametrize("field", ["skill_ref", "deterministic_case_id", "regression_command"])
def test_rejects_receipt_report_identity_disagreement(tmp_path: Path, field: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    report_path = bundle / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report[field] = "text2env.replay@1.0.0" if field == "skill_ref" else "different"
    _write_json(report_path, report)
    qualification = json.loads((bundle / "qualification.json").read_text(encoding="utf-8"))
    qualification["report_sha256"] = _sha(report_path.read_bytes())
    _write_json(bundle / "qualification.json", qualification)
    with pytest.raises(QualificationBundleError, match="does not match"):
        _load(tmp_path, bundle, scene_gen, ledger)


@pytest.mark.parametrize("document", ["qualification", "report", "manifest"])
def test_rejects_skill_ref_mismatch(tmp_path: Path, document: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    path = bundle / f"{document}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["skill_ref"] = "text2env.replay@1.0.0"
    _write_json(path, payload)
    if document == "report":
        qualification = json.loads((bundle / "qualification.json").read_text(encoding="utf-8"))
        qualification["report_sha256"] = _sha(path.read_bytes())
        _write_json(bundle / "qualification.json", qualification)
    with pytest.raises(QualificationBundleError, match="skill_ref"):
        _load(tmp_path, bundle, scene_gen, ledger)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.py",
        "/tmp/absolute.py",
        "C:\\temp\\evil.py",
        "./implementation/handler.py",
    ],
)
def test_rejects_noncanonical_or_escaping_manifest_paths(tmp_path: Path, bad_path: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = bad_path
    manifest["bundle_sha256"] = _implementation_sha(manifest)
    _write_json(manifest_path, manifest)
    with pytest.raises(QualificationBundleError, match="manifest path"):
        _load(tmp_path, bundle, scene_gen, ledger)


def test_rejects_duplicate_and_reserved_manifest_paths(tmp_path: Path) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path / "duplicate")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(dict(manifest["files"][0]))
    manifest["bundle_sha256"] = _implementation_sha(manifest)
    _write_json(manifest_path, manifest)
    with pytest.raises(QualificationBundleError, match="duplicate"):
        _load(tmp_path, bundle, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "reserved")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "report.json"
    manifest["bundle_sha256"] = _implementation_sha(manifest)
    _write_json(manifest_path, manifest)
    with pytest.raises(QualificationBundleError, match="reserved"):
        _load(tmp_path, bundle, scene_gen, ledger)


def test_rejects_wrong_bundle_or_report_implementation_digest(tmp_path: Path) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path / "manifest")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_sha256"] = "d" * 64
    _write_json(manifest_path, manifest)
    with pytest.raises(QualificationBundleError, match="bundle_sha256"):
        _load(tmp_path, bundle, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "report")
    report_path = bundle / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["implementation_sha256"] = "d" * 64
    _write_json(report_path, report)
    qualification = json.loads((bundle / "qualification.json").read_text(encoding="utf-8"))
    qualification["report_sha256"] = _sha(report_path.read_bytes())
    _write_json(bundle / "qualification.json", qualification)
    with pytest.raises(QualificationBundleError, match="implementation_sha256"):
        _load(tmp_path, bundle, scene_gen, ledger)


def test_rejects_unmanifested_file_and_non_file_member(tmp_path: Path) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path / "extra")
    (bundle / "unbound.py").write_text("UNBOUND = 1\n", encoding="utf-8")
    with pytest.raises(QualificationBundleError, match="unmanifested"):
        _load(tmp_path, bundle, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "directory")
    (_implementation_root(bundle) / "nested").mkdir()
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "nested"
    manifest["bundle_sha256"] = _implementation_sha(manifest)
    _write_json(manifest_path, manifest)
    with pytest.raises(QualificationBundleError, match="regular file"):
        _load(tmp_path, bundle, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "missing")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "missing.py"
    manifest["bundle_sha256"] = _implementation_sha(manifest)
    _write_json(manifest_path, manifest)
    with pytest.raises(QualificationBundleError, match="missing or inaccessible"):
        _load(tmp_path, bundle, scene_gen, ledger)


def test_rejects_symlinked_bundle_documents_implementation_and_source(tmp_path: Path) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path / "root")
    link = tmp_path / "bundle-link"
    link.symlink_to(bundle, target_is_directory=True)
    with pytest.raises(QualificationBundleError, match="symlink"):
        _load(tmp_path, link, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "document")
    qualification = bundle / "qualification.json"
    qualification_copy = tmp_path / "qualification-copy.json"
    qualification_copy.write_bytes(qualification.read_bytes())
    qualification.unlink()
    qualification.symlink_to(qualification_copy)
    with pytest.raises(QualificationBundleError, match="symlink"):
        _load(tmp_path, bundle, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "implementation")
    implementation_file = _implementation_root(bundle) / "handler.py"
    copy = tmp_path / "handler-copy.py"
    copy.write_bytes(implementation_file.read_bytes())
    implementation_file.unlink()
    implementation_file.symlink_to(copy)
    with pytest.raises(QualificationBundleError, match="symlink"):
        _load(tmp_path, bundle, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "source")
    source_file = scene_gen / "compiler.py"
    source_copy = tmp_path / "compiler-copy.py"
    source_copy.write_bytes(source_file.read_bytes())
    source_file.unlink()
    source_file.symlink_to(source_copy)
    with pytest.raises(QualificationBundleError, match="source tree symlink"):
        _load(tmp_path, bundle, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "unmanifested")
    (bundle / "unmanifested-link.py").symlink_to(tmp_path / "absent.py")
    with pytest.raises(QualificationBundleError, match="must not contain symlinks"):
        _load(tmp_path, bundle, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "nested-root" / "real")
    alias = tmp_path / "nested-root" / "alias"
    alias.symlink_to(bundle.parent, target_is_directory=True)
    with pytest.raises(QualificationBundleError, match="root must not contain a symlink"):
        _load(tmp_path, alias / bundle.name, scene_gen, ledger)

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "implementation-root")
    implementation_alias = tmp_path / "implementation-alias"
    implementation_alias.symlink_to(_implementation_root(bundle), target_is_directory=True)
    with pytest.raises(QualificationBundleError, match="root must not contain a symlink"):
        _load(
            tmp_path,
            bundle,
            scene_gen,
            ledger,
            implementation_root=implementation_alias,
        )


def test_defense_in_depth_rejects_resolved_root_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    implementation_file = _implementation_root(bundle) / "handler.py"
    outside = tmp_path / "outside.py"
    outside.write_bytes(implementation_file.read_bytes())
    implementation_file.unlink()
    implementation_file.symlink_to(outside)
    real_is_symlink = Path.is_symlink

    def hide_attacking_symlink(path: Path) -> bool:
        if path == implementation_file:
            return False
        return real_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", hide_attacking_symlink)
    with pytest.raises(QualificationBundleError, match="escapes implementation root"):
        _load(tmp_path, bundle, scene_gen, ledger)


def test_wraps_implementation_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    implementation_file = _implementation_root(bundle) / "handler.py"
    real_open = Path.open

    def fail_target(path: Path, *args, **kwargs):
        if path == implementation_file:
            raise OSError("read denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_target)
    with pytest.raises(QualificationBundleError, match="could not be read"):
        _load(tmp_path, bundle, scene_gen, ledger)


@pytest.mark.parametrize(
    "root_kind",
    [
        "bundle_missing",
        "bundle_file",
        "implementation_missing",
        "implementation_file",
        "source_missing",
        "source_file",
    ],
)
def test_rejects_invalid_roots(tmp_path: Path, root_kind: str) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    implementation_root = _implementation_root(bundle)
    if root_kind == "bundle_missing":
        bundle = tmp_path / "missing-bundle"
    elif root_kind == "bundle_file":
        bundle = tmp_path / "plain-bundle"
        bundle.write_text("not a directory", encoding="utf-8")
    elif root_kind == "implementation_missing":
        implementation_root = tmp_path / "missing-implementation"
    elif root_kind == "implementation_file":
        implementation_root = tmp_path / "plain-implementation"
        implementation_root.write_text("not a directory", encoding="utf-8")
    elif root_kind == "source_missing":
        scene_gen = tmp_path / "missing-source"
    else:
        scene_gen = tmp_path / "plain-source"
        scene_gen.write_text("not a directory", encoding="utf-8")
    with pytest.raises(QualificationBundleError, match="root"):
        _load(
            tmp_path,
            bundle,
            scene_gen,
            ledger,
            implementation_root=implementation_root,
        )


def test_rejects_invalid_requested_skill_ref_and_publish_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path / "invalid")
    with pytest.raises(QualificationBundleError, match="requested skill_ref"):
        _load(tmp_path, bundle, scene_gen, ledger, skill_ref="text2env.compile@latest")

    bundle, scene_gen, ledger = _make_bundle(tmp_path / "publish")
    monkeypatch.setattr(
        module,
        "_link_qualification_snapshot",
        lambda _source, _destination: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(QualificationBundleError, match="publish"):
        _load(
            tmp_path,
            bundle,
            scene_gen,
            ledger,
            store=LocalArtifactStore(tmp_path / "cas"),
        )


def test_second_pass_document_failure_leaves_only_uncommitted_report_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    store = LocalArtifactStore(tmp_path / "cas")
    real_link = module._link_qualification_snapshot
    calls = 0

    def second_link_fails(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second CAS link failed")
        real_link(source, destination)

    monkeypatch.setattr(module, "_link_qualification_snapshot", second_link_fails)

    with pytest.raises(QualificationBundleError, match="publish"):
        _load(tmp_path, bundle, scene_gen, ledger, store=store)

    report_sha256 = _sha((bundle / "report.json").read_bytes())
    qualification_sha256 = _sha((bundle / "qualification.json").read_bytes())
    assert {path.name for path in module._cas_object_paths(store)} == {report_sha256}
    assert store.resolve_digest(report_sha256).is_file()
    with pytest.raises(ValueError, match="not found"):
        store.resolve_digest(qualification_sha256)


def test_failed_publication_never_deletes_a_concurrent_cas_writer_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    concurrent_source = tmp_path / "concurrent.json"
    concurrent_source.write_text('{"owner":"concurrent"}\n', encoding="utf-8")
    concurrent_refs: list[ArtifactRef] = []

    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    store = LocalArtifactStore(tmp_path / "cas")
    real_link = module._link_qualification_snapshot
    calls = 0

    def concurrent_write_then_second_link_fails(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            concurrent_refs.append(
                store.put_file(
                    concurrent_source,
                    name="concurrent",
                    media_type="application/json",
                    schema_version="test.concurrent.v1",
                )
            )
            raise OSError("second CAS link failed after concurrent writer")
        real_link(source, destination)

    monkeypatch.setattr(
        module,
        "_link_qualification_snapshot",
        concurrent_write_then_second_link_fails,
    )

    with pytest.raises(QualificationBundleError, match="publish"):
        _load(tmp_path, bundle, scene_gen, ledger, store=store)

    assert len(concurrent_refs) == 1
    assert store.resolve(concurrent_refs[0]).path.read_bytes() == concurrent_source.read_bytes()


def test_failed_publication_preserves_same_digest_when_concurrent_writer_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    concurrent_refs: list[ArtifactRef] = []
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    store = LocalArtifactStore(tmp_path / "cas")
    real_link = module._link_qualification_snapshot
    calls = 0

    def same_digest_writer_wins(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            concurrent_refs.append(
                LocalArtifactStore(store.root).put_file(
                    source,
                    name="concurrent_same_digest",
                    media_type="application/json",
                    schema_version=REPORT_SCHEMA,
                )
            )
        if calls == 2:
            raise OSError("second CAS link failed")
        real_link(source, destination)

    monkeypatch.setattr(module, "_link_qualification_snapshot", same_digest_writer_wins)

    with pytest.raises(QualificationBundleError, match="publish"):
        _load(tmp_path, bundle, scene_gen, ledger, store=store)

    assert len(concurrent_refs) == 1
    assert store.resolve(concurrent_refs[0]).path.is_file()


def test_failed_creator_cannot_break_a_concurrent_successful_adopter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    store = LocalArtifactStore(tmp_path / "cas")
    real_link = module._link_qualification_snapshot
    adopter_results = []
    outer_links = 0
    inside_adopter = False

    def creator_fails_after_adopter_finishes(source: Path, destination: Path) -> None:
        nonlocal inside_adopter, outer_links
        if inside_adopter:
            real_link(source, destination)
            return
        outer_links += 1
        if outer_links == 2:
            inside_adopter = True
            try:
                adopter_results.append(
                    publish_qualification_bundle(
                        bundle,
                        skill_ref=SKILL_REF,
                        artifact_store=store,
                        implementation_root=_implementation_root(bundle),
                        scene_gen_root=scene_gen,
                        ledger_contract_root=ledger,
                    )
                )
            finally:
                inside_adopter = False
            raise OSError("creator failed after adopter committed")
        real_link(source, destination)

    monkeypatch.setattr(
        module,
        "_link_qualification_snapshot",
        creator_fails_after_adopter_finishes,
    )
    with pytest.raises(QualificationBundleError, match="publish"):
        publish_qualification_bundle(
            bundle,
            skill_ref=SKILL_REF,
            artifact_store=store,
            implementation_root=_implementation_root(bundle),
            scene_gen_root=scene_gen,
            ledger_contract_root=ledger,
        )

    assert len(adopter_results) == 1
    adopter = adopter_results[0]
    assert store.resolve(adopter.report_artifact).path.is_file()
    assert store.resolve(adopter.qualification_artifact).path.is_file()


def test_cleanup_failure_after_commit_marker_does_not_turn_success_into_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    store = LocalArtifactStore(tmp_path / "cas")
    original_unlink = Path.unlink
    staging_cleanup_calls = 0

    def fail_second_staging_cleanup(self: Path, *args, **kwargs) -> None:
        nonlocal staging_cleanup_calls
        if self.name.startswith(".qualification."):
            staging_cleanup_calls += 1
            if staging_cleanup_calls == 2:
                raise OSError("staging cleanup failed after commit")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_staging_cleanup)

    loaded = publish_qualification_bundle(
        bundle,
        skill_ref=SKILL_REF,
        artifact_store=store,
        implementation_root=_implementation_root(bundle),
        scene_gen_root=scene_gen,
        ledger_contract_root=ledger,
    )

    assert store.resolve(loaded.report_artifact).path.is_file()
    assert store.resolve(loaded.qualification_artifact).path.is_file()


def test_atomic_snapshot_staging_and_existing_object_failures_are_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(b"{}")
    raw_bytes = source.read_bytes()

    empty_store = LocalArtifactStore(tmp_path / "empty-cas")
    assert module._cas_object_paths(empty_store) == frozenset()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            Path,
            "read_bytes",
            lambda self: (_ for _ in ()).throw(OSError("source unavailable")),
        )
        with pytest.raises(QualificationBundleError, match="snapshot failed"):
            module._publish_verified_snapshot(
                empty_store,
                source,
                raw_bytes=raw_bytes,
                name="snapshot",
                schema_version="test.snapshot.v1",
                label="snapshot",
            )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            module.tempfile,
            "mkstemp",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("staging unavailable")),
        )
        with pytest.raises(QualificationBundleError, match="publish failed"):
            module._publish_verified_snapshot(
                LocalArtifactStore(tmp_path / "staging-cas"),
                source,
                raw_bytes=raw_bytes,
                name="snapshot",
                schema_version="test.snapshot.v1",
                label="snapshot",
            )

    expected = module._expected_artifact_ref(
        raw_bytes,
        name="snapshot",
        schema_version="test.snapshot.v1",
    )
    corrupt_store = LocalArtifactStore(tmp_path / "corrupt-cas")
    corrupt_path = corrupt_store.root / "sha256" / expected.sha256[:2] / expected.sha256
    corrupt_path.parent.mkdir(parents=True)
    corrupt_path.write_bytes(b"corrupt")
    with pytest.raises(QualificationBundleError, match="CAS object is corrupt"):
        module._publish_verified_snapshot(
            corrupt_store,
            source,
            raw_bytes=raw_bytes,
            name="snapshot",
            schema_version="test.snapshot.v1",
            label="snapshot",
        )

    unreadable_store = LocalArtifactStore(tmp_path / "unreadable-cas")
    unreadable_path = unreadable_store.root / "sha256" / expected.sha256[:2] / expected.sha256
    unreadable_path.parent.mkdir(parents=True)
    unreadable_path.write_bytes(raw_bytes)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            module,
            "_file_snapshot",
            lambda _path: (_ for _ in ()).throw(OSError("cannot reread")),
        )
        with pytest.raises(QualificationBundleError, match="cannot be verified"):
            module._publish_verified_snapshot(
                unreadable_store,
                source,
                raw_bytes=raw_bytes,
                name="snapshot",
                schema_version="test.snapshot.v1",
                label="snapshot",
            )

    successful_store = LocalArtifactStore(tmp_path / "successful-cas")
    with monkeypatch.context() as scoped:
        scoped.setattr(
            Path,
            "rmdir",
            lambda self: (_ for _ in ()).throw(OSError("directory is busy")),
        )
        artifact = module._publish_verified_snapshot(
            successful_store,
            source,
            raw_bytes=raw_bytes,
            name="snapshot",
            schema_version="test.snapshot.v1",
            label="snapshot",
        )
    assert successful_store.resolve(artifact).path.is_file()


@pytest.mark.parametrize(
    "mutated_name",
    ["text2env_compile_qualification_report", "text2env_compile_qualification"],
)
def test_rejects_bundle_bytes_changed_during_artifact_publish(
    tmp_path: Path,
    mutated_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    target = (
        bundle / "report.json"
        if mutated_name.endswith("qualification_report")
        else bundle / "qualification.json"
    )
    original_read = Path.read_bytes
    target_reads = 0

    def changed_after_verification(self: Path) -> bytes:
        nonlocal target_reads
        payload = original_read(self)
        if self == target:
            target_reads += 1
            if target_reads == 2:
                return b"{}"
        return payload

    monkeypatch.setattr(Path, "read_bytes", changed_after_verification)
    with pytest.raises(QualificationBundleError, match="published .* does not match"):
        _load(
            tmp_path,
            bundle,
            scene_gen,
            ledger,
            store=LocalArtifactStore(tmp_path / "cas"),
        )


def test_publish_rejects_ref_identity_drift_after_individual_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, scene_gen, ledger = _make_bundle(tmp_path)
    verified = verify_qualification_bundle(
        bundle,
        skill_ref=SKILL_REF,
        implementation_root=_implementation_root(bundle),
        scene_gen_root=scene_gen,
        ledger_contract_root=ledger,
    )
    expected = verified.expected_report_artifact
    monkeypatch.setattr(
        module,
        "_publish_verified_snapshot",
        lambda *args, **kwargs: expected.model_copy(update={"name": "wrong"}),
    )

    with pytest.raises(QualificationBundleError) as captured:
        publish_qualification_bundle(
            bundle,
            skill_ref=SKILL_REF,
            artifact_store=LocalArtifactStore(tmp_path / "cas"),
            implementation_root=_implementation_root(bundle),
            scene_gen_root=scene_gen,
            ledger_contract_root=ledger,
        )

    assert captured.value.reason == "artifact_snapshot_mismatch"


def test_document_read_and_nonbytes_failures_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "qualification.json"
    path.write_bytes(b"{}")
    original_read = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == path:
            raise OSError("unavailable")
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(QualificationBundleError) as captured:
        module._load_document(path, module.SkillQualification, label="qualification")
    assert captured.value.reason == "invalid_qualification"

    with pytest.raises(QualificationBundleError) as captured:
        module._load_document_bytes(
            "not-bytes",  # type: ignore[arg-type]
            module.SkillQualification,
            label="qualification",
        )
    assert captured.value.reason == "invalid_qualification"

"""Fail-closed loading of packaged Skill qualification bundles.

The public ``SkillQualification`` receipt intentionally stays small.  This module
is the production boundary that proves the receipt points at a strict report and
that the report, implementation files, and source contracts still share one
content identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, TypeVar

from pydantic import Field, TypeAdapter, ValidationError

from .artifacts import LocalArtifactStore
from .schemas import ArtifactRef, SkillQualification
from .schemas.base import (
    CanonicalSkillRef,
    HarnessModel,
    NonEmptyString,
    NonNegativeInt,
    Sha256,
    ShortString,
)

QUALIFICATION_SCHEMA_ID = "harness.skill_qualification.v1"
QUALIFICATION_REPORT_SCHEMA_ID = "harness.skill_qualification_report.v1"
IMPLEMENTATION_MANIFEST_SCHEMA_ID = "harness.skill_implementation_manifest.v1"
_BUNDLE_DOCUMENTS = frozenset({"qualification.json", "report.json", "manifest.json"})


class QualificationReportV1(HarnessModel):
    """Strict evidence summary named by a public qualification receipt."""

    schema_version: Literal["harness.skill_qualification_report.v1"]
    skill_ref: CanonicalSkillRef
    status: Literal["pass"]
    deterministic_case_id: ShortString
    regression_command: NonEmptyString
    implementation_sha256: Sha256
    scene_gen_tree_sha256: Sha256
    ledger_contract_tree_sha256: Sha256


class ImplementationFileV1(HarnessModel):
    """One implementation file and its expected immutable content identity."""

    path: NonEmptyString
    bytes: NonNegativeInt
    sha256: Sha256


class ImplementationManifestV1(HarnessModel):
    """Strict manifest whose canonical payload becomes the descriptor digest."""

    schema_version: Literal["harness.skill_implementation_manifest.v1"]
    skill_ref: CanonicalSkillRef
    files: tuple[ImplementationFileV1, ...] = Field(min_length=1)
    bundle_sha256: Sha256
    scene_gen_tree_sha256: Sha256
    ledger_contract_tree_sha256: Sha256


@dataclass(frozen=True)
class LoadedQualification:
    """Verified inputs needed to construct a production ``SkillDescriptor``."""

    qualification: SkillQualification
    report: QualificationReportV1
    manifest: ImplementationManifestV1
    qualification_artifact: ArtifactRef
    report_artifact: ArtifactRef
    implementation_sha256: str


class QualificationBundleError(ValueError):
    """A qualification bundle failed schema, path, or content verification."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


_DocumentModel = TypeVar(
    "_DocumentModel",
    SkillQualification,
    QualificationReportV1,
    ImplementationManifestV1,
)


def load_qualification_bundle(
    bundle_root: Path,
    *,
    skill_ref: str,
    artifact_store: LocalArtifactStore,
    implementation_root: Path,
    scene_gen_root: Path,
    ledger_contract_root: Path,
) -> LoadedQualification:
    """Verify a fixed bundle and publish its report then receipt to local CAS.

    No result is synthesized here: both ``status=pass`` records must already be
    packaged, strict, and bound to the implementation and source bytes supplied
    by the caller.
    """

    expected_skill_ref = _validate_requested_skill_ref(skill_ref)
    root = _checked_root(bundle_root, label="qualification bundle")
    actual_implementation_root = _checked_root(
        implementation_root,
        label="implementation source",
    )
    qualification_path = _required_bundle_file(root, "qualification.json")
    report_path = _required_bundle_file(root, "report.json")
    manifest_path = _required_bundle_file(root, "manifest.json")

    qualification, qualification_bytes = _load_document(
        qualification_path,
        SkillQualification,
        label="qualification",
    )
    report, report_bytes = _load_document(
        report_path,
        QualificationReportV1,
        label="report",
    )
    manifest, _ = _load_document(
        manifest_path,
        ImplementationManifestV1,
        label="manifest",
    )

    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    if qualification.report_sha256 != report_sha256:
        raise QualificationBundleError(
            "report_receipt_mismatch",
            "qualification report_sha256 does not match raw report bytes",
        )

    _verify_skill_identity(
        expected_skill_ref=expected_skill_ref,
        qualification=qualification,
        report=report,
        manifest=manifest,
    )
    if (
        qualification.deterministic_case_id != report.deterministic_case_id
        or qualification.regression_command != report.regression_command
    ):
        raise QualificationBundleError(
            "report_receipt_mismatch",
            "qualification identity does not match the qualification report",
        )

    _verify_implementation_files(actual_implementation_root, manifest.files)
    _reject_non_document_bundle_entries(root)
    implementation_sha256 = _implementation_bundle_sha256(manifest)
    if manifest.bundle_sha256 != implementation_sha256:
        raise QualificationBundleError(
            "bundle_digest_mismatch",
            "manifest bundle_sha256 does not match the canonical implementation bundle",
        )
    if report.implementation_sha256 != implementation_sha256:
        raise QualificationBundleError(
            "implementation_digest_mismatch",
            "report implementation_sha256 does not match the descriptor implementation digest",
        )

    actual_scene_gen_sha256 = _source_tree_sha256(scene_gen_root, label="scene_gen")
    actual_ledger_sha256 = _source_tree_sha256(
        ledger_contract_root,
        label="ledger contract",
    )
    if not (
        manifest.scene_gen_tree_sha256 == report.scene_gen_tree_sha256 == actual_scene_gen_sha256
        and manifest.ledger_contract_tree_sha256
        == report.ledger_contract_tree_sha256
        == actual_ledger_sha256
    ):
        raise QualificationBundleError(
            "source_tree_mismatch",
            "qualification source tree digest does not match current source tree bytes",
        )

    artifact_prefix = expected_skill_ref.split("@", maxsplit=1)[0].replace(".", "_")
    report_artifact = _publish_verified_snapshot(
        artifact_store,
        report_path,
        raw_bytes=report_bytes,
        name=f"{artifact_prefix}_qualification_report",
        schema_version=QUALIFICATION_REPORT_SCHEMA_ID,
        label="qualification report",
    )
    qualification_artifact = _publish_verified_snapshot(
        artifact_store,
        qualification_path,
        raw_bytes=qualification_bytes,
        name=f"{artifact_prefix}_qualification",
        schema_version=QUALIFICATION_SCHEMA_ID,
        label="qualification receipt",
    )

    return LoadedQualification(
        qualification=qualification,
        report=report,
        manifest=manifest,
        qualification_artifact=qualification_artifact,
        report_artifact=report_artifact,
        implementation_sha256=implementation_sha256,
    )


def _validate_requested_skill_ref(skill_ref: str) -> str:
    try:
        return TypeAdapter(CanonicalSkillRef).validate_python(skill_ref, strict=True)
    except ValidationError as error:
        raise QualificationBundleError(
            "invalid_skill_ref",
            "requested skill_ref is not a canonical exact-version Skill reference",
        ) from error


def _checked_root(root: Path, *, label: str) -> Path:
    candidate = root.expanduser()
    absolute = candidate.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise QualificationBundleError(
                "unsafe_symlink",
                f"{label} root must not contain a symlink",
            )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise QualificationBundleError(
            "invalid_root",
            f"{label} root is missing or inaccessible: {candidate}",
        ) from error
    if not resolved.is_dir():
        raise QualificationBundleError("invalid_root", f"{label} root is not a directory")
    return resolved


def _required_bundle_file(root: Path, filename: str) -> Path:
    path = root / filename
    if path.is_symlink():
        raise QualificationBundleError(
            "unsafe_symlink",
            f"required bundle file must not be a symlink: {filename}",
        )
    if not path.is_file():
        raise QualificationBundleError(
            "missing_document",
            f"required bundle file is missing or not a regular file: {filename}",
        )
    return path


def _load_document(
    path: Path,
    model: type[_DocumentModel],
    *,
    label: str,
) -> tuple[_DocumentModel, bytes]:
    try:
        payload = path.read_bytes()
        return model.model_validate_json(payload), payload
    except (OSError, ValidationError) as error:
        raise QualificationBundleError(
            f"invalid_{label}",
            f"invalid {label} document: {error}",
        ) from error


def _publish_verified_snapshot(
    artifact_store: LocalArtifactStore,
    path: Path,
    *,
    raw_bytes: bytes,
    name: str,
    schema_version: str,
    label: str,
) -> ArtifactRef:
    try:
        artifact = artifact_store.put_file(
            path,
            name=name,
            media_type="application/json",
            schema_version=schema_version,
        )
    except Exception as error:
        raise QualificationBundleError(
            "artifact_publish_failed",
            f"qualification artifact publish failed: {error}",
        ) from error
    expected_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if artifact.sha256 != expected_sha256 or artifact.bytes != len(raw_bytes):
        raise QualificationBundleError(
            "artifact_snapshot_mismatch",
            f"published {label} does not match the bytes that were verified",
        )
    return artifact


def _verify_skill_identity(
    *,
    expected_skill_ref: str,
    qualification: SkillQualification,
    report: QualificationReportV1,
    manifest: ImplementationManifestV1,
) -> None:
    for label, actual in (
        ("qualification", qualification.skill_ref),
        ("report", report.skill_ref),
        ("manifest", manifest.skill_ref),
    ):
        if actual != expected_skill_ref:
            raise QualificationBundleError(
                "skill_ref_mismatch",
                f"{label} skill_ref does not match requested skill_ref",
            )


def _verify_implementation_files(
    root: Path,
    files: tuple[ImplementationFileV1, ...],
) -> None:
    seen: set[str] = set()
    for expected in files:
        relative = _validate_manifest_path(expected.path)
        if relative in seen:
            raise QualificationBundleError(
                "duplicate_manifest_path",
                f"duplicate implementation manifest path: {relative}",
            )
        seen.add(relative)
        path = _safe_descendant(root, relative)
        if not path.is_file():
            raise QualificationBundleError(
                "invalid_implementation_file",
                f"implementation file is not a regular file: {relative}",
            )
        actual_bytes, actual_sha256 = _file_snapshot(path)
        if actual_bytes != expected.bytes or actual_sha256 != expected.sha256:
            raise QualificationBundleError(
                "implementation_file_mismatch",
                f"implementation file does not match manifest bytes/hash: {relative}",
            )


def _validate_manifest_path(value: str) -> str:
    windows_path = PureWindowsPath(value)
    posix_path = PurePosixPath(value)
    if (
        "\\" in value
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or posix_path.is_absolute()
        or value != posix_path.as_posix()
        or any(part in {"", ".", ".."} for part in posix_path.parts)
    ):
        raise QualificationBundleError(
            "unsafe_manifest_path",
            f"manifest path must be canonical and relative: {value}",
        )
    if value in _BUNDLE_DOCUMENTS:
        raise QualificationBundleError(
            "reserved_manifest_path",
            f"implementation manifest path is reserved: {value}",
        )
    return value


def _safe_descendant(root: Path, relative: str) -> Path:
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise QualificationBundleError(
                "unsafe_symlink",
                f"implementation path must not contain a symlink: {relative}",
            )
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise QualificationBundleError(
            "invalid_implementation_file",
            f"implementation file is missing or inaccessible: {relative}",
        ) from error
    if not resolved.is_relative_to(root):
        raise QualificationBundleError(
            "root_escape",
            f"implementation file escapes implementation root: {relative}",
        )
    return resolved


def _reject_non_document_bundle_entries(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise QualificationBundleError(
                "unsafe_symlink",
                f"qualification bundle must not contain symlinks: {relative}",
            )
        if relative not in _BUNDLE_DOCUMENTS:
            raise QualificationBundleError(
                "unmanifested_file",
                f"qualification bundle contains unmanifested entry: {relative}",
            )


def _implementation_bundle_sha256(manifest: ImplementationManifestV1) -> str:
    files = sorted(
        (item.model_dump(mode="json") for item in manifest.files),
        key=lambda item: item["path"],
    )
    return _canonical_sha256(
        {
            "schema_version": manifest.schema_version,
            "skill_ref": manifest.skill_ref,
            "files": files,
            "scene_gen_tree_sha256": manifest.scene_gen_tree_sha256,
            "ledger_contract_tree_sha256": manifest.ledger_contract_tree_sha256,
        }
    )


def _source_tree_sha256(root: Path, *, label: str) -> str:
    resolved_root = _checked_root(root, label=f"{label} source tree")
    records: list[dict[str, Any]] = []
    for path in sorted(resolved_root.rglob("*"), key=lambda item: item.as_posix()):
        relative_path = path.relative_to(resolved_root)
        if "__pycache__" in relative_path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            raise QualificationBundleError(
                "unsafe_symlink",
                f"source tree symlink is not allowed: {label}/{relative}",
            )
        if not path.is_file():
            continue
        size, digest = _file_snapshot(path)
        records.append({"path": relative, "bytes": size, "sha256": digest})
    return _canonical_sha256({"exists": True, "files": records})


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_snapshot(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise QualificationBundleError(
            "file_read_failed",
            f"qualification file could not be read: {path}",
        ) from error
    return size, digest.hexdigest()

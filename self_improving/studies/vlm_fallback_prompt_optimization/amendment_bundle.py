"""Build and verify the amendment-01 effective preregistration bundle.

This module is deliberately limited to deterministic local document handling. It
does not load either model, invoke a provider, contact the network, or authorize
study execution.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from self_improving.studies.vlm_fallback_prompt_optimization import amendment

SPEC_V2_SCHEMA = "vlm_fallback.experiment_spec.v2"
PENDING_ANNOTATION_V3_SCHEMA = "vlm_fallback.pending_annotation_manifest.v3"
ROOT_MANIFEST_SCHEMA = "vlm_fallback.amendment_root_manifest.v1"
EXPECTED_ROOT_SHA256 = "5276894e83f3412f287b7e1b00e39f2eeb71cf51c52d55dafe49d7950a007dff"
DEFAULT_STUDY_ROOT = Path(__file__).resolve().parent
ROOT_CHILD_NAMES = (
    "AMENDMENT.md",
    "amendment_contract.json",
    "experiment_spec.v2.json",
    "model_content_3b.json",
    "model_content_7b.json",
    "pending_annotation_manifest.v3.json",
    "routing_case_inputs.json",
    "surface_canonicalization.json",
)
ROOT_EXCLUDED_PATHS = (
    "../../run_log.jsonl",
    "../../runner_source_manifest.json",
    "manifest.json",
)


@dataclass(frozen=True)
class _LoadedAmendmentBundle:
    """Internal immutable snapshot; it is not accepted as authority by any public sink."""

    root_sha256: str
    child_sha256: Mapping[str, str]
    _root_manifest_bytes: bytes
    _spec_bytes: bytes
    _pending_annotation_manifest_bytes: bytes

    def parse_root_manifest(self) -> dict[str, Any]:
        return _decode_json_object(self._root_manifest_bytes, "manifest.json")

    def parse_spec(self) -> dict[str, Any]:
        return _decode_json_object(self._spec_bytes, "experiment_spec.v2.json")

    def parse_pending_annotation_manifest(self) -> dict[str, Any]:
        return _decode_json_object(
            self._pending_annotation_manifest_bytes,
            "pending_annotation_manifest.v3.json",
        )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_json_object(raw: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate object key: {key}")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"{label} contains non-finite JSON number: {value}")

    value = json.loads(
        raw,
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_non_finite,
    )
    if type(value) is not dict:
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _load_json_object(path: Path) -> dict[str, Any]:
    return _decode_json_object(path.read_bytes(), path.name)


def _read_regular_file_at(directory_fd: int, name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise ValueError(f"root child must be a regular file: {name}") from error
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"root child must be a regular file: {name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(file_fd)


def _build_effective_spec(
    base_spec: dict[str, Any], loaded_amendment: dict[str, Any]
) -> dict[str, Any]:
    contract = loaded_amendment["contract"]
    effective = copy.deepcopy(base_spec)
    effective["schema_version"] = SPEC_V2_SCHEMA
    effective["status"] = contract["status"]
    effective["content_manifest_digest_definition"] = {
        "schema_version": amendment.MODEL_CONTENT_SCHEMA_VERSION,
        "canonical_json": {
            "encoding": "UTF-8",
            "ensure_ascii": False,
            "sort_keys": True,
            "separators": [",", ":"],
            "trailing_newline": False,
        },
        "digest_algorithm": "sha256",
        "top_level_fields": ["schema_version", "model_id", "revision", "entries"],
        "entry_fields": [
            "path",
            "blob_id",
            "blob_id_algorithm",
            "size_bytes",
            "content_sha256",
        ],
        "entry_order": "ascending UTF-8 path bytes",
        "content_binding": "content_sha256 is SHA-256 of every resolved file byte sequence",
        "blob_identity_binding": (
            "blob_id and blob_id_algorithm bind source provenance; a sha256 blob_id must "
            "equal content_sha256"
        ),
        "authority": "required normative model identity for v2 execution and receipts",
        "legacy_model_digest_definition_authority": "historical_snapshot_metadata_only",
        "receipt_semantics": {
            "model_content_manifest_sha256": (
                "normative canonical v2 manifest digest bound to model_id and revision"
            ),
            "model_roster_sha256": (
                "runtime freshness receipt issued after full content verification; not a "
                "normative model-content digest"
            ),
        },
    }
    effective["logging_contract"]["schema_version"] = "vlm_fallback.run_event.v2"
    effective["logging_contract"]["invocation_receipt_fields"].extend(
        ["model_content_manifest_sha256", "model_roster_sha256"]
    )
    effective["amendment"] = {
        "amendment_id": amendment.AMENDMENT_ID,
        "base_spec": {
            "path": "../../experiment_spec.json",
            "sha256": amendment.ORIGINAL_SPEC_SHA256,
        },
        "contract": {
            "path": "amendment_contract.json",
            "canonical_sha256": amendment.EXPECTED_CONTRACT_SHA256,
            "effective": copy.deepcopy(contract),
        },
        "surface_canonicalization": {
            "path": "surface_canonicalization.json",
            "canonical_sha256": amendment.EXPECTED_SURFACE_SHA256,
        },
        "routing_case_inputs": {
            "path": "routing_case_inputs.json",
            "canonical_sha256": amendment.EXPECTED_ROUTING_SHA256,
        },
    }

    bindings_by_role = {binding["role"]: binding for binding in amendment.MODEL_BINDINGS}
    for model in effective["models"]:
        binding = bindings_by_role.get(model["role"])
        if binding is None:
            raise ValueError(f"base spec has an unbound model role: {model['role']}")
        if model["model_id"] != binding["model_id"] or model["revision"] != binding["revision"]:
            raise ValueError(f"base spec model identity drifted for role: {model['role']}")
        model["content_manifest"] = {
            "path": binding["path"],
            "schema_version": binding["schema_version"],
            "canonical_sha256": binding["canonical_sha256"],
        }
    if set(bindings_by_role) != {model["role"] for model in effective["models"]}:
        raise ValueError("base spec does not contain every amendment-bound model role")
    return effective


def _build_pending_annotation_manifest(
    base_spec: dict[str, Any], prior_pending: dict[str, Any], spec_v2_bytes: bytes
) -> dict[str, Any]:
    expected_prior_fields = {
        "schema_version",
        "study_id",
        "spec_sha256",
        "case_ids",
        "label_contract_sha256",
        "rater_contract_sha256",
        "adjudication_contract_sha256",
        "state",
        "dev_gold_manifest_sha256",
        "test_annotation_payload_sha256",
    }
    if set(prior_pending) != expected_prior_fields:
        raise ValueError("prior pending annotation manifest fields drifted")
    if prior_pending["schema_version"] != "vlm_fallback.sealed_test_annotation_manifest.v2":
        raise ValueError("prior pending annotation manifest schema drifted")
    if prior_pending["study_id"] != amendment.STUDY_ID:
        raise ValueError("prior pending annotation manifest study drifted")
    if prior_pending["spec_sha256"] != amendment.ORIGINAL_SPEC_SHA256:
        raise ValueError("prior pending annotation manifest base spec binding drifted")
    if prior_pending["state"] != "pending_blinded_annotation":
        raise ValueError(
            "annotation state is no longer pending; amendment 01 must not auto-unlock it"
        )
    if (
        prior_pending["dev_gold_manifest_sha256"] is not None
        or prior_pending["test_annotation_payload_sha256"] is not None
    ):
        raise ValueError("prior pending annotation manifest unexpectedly contains gold digests")

    experiments = [
        experiment
        for experiment in base_spec["experiments"]
        if experiment["experiment_id"] == "A_visible_semantic_correction"
    ]
    if len(experiments) != 1:
        raise ValueError("base spec must contain exactly one visible-semantic experiment")
    case_ids = [
        sample["case_id"] for sample in experiments[0]["samples"] if sample["split"] == "test"
    ]
    if prior_pending["case_ids"] != case_ids:
        raise ValueError("prior pending annotation manifest test roster drifted")
    for field in (
        "label_contract_sha256",
        "rater_contract_sha256",
        "adjudication_contract_sha256",
    ):
        digest = prior_pending[field]
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"prior pending annotation manifest has an invalid {field}")

    return {
        "schema_version": PENDING_ANNOTATION_V3_SCHEMA,
        "study_id": amendment.STUDY_ID,
        "amendment_id": amendment.AMENDMENT_ID,
        "state": "pending_blinded_annotation",
        "base_spec_sha256": amendment.ORIGINAL_SPEC_SHA256,
        "effective_spec_v2": {
            "path": "experiment_spec.v2.json",
            "sha256": hashlib.sha256(spec_v2_bytes).hexdigest(),
        },
        "case_ids": copy.deepcopy(case_ids),
        "label_contract_sha256": prior_pending["label_contract_sha256"],
        "rater_contract_sha256": prior_pending["rater_contract_sha256"],
        "adjudication_contract_sha256": prior_pending["adjudication_contract_sha256"],
        "dev_gold_manifest_sha256": None,
        "test_annotation_payload_sha256": None,
        "typed_correction_blind_adjudication_manifest_sha256": None,
        "typed_correction_adjudication_roster_sha256": None,
        "execution_authorized": False,
        "provider_execution_allowed_now": False,
    }


def _build_root_manifest(
    amendment_root: Path, generated_children: dict[str, bytes]
) -> dict[str, Any]:
    static_names = tuple(name for name in ROOT_CHILD_NAMES if name not in generated_children)
    child_bytes = {name: (amendment_root / name).read_bytes() for name in static_names}
    child_bytes.update(generated_children)
    children = [
        {
            "path": name,
            "sha256": hashlib.sha256(child_bytes[name]).hexdigest(),
            "size_bytes": len(child_bytes[name]),
        }
        for name in sorted(child_bytes)
    ]
    return {
        "schema_version": ROOT_MANIFEST_SCHEMA,
        "study_id": amendment.STUDY_ID,
        "amendment_id": amendment.AMENDMENT_ID,
        "digest_algorithm": "sha256",
        "children": children,
        "excluded_paths": list(ROOT_EXCLUDED_PATHS),
    }


def build_bundle(study_root: Path = DEFAULT_STUDY_ROOT) -> dict[str, bytes]:
    """Return all three amendment documents as canonical UTF-8 JSON bytes."""

    root = study_root.expanduser().resolve(strict=True)
    amendment_root = root / "amendments" / amendment.AMENDMENT_ID
    loaded = amendment.load_amendment(amendment_root, verify_bindings=True)
    base_path = root / "experiment_spec.json"
    base_bytes = base_path.read_bytes()
    if hashlib.sha256(base_bytes).hexdigest() != amendment.ORIGINAL_SPEC_SHA256:
        raise ValueError("base experiment spec byte digest mismatch")
    base_spec = _load_json_object(base_path)
    effective = _build_effective_spec(base_spec, loaded)
    spec_v2_bytes = _canonical_json_bytes(effective)
    prior_pending = _load_json_object(root / "sealed_test_annotation_manifest.json")
    pending = _build_pending_annotation_manifest(base_spec, prior_pending, spec_v2_bytes)
    pending_bytes = _canonical_json_bytes(pending)
    generated_children = {
        "experiment_spec.v2.json": spec_v2_bytes,
        "pending_annotation_manifest.v3.json": pending_bytes,
    }
    root_manifest = _build_root_manifest(amendment_root, generated_children)
    return {
        **generated_children,
        "manifest.json": _canonical_json_bytes(root_manifest),
    }


def _require_sha256(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return value


def load_verified_bundle() -> _LoadedAmendmentBundle:
    """Parse amendment 01 only after verifying its module-pinned root and every child."""

    root = DEFAULT_STUDY_ROOT
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(root, directory_flags)
        try:
            amendments_fd = os.open("amendments", directory_flags, dir_fd=root_fd)
            try:
                amendment_fd = os.open(
                    amendment.AMENDMENT_ID,
                    directory_flags,
                    dir_fd=amendments_fd,
                )
            finally:
                os.close(amendments_fd)
        finally:
            os.close(root_fd)
    except OSError as error:
        raise ValueError(
            "amendment root must be a real directory under the fixed study root"
        ) from error
    try:
        expected_names = {*ROOT_CHILD_NAMES, "manifest.json"}
        if set(os.listdir(amendment_fd)) != expected_names:
            raise ValueError("amendment root directory entries mismatch")
        manifest_bytes = _read_regular_file_at(amendment_fd, "manifest.json")
        child_bytes_from_disk = {
            name: _read_regular_file_at(amendment_fd, name) for name in ROOT_CHILD_NAMES
        }
    finally:
        os.close(amendment_fd)
    root_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if root_sha256 != EXPECTED_ROOT_SHA256:
        raise ValueError("amendment root manifest digest mismatch")
    root_manifest = _decode_json_object(manifest_bytes, "manifest.json")
    if _canonical_json_bytes(root_manifest) != manifest_bytes:
        raise ValueError("amendment root manifest must use canonical JSON bytes")
    expected_root_fields = {
        "schema_version",
        "study_id",
        "amendment_id",
        "digest_algorithm",
        "children",
        "excluded_paths",
    }
    if set(root_manifest) != expected_root_fields:
        raise ValueError("amendment root manifest fields mismatch")
    if root_manifest["schema_version"] != ROOT_MANIFEST_SCHEMA:
        raise ValueError("amendment root manifest schema mismatch")
    if root_manifest["study_id"] != amendment.STUDY_ID:
        raise ValueError("amendment root manifest study mismatch")
    if root_manifest["amendment_id"] != amendment.AMENDMENT_ID:
        raise ValueError("amendment root manifest amendment mismatch")
    if root_manifest["digest_algorithm"] != "sha256":
        raise ValueError("amendment root manifest digest algorithm mismatch")
    if root_manifest["excluded_paths"] != list(ROOT_EXCLUDED_PATHS):
        raise ValueError("amendment root manifest exclusions mismatch")

    children = root_manifest["children"]
    if type(children) is not list or len(children) != len(ROOT_CHILD_NAMES):
        raise ValueError("amendment root manifest children mismatch")
    if [entry.get("path") if type(entry) is dict else None for entry in children] != list(
        ROOT_CHILD_NAMES
    ):
        raise ValueError("amendment root manifest children must be exact and sorted")

    child_sha256: dict[str, str] = {}
    child_bytes: dict[str, bytes] = {}
    for entry in children:
        if type(entry) is not dict or set(entry) != {"path", "sha256", "size_bytes"}:
            raise ValueError("amendment root child fields mismatch")
        name = entry["path"]
        digest = _require_sha256(entry["sha256"], f"root child SHA-256 for {name}")
        if type(entry["size_bytes"]) is not int or entry["size_bytes"] < 0:
            raise ValueError(f"root child size must be a non-negative integer: {name}")
        raw = child_bytes_from_disk[name]
        if len(raw) != entry["size_bytes"]:
            raise ValueError(f"root child size mismatch: {name}")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"root child digest mismatch: {name}")
        child_sha256[name] = digest
        child_bytes[name] = raw

    expected_documents = build_bundle(root)
    for name, expected_bytes in expected_documents.items():
        observed = manifest_bytes if name == "manifest.json" else child_bytes[name]
        if observed != expected_bytes:
            raise ValueError(f"committed amendment bundle document drifted: {name}")

    spec = _decode_json_object(child_bytes["experiment_spec.v2.json"], "experiment_spec.v2.json")
    pending = _decode_json_object(
        child_bytes["pending_annotation_manifest.v3.json"],
        "pending_annotation_manifest.v3.json",
    )
    if _canonical_json_bytes(spec) != child_bytes["experiment_spec.v2.json"]:
        raise ValueError("effective experiment spec must use canonical JSON bytes")
    if _canonical_json_bytes(pending) != child_bytes["pending_annotation_manifest.v3.json"]:
        raise ValueError("pending annotation manifest must use canonical JSON bytes")
    return _LoadedAmendmentBundle(
        root_sha256=root_sha256,
        child_sha256=MappingProxyType(dict(child_sha256)),
        _root_manifest_bytes=manifest_bytes,
        _spec_bytes=child_bytes["experiment_spec.v2.json"],
        _pending_annotation_manifest_bytes=child_bytes["pending_annotation_manifest.v3.json"],
    )


def verify_committed_bundle() -> dict[str, str]:
    """Fail closed unless committed bytes equal a fresh deterministic build."""

    loaded = load_verified_bundle()
    return {
        "experiment_spec.v2.json": loaded.child_sha256["experiment_spec.v2.json"],
        "pending_annotation_manifest.v3.json": loaded.child_sha256[
            "pending_annotation_manifest.v3.json"
        ],
        "manifest.json": loaded.root_sha256,
    }

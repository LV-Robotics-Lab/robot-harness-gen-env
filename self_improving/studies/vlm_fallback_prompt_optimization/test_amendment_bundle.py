from __future__ import annotations

import copy
import hashlib
import inspect
import json
import shutil
from pathlib import Path

import pytest

from self_improving.studies.vlm_fallback_prompt_optimization import amendment_bundle

STUDY_ROOT = Path(__file__).resolve().parent
AMENDMENT_ROOT = STUDY_ROOT / "amendments" / "01"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    assert type(value) is dict
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _copy_study(tmp_path: Path) -> Path:
    copied_study = tmp_path / "study"
    shutil.copytree(STUDY_ROOT, copied_study)
    return copied_study


def _write_json(path: Path, value: object) -> bytes:
    raw = _canonical_bytes(value)
    path.write_bytes(raw)
    return raw


def _rebind_mutated_base_for_branch_test(
    copied_study: Path,
    base: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Inject a synthetic base freeze without weakening the production loader."""

    loaded_amendment = amendment_bundle.amendment.load_amendment(
        AMENDMENT_ROOT,
        verify_bindings=True,
    )
    raw = _write_json(copied_study / "experiment_spec.json", base)
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(amendment_bundle.amendment, "ORIGINAL_SPEC_SHA256", digest)
    monkeypatch.setattr(
        amendment_bundle.amendment,
        "load_amendment",
        lambda *_args, **_kwargs: loaded_amendment,
    )
    return digest


def _point_loader_at_copy(
    copied_study: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pin_observed_root_for_branch_test: bool,
) -> None:
    monkeypatch.setattr(amendment_bundle, "DEFAULT_STUDY_ROOT", copied_study)
    if pin_observed_root_for_branch_test:
        raw = (copied_study / "amendments" / "01" / "manifest.json").read_bytes()
        monkeypatch.setattr(
            amendment_bundle, "EXPECTED_ROOT_SHA256", hashlib.sha256(raw).hexdigest()
        )


def _write_injected_root_manifest(
    copied_study: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: object,
    *,
    canonical: bool = True,
) -> bytes:
    raw = _canonical_bytes(value) if canonical else json.dumps(value, indent=2).encode("utf-8")
    _write_injected_root_bytes(copied_study, monkeypatch, raw)
    return raw


def _write_injected_root_bytes(
    copied_study: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    path = copied_study / "amendments" / "01" / "manifest.json"
    path.write_bytes(raw)
    _point_loader_at_copy(
        copied_study,
        monkeypatch,
        pin_observed_root_for_branch_test=True,
    )


def test_build_bundle_emits_full_v2_and_preserves_every_v1_experiment_binding() -> None:
    base = _load(STUDY_ROOT / "experiment_spec.json")
    contract = _load(AMENDMENT_ROOT / "amendment_contract.json")

    documents = amendment_bundle.build_bundle(STUDY_ROOT)

    assert set(documents) == {
        "experiment_spec.v2.json",
        "pending_annotation_manifest.v3.json",
        "manifest.json",
    }
    effective = json.loads(documents["experiment_spec.v2.json"])
    assert effective["schema_version"] == "vlm_fallback.experiment_spec.v2"
    assert effective["status"] == "preregistered_pre_execution"
    assert effective["experiments"] == base["experiments"]

    unchanged = copy.deepcopy(base)
    del unchanged["schema_version"]
    del unchanged["status"]
    del unchanged["models"]
    del unchanged["logging_contract"]
    for field, value in unchanged.items():
        assert effective[field] == value

    assert effective["amendment"] == {
        "amendment_id": "01",
        "base_spec": {
            "path": "../../experiment_spec.json",
            "sha256": "ad19d38204f20c42d8070785994fe749b8db41364e002e382e938cb92ae5bf6c",
        },
        "contract": {
            "path": "amendment_contract.json",
            "canonical_sha256": (
                "53e080f8ea03fcd36f88133a0445301a5c144281d5ee5bfbf08351f66ef480b2"
            ),
            "effective": contract,
        },
        "surface_canonicalization": {
            "path": "surface_canonicalization.json",
            "canonical_sha256": (
                "fdc8116af6f158253f44f4fda2ba3c6bc624b442addab2bcb97ae1c6077d5ce0"
            ),
        },
        "routing_case_inputs": {
            "path": "routing_case_inputs.json",
            "canonical_sha256": (
                "05d7e2fc66a162bbeeebf2e2c08200f7e57d91005c18f6a9a37b08f883811cbf"
            ),
        },
    }

    expected_model_manifests = {
        "primary_local_vlm": (
            "model_content_3b.json",
            "dd904e42c13f7a47296e1aff8ce8a78090a86451d24b5ccd0d2807d29e6e4cad",
        ),
        "confirmatory_model_size_ceiling": (
            "model_content_7b.json",
            "9686327d73f5f373917d25577fc06244c69b132927af1c586fbcbc23f3301208",
        ),
    }
    for old_model, new_model in zip(base["models"], effective["models"], strict=True):
        old_fields = dict(new_model)
        content_manifest = old_fields.pop("content_manifest")
        assert old_fields == old_model
        path, digest = expected_model_manifests[new_model["role"]]
        assert content_manifest == {
            "path": path,
            "schema_version": "vlm_fallback.model_content_manifest.v2",
            "canonical_sha256": digest,
        }


def test_v2_makes_content_bytes_authoritative_and_extends_invocation_receipts() -> None:
    base = _load(STUDY_ROOT / "experiment_spec.json")
    effective = json.loads(amendment_bundle.build_bundle(STUDY_ROOT)["experiment_spec.v2.json"])

    assert effective["model_digest_definition"] == base["model_digest_definition"]
    assert effective["content_manifest_digest_definition"] == {
        "schema_version": "vlm_fallback.model_content_manifest.v2",
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

    old_logging = copy.deepcopy(base["logging_contract"])
    old_schema = old_logging.pop("schema_version")
    old_receipt_fields = old_logging.pop("invocation_receipt_fields")
    new_logging = copy.deepcopy(effective["logging_contract"])
    new_schema = new_logging.pop("schema_version")
    new_receipt_fields = new_logging.pop("invocation_receipt_fields")
    assert old_schema == "vlm_fallback.run_event.v1"
    assert new_schema == "vlm_fallback.run_event.v2"
    assert new_logging == old_logging
    assert new_receipt_fields == [
        *old_receipt_fields,
        "model_content_manifest_sha256",
        "model_roster_sha256",
    ]


def test_pending_v3_binds_v2_one_way_and_keeps_every_execution_gate_closed() -> None:
    base = _load(STUDY_ROOT / "experiment_spec.json")
    prior_pending = _load(STUDY_ROOT / "sealed_test_annotation_manifest.json")

    documents = amendment_bundle.build_bundle(STUDY_ROOT)
    spec_v2_sha256 = hashlib.sha256(documents["experiment_spec.v2.json"]).hexdigest()
    pending = json.loads(documents["pending_annotation_manifest.v3.json"])

    expected_test_case_ids = [
        sample["case_id"]
        for experiment in base["experiments"]
        if experiment["experiment_id"] == "A_visible_semantic_correction"
        for sample in experiment["samples"]
        if sample["split"] == "test"
    ]
    assert pending == {
        "schema_version": "vlm_fallback.pending_annotation_manifest.v3",
        "study_id": "vlm-fallback-prompt-optimization-2026-08-31",
        "amendment_id": "01",
        "state": "pending_blinded_annotation",
        "base_spec_sha256": ("ad19d38204f20c42d8070785994fe749b8db41364e002e382e938cb92ae5bf6c"),
        "effective_spec_v2": {
            "path": "experiment_spec.v2.json",
            "sha256": spec_v2_sha256,
        },
        "case_ids": expected_test_case_ids,
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
    assert prior_pending["case_ids"] == expected_test_case_ids

    effective = json.loads(documents["experiment_spec.v2.json"])
    assert "pending_annotation_manifest_sha256" not in json.dumps(effective, sort_keys=True)


def test_root_manifest_has_exact_sorted_raw_byte_children_without_a_digest_cycle() -> None:
    documents = amendment_bundle.build_bundle(STUDY_ROOT)
    root_manifest = json.loads(documents["manifest.json"])

    expected_paths = [
        "AMENDMENT.md",
        "amendment_contract.json",
        "experiment_spec.v2.json",
        "model_content_3b.json",
        "model_content_7b.json",
        "pending_annotation_manifest.v3.json",
        "routing_case_inputs.json",
        "surface_canonicalization.json",
    ]
    assert root_manifest == {
        "schema_version": "vlm_fallback.amendment_root_manifest.v1",
        "study_id": "vlm-fallback-prompt-optimization-2026-08-31",
        "amendment_id": "01",
        "digest_algorithm": "sha256",
        "children": root_manifest["children"],
        "excluded_paths": [
            "../../run_log.jsonl",
            "../../runner_source_manifest.json",
            "manifest.json",
        ],
    }
    assert [entry["path"] for entry in root_manifest["children"]] == expected_paths

    for entry in root_manifest["children"]:
        assert set(entry) == {"path", "sha256", "size_bytes"}
        child_bytes = documents.get(entry["path"])
        if child_bytes is None:
            child_bytes = (AMENDMENT_ROOT / entry["path"]).read_bytes()
        assert entry == {
            "path": entry["path"],
            "sha256": hashlib.sha256(child_bytes).hexdigest(),
            "size_bytes": len(child_bytes),
        }

    forward_binding_text = (
        documents["manifest.json"] + documents["pending_annotation_manifest.v3.json"]
    )
    child_paths = {entry["path"] for entry in root_manifest["children"]}
    assert b'"path":"manifest.json"' not in documents["experiment_spec.v2.json"]
    assert "manifest.json" not in child_paths
    assert "../../runner_source_manifest.json" not in child_paths
    assert "../../run_log.jsonl" not in child_paths
    assert forward_binding_text.count(b'"path":"experiment_spec.v2.json"') == 2


def test_build_bundle_rejects_base_spec_byte_drift(tmp_path: Path) -> None:
    copied_study = _copy_study(tmp_path)
    base_path = copied_study / "experiment_spec.json"
    base_path.write_bytes(base_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="original spec file digest mismatch"):
        amendment_bundle.build_bundle(copied_study)


def test_build_bundle_secondary_digest_guard_rejects_if_upstream_check_is_injected_away(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_study = _copy_study(tmp_path)
    loaded_amendment = amendment_bundle.amendment.load_amendment(
        AMENDMENT_ROOT,
        verify_bindings=True,
    )
    monkeypatch.setattr(
        amendment_bundle.amendment,
        "load_amendment",
        lambda *_args, **_kwargs: loaded_amendment,
    )
    base_path = copied_study / "experiment_spec.json"
    base_path.write_bytes(base_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="base experiment spec byte digest mismatch"):
        amendment_bundle.build_bundle(copied_study)


def test_build_bundle_rejects_model_manifest_semantic_drift(tmp_path: Path) -> None:
    copied_study = _copy_study(tmp_path)
    manifest_path = copied_study / "amendments" / "01" / "model_content_3b.json"
    manifest = _load(manifest_path)
    manifest["revision"] = "0" * 40
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="model content revision"):
        amendment_bundle.build_bundle(copied_study)


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("unbound_role", "base spec has an unbound model role"),
        ("model_id", "base spec model identity drifted"),
        ("revision", "base spec model identity drifted"),
        ("missing_role", "base spec does not contain every amendment-bound model role"),
    ],
)
def test_build_bundle_rejects_base_model_binding_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    message: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    base = _load(copied_study / "experiment_spec.json")
    models = base["models"]
    assert type(models) is list
    assert type(models[0]) is dict
    if drift == "unbound_role":
        models[0]["role"] = "unbound-role"
    elif drift == "model_id":
        models[0]["model_id"] = "drifted/model"
    elif drift == "revision":
        models[0]["revision"] = "0" * 40
    else:
        models.pop()
    _rebind_mutated_base_for_branch_test(copied_study, base, monkeypatch)

    with pytest.raises(ValueError, match=message):
        amendment_bundle.build_bundle(copied_study)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"duplicate":1,"duplicate":2}', "contains duplicate object key: duplicate"),
        (b'{"value":NaN}', "contains non-finite JSON number: NaN"),
        (b"[]", "must contain a JSON object"),
    ],
)
def test_build_bundle_rejects_malformed_pending_json(
    tmp_path: Path,
    raw: bytes,
    message: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    (copied_study / "sealed_test_annotation_manifest.json").write_bytes(raw)

    with pytest.raises(ValueError, match=message):
        amendment_bundle.build_bundle(copied_study)


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("fields", "prior pending annotation manifest fields drifted"),
        ("schema", "prior pending annotation manifest schema drifted"),
        ("study", "prior pending annotation manifest study drifted"),
        ("spec", "prior pending annotation manifest base spec binding drifted"),
        ("state", "annotation state is no longer pending"),
        ("dev_gold", "unexpectedly contains gold digests"),
        ("test_gold", "unexpectedly contains gold digests"),
        ("roster", "test roster drifted"),
    ],
)
def test_build_bundle_rejects_pending_manifest_drift(
    tmp_path: Path,
    drift: str,
    message: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    pending_path = copied_study / "sealed_test_annotation_manifest.json"
    pending = _load(pending_path)
    if drift == "fields":
        pending["extra"] = None
    elif drift == "schema":
        pending["schema_version"] = "drifted"
    elif drift == "study":
        pending["study_id"] = "drifted"
    elif drift == "spec":
        pending["spec_sha256"] = "0" * 64
    elif drift == "state":
        pending["state"] = "sealed"
    elif drift == "dev_gold":
        pending["dev_gold_manifest_sha256"] = "0" * 64
    elif drift == "test_gold":
        pending["test_annotation_payload_sha256"] = "0" * 64
    else:
        case_ids = pending["case_ids"]
        assert type(case_ids) is list
        case_ids.pop()
    _write_json(pending_path, pending)

    with pytest.raises(ValueError, match=message):
        amendment_bundle.build_bundle(copied_study)


@pytest.mark.parametrize(
    ("field", "invalid_digest"),
    [
        ("label_contract_sha256", None),
        ("rater_contract_sha256", "0" * 63),
        ("adjudication_contract_sha256", "g" * 64),
    ],
)
def test_build_bundle_rejects_each_invalid_pending_contract_digest_form(
    tmp_path: Path,
    field: str,
    invalid_digest: object,
) -> None:
    copied_study = _copy_study(tmp_path)
    pending_path = copied_study / "sealed_test_annotation_manifest.json"
    pending = _load(pending_path)
    pending[field] = invalid_digest
    _write_json(pending_path, pending)

    with pytest.raises(ValueError, match=f"has an invalid {field}"):
        amendment_bundle.build_bundle(copied_study)


@pytest.mark.parametrize("visible_experiment_count", [0, 2])
def test_build_bundle_requires_exactly_one_visible_semantic_experiment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    visible_experiment_count: int,
) -> None:
    copied_study = _copy_study(tmp_path)
    base = _load(copied_study / "experiment_spec.json")
    experiments = base["experiments"]
    assert type(experiments) is list
    visible = next(
        experiment
        for experiment in experiments
        if experiment["experiment_id"] == "A_visible_semantic_correction"
    )
    if visible_experiment_count == 0:
        visible["experiment_id"] = "renamed-visible-experiment"
    else:
        experiments.append(copy.deepcopy(visible))
    rebound_digest = _rebind_mutated_base_for_branch_test(copied_study, base, monkeypatch)
    pending_path = copied_study / "sealed_test_annotation_manifest.json"
    pending = _load(pending_path)
    pending["spec_sha256"] = rebound_digest
    _write_json(pending_path, pending)

    with pytest.raises(ValueError, match="exactly one visible-semantic experiment"):
        amendment_bundle.build_bundle(copied_study)


def test_committed_bundle_rebuilds_byte_for_byte() -> None:
    summary = amendment_bundle.verify_committed_bundle()
    assert summary == {
        name: hashlib.sha256((AMENDMENT_ROOT / name).read_bytes()).hexdigest()
        for name in (
            "experiment_spec.v2.json",
            "pending_annotation_manifest.v3.json",
            "manifest.json",
        )
    }


def test_load_verified_bundle_uses_fixed_root_and_returns_fresh_document_copies() -> None:
    assert list(inspect.signature(amendment_bundle.load_verified_bundle).parameters) == []
    assert list(inspect.signature(amendment_bundle.verify_committed_bundle).parameters) == []
    assert not hasattr(amendment_bundle, "VerifiedAmendmentBundle")
    root_bytes = (AMENDMENT_ROOT / "manifest.json").read_bytes()
    root_sha256 = hashlib.sha256(root_bytes).hexdigest()

    loaded = amendment_bundle.load_verified_bundle()

    assert loaded.root_sha256 == root_sha256
    spec = loaded.parse_spec()
    pending = loaded.parse_pending_annotation_manifest()
    root = loaded.parse_root_manifest()
    assert spec["schema_version"] == "vlm_fallback.experiment_spec.v2"
    assert pending["state"] == "pending_blinded_annotation"
    assert pending["execution_authorized"] is False
    assert pending["provider_execution_allowed_now"] is False
    assert root["schema_version"] == "vlm_fallback.amendment_root_manifest.v1"
    assert (
        loaded.child_sha256["experiment_spec.v2.json"]
        == hashlib.sha256((AMENDMENT_ROOT / "experiment_spec.v2.json").read_bytes()).hexdigest()
    )

    spec["models"][0]["model_id"] = "mutated caller copy"
    pending["case_ids"].append("mutated caller copy")
    root["children"].clear()
    assert loaded.parse_spec()["models"][0]["model_id"] != "mutated caller copy"
    assert "mutated caller copy" not in loaded.parse_pending_annotation_manifest()["case_ids"]
    assert loaded.parse_root_manifest()["children"]
    with pytest.raises(TypeError):
        loaded.child_sha256["experiment_spec.v2.json"] = "0" * 64  # type: ignore[index]


def test_loaded_bundle_keeps_verified_bytes_after_the_backing_copy_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_study = _copy_study(tmp_path)
    _point_loader_at_copy(
        copied_study,
        monkeypatch,
        pin_observed_root_for_branch_test=False,
    )
    loaded = amendment_bundle.load_verified_bundle()
    spec_before = loaded.parse_spec()
    pending_before = loaded.parse_pending_annotation_manifest()
    root_before = loaded.parse_root_manifest()

    copied_amendment = copied_study / "amendments" / "01"
    (copied_amendment / "experiment_spec.v2.json").write_bytes(b"{}")
    (copied_amendment / "pending_annotation_manifest.v3.json").write_bytes(b"{}")
    (copied_amendment / "manifest.json").write_bytes(b"{}")

    assert loaded.parse_spec() == spec_before
    assert loaded.parse_pending_annotation_manifest() == pending_before
    assert loaded.parse_root_manifest() == root_before


def test_copied_and_self_rebuilt_bundle_cannot_self_certify_a_new_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_study = _copy_study(tmp_path)
    copied_amendment = copied_study / "amendments" / "01"
    amendment_doc = copied_amendment / "AMENDMENT.md"
    amendment_doc.write_bytes(amendment_doc.read_bytes() + b"\nsynthetic drift\n")
    rebuilt = amendment_bundle.build_bundle(copied_study)
    for name, raw in rebuilt.items():
        (copied_amendment / name).write_bytes(raw)
    self_calculated_root = hashlib.sha256(rebuilt["manifest.json"]).hexdigest()
    assert self_calculated_root != amendment_bundle.EXPECTED_ROOT_SHA256
    _point_loader_at_copy(
        copied_study,
        monkeypatch,
        pin_observed_root_for_branch_test=False,
    )

    with pytest.raises(ValueError, match="amendment root manifest digest mismatch"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("tamper", ["size", "digest"])
def test_fixed_root_rejects_child_byte_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    copied_spec = copied_study / "amendments" / "01" / "experiment_spec.v2.json"
    original = copied_spec.read_bytes()
    if tamper == "size":
        copied_spec.write_bytes(original + b"\n")
    else:
        replacement = b"{" if original[:1] != b"{" else b"["
        copied_spec.write_bytes(replacement + original[1:])
    _point_loader_at_copy(
        copied_study,
        monkeypatch,
        pin_observed_root_for_branch_test=False,
    )

    with pytest.raises(
        ValueError,
        match=f"root child {tamper} mismatch: experiment_spec.v2.json",
    ):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("entry_change", ["extra", "missing"])
def test_loader_requires_the_exact_amendment_directory_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_change: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    copied_amendment = copied_study / "amendments" / "01"
    if entry_change == "extra":
        (copied_amendment / "unbound.json").write_bytes(b"{}")
    else:
        (copied_amendment / "AMENDMENT.md").unlink()
    _point_loader_at_copy(
        copied_study,
        monkeypatch,
        pin_observed_root_for_branch_test=False,
    )

    with pytest.raises(ValueError, match="amendment root directory entries mismatch"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("symlink_level", ["study", "amendments", "amendment"])
def test_loader_rejects_symlinked_bundle_parent_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symlink_level: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    loader_root = copied_study
    if symlink_level == "study":
        loader_root = tmp_path / "study-link"
        loader_root.symlink_to(copied_study, target_is_directory=True)
    elif symlink_level == "amendments":
        real_amendments = copied_study / "real-amendments"
        (copied_study / "amendments").rename(real_amendments)
        (copied_study / "amendments").symlink_to(real_amendments, target_is_directory=True)
    else:
        amendment_dir = copied_study / "amendments" / "01"
        real_amendment = copied_study / "amendments" / "real-01"
        amendment_dir.rename(real_amendment)
        amendment_dir.symlink_to(real_amendment, target_is_directory=True)
    _point_loader_at_copy(
        loader_root,
        monkeypatch,
        pin_observed_root_for_branch_test=False,
    )

    with pytest.raises(
        ValueError,
        match="amendment root must be a real directory under the fixed study root",
    ):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("name", ["manifest.json", "AMENDMENT.md"])
def test_loader_rejects_leaf_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    copied_amendment = copied_study / "amendments" / "01"
    leaf = copied_amendment / name
    target = tmp_path / f"real-{name}"
    target.write_bytes(leaf.read_bytes())
    leaf.unlink()
    leaf.symlink_to(target)
    _point_loader_at_copy(
        copied_study,
        monkeypatch,
        pin_observed_root_for_branch_test=False,
    )

    with pytest.raises(ValueError, match=f"root child must be a regular file: {name}"):
        amendment_bundle.load_verified_bundle()


def test_loader_rejects_a_directory_in_place_of_a_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_study = _copy_study(tmp_path)
    child = copied_study / "amendments" / "01" / "AMENDMENT.md"
    child.unlink()
    child.mkdir()
    _point_loader_at_copy(
        copied_study,
        monkeypatch,
        pin_observed_root_for_branch_test=False,
    )

    with pytest.raises(ValueError, match="root child must be a regular file: AMENDMENT.md"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"schema_version":1,"schema_version":2}', "contains duplicate object key"),
        (b'{"value":Infinity}', "contains non-finite JSON number: Infinity"),
        (b"[]", "must contain a JSON object"),
    ],
)
def test_loader_rejects_malformed_root_json_after_test_only_branch_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    message: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    _write_injected_root_bytes(copied_study, monkeypatch, raw)

    with pytest.raises(ValueError, match=message):
        amendment_bundle.load_verified_bundle()


def test_loader_requires_canonical_root_manifest_bytes_after_test_only_branch_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_study = _copy_study(tmp_path)
    root = _load(copied_study / "amendments" / "01" / "manifest.json")
    _write_injected_root_manifest(copied_study, monkeypatch, root, canonical=False)

    with pytest.raises(ValueError, match="root manifest must use canonical JSON bytes"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("fields", "root manifest fields mismatch"),
        ("schema", "root manifest schema mismatch"),
        ("study", "root manifest study mismatch"),
        ("amendment", "root manifest amendment mismatch"),
        ("algorithm", "root manifest digest algorithm mismatch"),
        ("exclusions", "root manifest exclusions mismatch"),
    ],
)
def test_loader_rejects_root_contract_field_drift_after_test_only_branch_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    message: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    root = _load(copied_study / "amendments" / "01" / "manifest.json")
    if drift == "fields":
        root["extra"] = None
    elif drift == "schema":
        root["schema_version"] = "drifted"
    elif drift == "study":
        root["study_id"] = "drifted"
    elif drift == "amendment":
        root["amendment_id"] = "drifted"
    elif drift == "algorithm":
        root["digest_algorithm"] = "sha512"
    else:
        root["excluded_paths"] = []
    _write_injected_root_manifest(copied_study, monkeypatch, root)

    with pytest.raises(ValueError, match=message):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize(
    "drift",
    ["not_list", "wrong_length", "nonobject_entry", "wrong_path", "wrong_order"],
)
def test_loader_rejects_nonexact_root_child_rosters_after_test_only_branch_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    root = _load(copied_study / "amendments" / "01" / "manifest.json")
    children = root["children"]
    assert type(children) is list
    if drift == "not_list":
        root["children"] = {}
        message = "root manifest children mismatch"
    elif drift == "wrong_length":
        children.pop()
        message = "root manifest children mismatch"
    elif drift == "nonobject_entry":
        children[0] = None
        message = "root manifest children must be exact and sorted"
    elif drift == "wrong_path":
        children[0]["path"] = "not-a-child"
        message = "root manifest children must be exact and sorted"
    else:
        children[0], children[1] = children[1], children[0]
        message = "root manifest children must be exact and sorted"
    _write_injected_root_manifest(copied_study, monkeypatch, root)

    with pytest.raises(ValueError, match=message):
        amendment_bundle.load_verified_bundle()


def test_loader_rejects_root_child_entry_field_drift_after_test_only_branch_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_study = _copy_study(tmp_path)
    root = _load(copied_study / "amendments" / "01" / "manifest.json")
    children = root["children"]
    assert type(children) is list
    children[0]["extra"] = None
    _write_injected_root_manifest(copied_study, monkeypatch, root)

    with pytest.raises(ValueError, match="root child fields mismatch"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("invalid_digest", [None, "0" * 63, "g" * 64])
def test_loader_rejects_invalid_root_child_sha_forms_after_test_only_branch_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_digest: object,
) -> None:
    copied_study = _copy_study(tmp_path)
    root = _load(copied_study / "amendments" / "01" / "manifest.json")
    children = root["children"]
    assert type(children) is list
    children[0]["sha256"] = invalid_digest
    _write_injected_root_manifest(copied_study, monkeypatch, root)

    with pytest.raises(ValueError, match="must be 64 lowercase hex characters"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("invalid_size", [False, -1])
def test_loader_rejects_invalid_root_child_sizes_after_test_only_branch_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_size: object,
) -> None:
    copied_study = _copy_study(tmp_path)
    root = _load(copied_study / "amendments" / "01" / "manifest.json")
    children = root["children"]
    assert type(children) is list
    children[0]["size_bytes"] = invalid_size
    _write_injected_root_manifest(copied_study, monkeypatch, root)

    with pytest.raises(ValueError, match="root child size must be a non-negative integer"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("mismatch", ["size", "digest"])
def test_loader_rejects_root_child_receipt_mismatch_after_test_only_branch_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    root = _load(copied_study / "amendments" / "01" / "manifest.json")
    children = root["children"]
    assert type(children) is list
    if mismatch == "size":
        children[0]["size_bytes"] += 1
    else:
        children[0]["sha256"] = "0" * 64
    _write_injected_root_manifest(copied_study, monkeypatch, root)

    with pytest.raises(ValueError, match=f"root child {mismatch} mismatch: AMENDMENT.md"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("name", ["experiment_spec.v2.json", "pending_annotation_manifest.v3.json"])
def test_loader_rejects_semantically_drifted_generated_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    copied_amendment = copied_study / "amendments" / "01"
    document_path = copied_amendment / name
    document = _load(document_path)
    document["state" if name.startswith("pending") else "status"] = "synthetic-drift"
    raw = _write_json(document_path, document)
    root = _load(copied_amendment / "manifest.json")
    children = root["children"]
    assert type(children) is list
    entry = next(item for item in children if item["path"] == name)
    entry["sha256"] = hashlib.sha256(raw).hexdigest()
    entry["size_bytes"] = len(raw)
    _write_injected_root_manifest(copied_study, monkeypatch, root)

    with pytest.raises(ValueError, match=f"committed amendment bundle document drifted: {name}"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize("name", ["experiment_spec.v2.json", "pending_annotation_manifest.v3.json"])
def test_loader_rejects_noncanonical_generated_document_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    copied_amendment = copied_study / "amendments" / "01"
    document_path = copied_amendment / name
    pretty_bytes = json.dumps(_load(document_path), ensure_ascii=False, indent=2).encode("utf-8")
    document_path.write_bytes(pretty_bytes)
    root = _load(copied_amendment / "manifest.json")
    children = root["children"]
    assert type(children) is list
    entry = next(item for item in children if item["path"] == name)
    entry["sha256"] = hashlib.sha256(pretty_bytes).hexdigest()
    entry["size_bytes"] = len(pretty_bytes)
    _write_injected_root_manifest(copied_study, monkeypatch, root)

    with pytest.raises(ValueError, match=f"committed amendment bundle document drifted: {name}"):
        amendment_bundle.load_verified_bundle()


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("experiment_spec.v2.json", "effective experiment spec must use canonical JSON bytes"),
        (
            "pending_annotation_manifest.v3.json",
            "pending annotation manifest must use canonical JSON bytes",
        ),
    ],
)
def test_loader_late_canonical_guards_reject_if_rebuild_check_is_injected_away(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    message: str,
) -> None:
    copied_study = _copy_study(tmp_path)
    copied_amendment = copied_study / "amendments" / "01"
    document_path = copied_amendment / name
    pretty_bytes = json.dumps(_load(document_path), ensure_ascii=False, indent=2).encode("utf-8")
    document_path.write_bytes(pretty_bytes)
    root = _load(copied_amendment / "manifest.json")
    children = root["children"]
    assert type(children) is list
    entry = next(item for item in children if item["path"] == name)
    entry["sha256"] = hashlib.sha256(pretty_bytes).hexdigest()
    entry["size_bytes"] = len(pretty_bytes)
    root_bytes = _write_injected_root_manifest(copied_study, monkeypatch, root)
    observed_documents = {
        "experiment_spec.v2.json": (copied_amendment / "experiment_spec.v2.json").read_bytes(),
        "pending_annotation_manifest.v3.json": (
            copied_amendment / "pending_annotation_manifest.v3.json"
        ).read_bytes(),
        "manifest.json": root_bytes,
    }
    monkeypatch.setattr(
        amendment_bundle,
        "build_bundle",
        lambda _root: observed_documents,
    )

    with pytest.raises(ValueError, match=message):
        amendment_bundle.load_verified_bundle()

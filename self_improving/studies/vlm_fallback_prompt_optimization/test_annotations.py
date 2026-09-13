from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from self_improving.studies.vlm_fallback_prompt_optimization import annotations

CHECKS = (
    "object_presence",
    "object_identity",
    "object_count",
    "orientation",
    "table_contact",
    "visible_penetration_or_floating",
    "spatial_relation",
    "overall_prompt_match",
)
STATUSES = ("pass", "fail", "not_applicable", "insufficient_view")


def _spec(repo_root: Path) -> dict:
    samples = []
    for index, split in enumerate(("train", "dev", "test"), start=1):
        image = repo_root / f"view-{index}.png"
        image.write_bytes(f"image-{index}".encode())
        report = repo_root / f"runtime-{index}.json"
        report.write_text('{"physical_pass":true}\n', encoding="utf-8")
        samples.append(
            {
                "case_id": f"secret-case-{index}",
                "split": split,
                "group_id": "secret-group",
                "task_context": f"Place object {index} on its target.",
                "artifacts": [image.name, report.name],
                "bundle_sha256": str(index) * 64,
                "annotation_state": "pending_blinded_annotation",
                "physical_label_role": "independent_gate_only",
            }
        )
    return {
        "study_id": "vlm-fallback-prompt-optimization-2026-08-31",
        "experiments": [
            {
                "experiment_id": "A_visible_semantic_correction",
                "visible_checks": list(CHECKS),
                "gold_annotation": {
                    "raters": 2,
                    "blinded_to": [
                        "arm",
                        "model output",
                        "runtime pass/fail",
                        "fallback result",
                    ],
                    "statuses": list(STATUSES),
                    "insufficient_view_maps_to": "gold_not_scorable_and_model_must_abstain",
                    "adjudication": (
                        "third adjudicator resolves every disagreement before test scoring"
                    ),
                    "agreement_report": [
                        "raw agreement",
                        "Cohen kappa per check",
                        "disagreement count",
                    ],
                },
                "samples": samples,
            }
        ],
    }


def _install_spec(monkeypatch: pytest.MonkeyPatch, spec: dict, spec_sha: str = "a" * 64) -> None:
    monkeypatch.setattr(
        annotations,
        "_load_verified_spec",
        lambda spec_path, repo_root: (spec, spec_sha),
    )


def _export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    spec = _spec(repo_root)
    _install_spec(monkeypatch, spec)
    public_root = tmp_path / "public"
    private_map = tmp_path / "private" / "assignment-map.json"
    receipt = annotations.export_blinded_assignments(
        spec_path=tmp_path / "unused-spec.json",
        repo_root=repo_root,
        public_root=public_root,
        private_map_path=private_map,
        blind_key=b"k" * 32,
    )
    return spec, repo_root, public_root, private_map, receipt


def _rating(public_root: Path, slot: str, *, overrides: dict | None = None) -> Path:
    assignment_path = public_root / slot.replace("_", "-") / "assignment.json"
    assignment_raw = assignment_path.read_bytes()
    assignment = json.loads(assignment_raw)
    rows = []
    for item in assignment["items"]:
        labels = {check: "pass" for check in item["checks"]}
        labels.update((overrides or {}).get(item["item_id"], {}))
        rows.append({"item_id": item["item_id"], "labels": labels})
    rating = {
        "schema_version": "vlm_fallback.blinded_rating.v1",
        "assignment_sha256": annotations.sha256_bytes(assignment_raw),
        "rater_slot": slot,
        "rows": rows,
    }
    path = public_root / f"{slot}-ratings.json"
    path.write_bytes(annotations.canonical_json_bytes(rating) + b"\n")
    return path


def test_export_is_blinded_distinct_and_excludes_runtime_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _, public_root, private_map, receipt = _export(tmp_path, monkeypatch)

    assert receipt.case_count == 3
    assert private_map.stat().st_mode & 0o077 == 0
    first = json.loads((public_root / "rater-1" / "assignment.json").read_text())
    second = json.loads((public_root / "rater-2" / "assignment.json").read_text())
    mapping = json.loads(private_map.read_text())
    assert first["schema_version"] == "vlm_fallback.blinded_assignment.v2"
    assert second["schema_version"] == "vlm_fallback.blinded_assignment.v2"
    assert mapping["schema_version"] == "vlm_fallback.private_assignment_map.v2"
    expected_tool_sha = annotations.sha256_bytes(Path(annotations.__file__).read_bytes())
    assert first["annotation_source_sha256"] == expected_tool_sha
    assert second["annotation_source_sha256"] == expected_tool_sha
    assert mapping["annotation_source_sha256"] == expected_tool_sha
    assert len(first["items"]) == len(second["items"]) == 3
    assert {row["item_id"] for row in first["items"]}.isdisjoint(
        row["item_id"] for row in second["items"]
    )
    public_bytes = b"".join(path.read_bytes() for path in public_root.rglob("*.*"))
    for sample in spec["experiments"][0]["samples"]:
        assert sample["case_id"].encode() not in public_bytes
        assert sample["group_id"].encode() not in public_bytes
        assert sample["artifacts"][1].encode() not in public_bytes
    assert b"physical_pass" not in public_bytes
    assert sorted(path.read_bytes() for path in public_root.rglob("*.png")) == [
        b"image-1",
        b"image-1",
        b"image-2",
        b"image-2",
        b"image-3",
        b"image-3",
    ]


def test_export_rejects_private_map_inside_public_tree_and_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _install_spec(monkeypatch, _spec(repo_root))
    public_root = tmp_path / "public"

    with pytest.raises(annotations.AnnotationIntegrityError, match="private.*public"):
        annotations.export_blinded_assignments(
            spec_path=tmp_path / "spec.json",
            repo_root=repo_root,
            public_root=public_root,
            private_map_path=public_root / "private.json",
            blind_key=b"k" * 32,
        )

    public_root.mkdir()
    marker = public_root / "keep"
    marker.write_text("do not overwrite")
    with pytest.raises(annotations.AnnotationIntegrityError, match="already exists"):
        annotations.export_blinded_assignments(
            spec_path=tmp_path / "spec.json",
            repo_root=repo_root,
            public_root=public_root,
            private_map_path=tmp_path / "private.json",
            blind_key=b"k" * 32,
        )
    assert marker.read_text() == "do not overwrite"


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(rater_slot="rater_2"), "rater slot"),
        (lambda value: value["rows"].pop(), "complete"),
        (lambda value: value["rows"].append(value["rows"][0]), "duplicate"),
        (
            lambda value: value["rows"][0]["labels"].update(object_presence="maybe"),
            "status",
        ),
        (lambda value: value.update(assignment_sha256="0" * 64), "assignment digest"),
    ],
)
def test_rating_validation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    match: str,
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rating_path = _rating(public_root, "rater_1")
    value = json.loads(rating_path.read_text())
    mutation(value)
    rating_path.write_bytes(annotations.canonical_json_bytes(value) + b"\n")

    with pytest.raises(annotations.AnnotationIntegrityError, match=match):
        annotations.load_bound_ratings(
            private_map_path=private_map,
            public_root=public_root,
            rating_path=rating_path,
            rater_slot="rater_1",
            blind_key=b"k" * 32,
        )


def test_private_mapping_tamper_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rating_path = _rating(public_root, "rater_1")
    mapping = json.loads(private_map.read_text())
    mapping["entries"][0]["case_id"] = "changed"
    private_map.write_bytes(annotations.canonical_json_bytes(mapping) + b"\n")

    with pytest.raises(annotations.AnnotationIntegrityError, match="mapping authentication"):
        annotations.load_bound_ratings(
            private_map_path=private_map,
            public_root=public_root,
            rating_path=rating_path,
            rater_slot="rater_1",
            blind_key=b"k" * 32,
        )


def test_seal_rejects_assigned_media_changed_after_ratings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    assigned_view = next((public_root / "rater-1" / "media").rglob("*.png"))
    assigned_view.write_bytes(b"replacement-media")
    output_root = tmp_path / "sealed"

    with pytest.raises(annotations.AnnotationIntegrityError, match="assignment view digest"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert not output_root.exists()


@pytest.mark.parametrize(
    "output_kind",
    ["same", "child", "symlink-child", "symlink-escape", "symlink-chain"],
)
def test_seal_rejects_output_in_public_assignment_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_kind: str,
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    if output_kind == "same":
        output_root = public_root
    elif output_kind == "child":
        output_root = public_root / "sealed"
    elif output_kind == "symlink-child":
        alias = tmp_path / "public-alias"
        alias.symlink_to(public_root, target_is_directory=True)
        output_root = alias / "sealed"
    elif output_kind == "symlink-escape":
        outside = tmp_path / "outside-public"
        outside.mkdir()
        alias = public_root / "escape"
        alias.symlink_to(outside, target_is_directory=True)
        output_root = alias / "sealed"
    else:
        outside = tmp_path / "outside-public"
        outside.mkdir()
        escape = public_root / "escape"
        escape.symlink_to(outside, target_is_directory=True)
        alias = tmp_path / "public-chain-alias"
        alias.symlink_to(public_root, target_is_directory=True)
        output_root = alias / "escape" / "sealed"

    with pytest.raises(annotations.AnnotationIntegrityError, match="outside.*public"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert not (public_root / "sealed").exists()
    assert not (tmp_path / "outside-public" / "sealed").exists()


@pytest.mark.parametrize(
    "output_kind",
    ["same", "child", "symlink-child", "symlink-escape", "symlink-chain"],
)
def test_seal_rejects_output_in_adjudication_assignment_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_kind: str,
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    private_mapping = json.loads(private_map.read_text())
    disputed_item = private_mapping["entries"][0]["item_ids"]["rater_2"]
    rater_2 = _rating(
        public_root,
        "rater_2",
        overrides={disputed_item: {"orientation": "fail"}},
    )
    adjudication_root = tmp_path / "adjudication"
    adjudication_map = tmp_path / "private" / "adjudication-map.json"
    annotations.prepare_adjudication(
        private_map_path=private_map,
        public_root=public_root,
        rater_1_rating_path=rater_1,
        rater_2_rating_path=rater_2,
        adjudication_root=adjudication_root,
        adjudication_map_path=adjudication_map,
        blind_key=b"k" * 32,
    )
    assignment_raw = (adjudication_root / "assignment.json").read_bytes()
    assignment = json.loads(assignment_raw)
    adjudication_rating = tmp_path / "adjudication-ratings.json"
    adjudication_rating.write_bytes(
        annotations.canonical_json_bytes(
            {
                "schema_version": "vlm_fallback.blinded_rating.v1",
                "assignment_sha256": annotations.sha256_bytes(assignment_raw),
                "rater_slot": "adjudicator",
                "rows": [
                    {
                        "item_id": item["item_id"],
                        "labels": {check: "fail" for check in item["checks"]},
                    }
                    for item in assignment["items"]
                ],
            }
        )
        + b"\n"
    )
    if output_kind == "same":
        output_root = adjudication_root
    elif output_kind == "child":
        output_root = adjudication_root / "sealed"
    elif output_kind == "symlink-child":
        alias = tmp_path / "adjudication-alias"
        alias.symlink_to(adjudication_root, target_is_directory=True)
        output_root = alias / "sealed"
    elif output_kind == "symlink-escape":
        outside = tmp_path / "outside-adjudication"
        outside.mkdir()
        alias = adjudication_root / "escape"
        alias.symlink_to(outside, target_is_directory=True)
        output_root = alias / "sealed"
    else:
        outside = tmp_path / "outside-adjudication"
        outside.mkdir()
        escape = adjudication_root / "escape"
        escape.symlink_to(outside, target_is_directory=True)
        alias = tmp_path / "adjudication-chain-alias"
        alias.symlink_to(adjudication_root, target_is_directory=True)
        output_root = alias / "escape" / "sealed"

    with pytest.raises(annotations.AnnotationIntegrityError, match="outside.*adjudication"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            adjudication_map_path=adjudication_map,
            adjudication_root=adjudication_root,
            adjudication_rating_path=adjudication_rating,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert not (adjudication_root / "sealed").exists()
    assert not (tmp_path / "outside-adjudication" / "sealed").exists()


def test_seal_publish_failure_leaves_no_public_or_staging_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_root = tmp_path / "sealed"

    def fail_publish(source, destination, *, parent_fd=None) -> None:
        raise OSError("simulated atomic publication failure")

    monkeypatch.setattr(annotations, "_rename_directory_noreplace", fail_publish)
    with pytest.raises(annotations.AnnotationIntegrityError, match="atomic publication"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert not output_root.exists()
    assert not list(tmp_path.glob(".sealed.staging-*"))


def test_seal_staging_initialization_failure_removes_private_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_root = tmp_path / "sealed"
    original_chmod = Path.chmod

    def fail_staging_chmod(path: Path, mode: int) -> None:
        if path.name.startswith(".sealed.staging-"):
            raise OSError("simulated staging permission failure")
        original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", fail_staging_chmod)
    with pytest.raises(annotations.AnnotationIntegrityError, match="staging cannot be created"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert not output_root.exists()
    assert not list(tmp_path.glob(".sealed.staging-*"))


def test_seal_staging_initialization_cleanup_failure_preserves_quarantine_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_parent = tmp_path / "private-output"
    output_root = output_parent / "sealed"
    relocated_parent = tmp_path / "relocated-private-output"
    original_chmod = Path.chmod
    original_rmtree = annotations.shutil.rmtree

    def relocate_then_fail_staging_chmod(path: Path, mode: int) -> None:
        if path.name.startswith(".sealed.staging-"):
            output_parent.rename(relocated_parent)
            output_parent.mkdir(mode=0o700)
            raise OSError("simulated staging permission failure")
        original_chmod(path, mode)

    def fail_pinned_cleanup(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            raise OSError("simulated pinned cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", relocate_then_fail_staging_chmod)
    monkeypatch.setattr(annotations.shutil, "rmtree", fail_pinned_cleanup)

    with pytest.raises(annotations.AnnotationIntegrityError, match="quarantine required at"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert list(relocated_parent.glob(".sealed.staging-*"))


def test_seal_atomic_publication_never_replaces_concurrent_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_root = tmp_path / "sealed"
    real_publish = annotations._rename_directory_noreplace
    raced = False

    def create_destination_then_publish(
        source: Path, destination: Path, *, parent_fd: int | None = None
    ) -> None:
        nonlocal raced
        destination.mkdir()
        raced = True
        real_publish(source, destination, parent_fd=parent_fd)

    monkeypatch.setattr(
        annotations,
        "_rename_directory_noreplace",
        create_destination_then_publish,
    )
    with pytest.raises(annotations.AnnotationIntegrityError, match="already exists"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert raced is True
    assert output_root.is_dir()
    assert list(output_root.iterdir()) == []
    assert not list(tmp_path.glob(".sealed.staging-*"))


def test_seal_parent_relocation_during_publication_leaves_no_shared_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_parent = tmp_path / "private-sealed-output"
    output_root = output_parent / "sealed"
    relocated_parent = public_root / "relocated-sealed-output"
    real_fstat = annotations.os.fstat
    relocated = False
    fstat_calls = 0

    def relocate_after_helper_pins_parent(fd: int):
        nonlocal fstat_calls, relocated
        result = real_fstat(fd)
        fstat_calls += 1
        if fstat_calls == 2 and not relocated:
            output_parent.rename(relocated_parent)
            output_parent.mkdir(mode=0o700)
            relocated = True
        return result

    monkeypatch.setattr(annotations.os, "fstat", relocate_after_helper_pins_parent)
    with pytest.raises(annotations.AnnotationIntegrityError, match="atomic publication"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert relocated is True
    assert not output_root.exists()
    assert not list(public_root.rglob("test_annotation_payload.json"))
    assert not list(relocated_parent.glob(".sealed.staging-*"))


def test_seal_parent_relocation_after_payload_write_cleans_pinned_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_parent = tmp_path / "private-sealed-output"
    output_root = output_parent / "sealed"
    relocated_parent = public_root / "relocated-before-publish"
    real_write_bytes = Path.write_bytes
    relocated = False

    def write_then_relocate_parent(path: Path, data: bytes) -> int:
        nonlocal relocated
        written = real_write_bytes(path, data)
        if path.name == "visible_gold_seal.json" and not relocated:
            output_parent.rename(relocated_parent)
            output_parent.mkdir(mode=0o700)
            relocated = True
        return written

    monkeypatch.setattr(Path, "write_bytes", write_then_relocate_parent)
    with pytest.raises(annotations.AnnotationIntegrityError, match="atomic publication"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert relocated is True
    assert not list(public_root.rglob("test_annotation_payload.json"))
    assert not list(relocated_parent.glob(".sealed.staging-*"))


def test_seal_parent_drift_cleanup_failure_requires_manual_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_parent = tmp_path / "private-sealed-output"
    output_root = output_parent / "sealed"
    relocated_parent = public_root / "relocated-before-publish"
    real_write_bytes = Path.write_bytes
    real_rmtree = annotations.shutil.rmtree
    relocated = False
    cleanup_attempted = False

    def write_then_relocate_parent(path: Path, data: bytes) -> int:
        nonlocal relocated
        written = real_write_bytes(path, data)
        if path.name == "visible_gold_seal.json" and not relocated:
            output_parent.rename(relocated_parent)
            output_parent.mkdir(mode=0o700)
            relocated = True
        return written

    def fail_pinned_cleanup(path, *args, **kwargs):
        nonlocal cleanup_attempted
        if kwargs.get("dir_fd") is not None:
            cleanup_attempted = True
            raise OSError("simulated pinned cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", write_then_relocate_parent)
    monkeypatch.setattr(annotations.shutil, "rmtree", fail_pinned_cleanup)
    with pytest.raises(annotations.AnnotationIntegrityError, match="quarantine required"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert relocated is True
    assert cleanup_attempted is True
    assert list(relocated_parent.rglob("test_annotation_payload.json"))


def test_seal_rejects_non_private_output_parent_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_parent = tmp_path / "shared-output-parent"
    output_parent.mkdir(mode=0o755)
    output_parent.chmod(0o755)

    with pytest.raises(annotations.AnnotationIntegrityError, match="owner-only"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_parent / "sealed",
            blind_key=b"k" * 32,
        )

    assert list(output_parent.iterdir()) == []


def test_seal_rejects_output_parent_below_untrusted_writable_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    shared_ancestor = tmp_path / "untrusted-shared-parent"
    shared_ancestor.mkdir(mode=0o777)
    shared_ancestor.chmod(0o777)
    output_parent = shared_ancestor / "private-child"
    output_parent.mkdir(mode=0o700)

    with pytest.raises(annotations.AnnotationIntegrityError, match="writable ancestor"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_parent / "sealed",
            blind_key=b"k" * 32,
        )

    assert list(output_parent.iterdir()) == []


@pytest.mark.parametrize("ancestor_mode", [0o755, 0o1777])
def test_seal_rejects_output_parent_below_foreign_owned_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ancestor_mode: int,
) -> None:
    output_parent = tmp_path / "private-output-parent"
    output_parent.mkdir(mode=0o700)
    output_parent.chmod(0o700)
    real_stat = os.stat
    foreign_uid = os.geteuid() + 1

    def report_foreign_owner(path: os.PathLike[str] | str, **kwargs: object) -> os.stat_result:
        metadata = real_stat(path, **kwargs)
        if Path(path) == tmp_path:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR | ancestor_mode,
                st_uid=foreign_uid,
            )
        return metadata

    monkeypatch.setattr(annotations.os, "stat", report_foreign_owner)

    with pytest.raises(annotations.AnnotationIntegrityError, match="untrusted owner"):
        annotations._require_private_output_parent(output_parent)


def test_seal_commit_is_not_reported_failed_when_parent_fd_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_root = tmp_path / "sealed"
    real_close = annotations.os.close
    failed = False

    def close_then_fail(fd: int) -> None:
        nonlocal failed
        real_close(fd)
        failed = True
        raise OSError("simulated post-close failure")

    monkeypatch.setattr(annotations.os, "close", close_then_fail)
    receipt = annotations.seal_annotations(
        private_map_path=private_map,
        public_root=public_root,
        rater_1_rating_path=rater_1,
        rater_2_rating_path=rater_2,
        output_root=output_root,
        blind_key=b"k" * 32,
    )

    assert failed is True
    assert output_root.is_dir()
    assert receipt.test_annotation_payload_sha256 == annotations.sha256_bytes(
        (output_root / "test_annotation_payload.json").read_bytes()
    )


def test_seal_rechecks_assigned_media_at_publication_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    assigned_view = next((public_root / "rater-1" / "media").rglob("*.png"))
    output_root = tmp_path / "sealed"
    original_write_bytes = Path.write_bytes
    changed = False

    def write_bytes_and_change_media(path: Path, data: bytes) -> int:
        nonlocal changed
        if path.name == "train_gold_manifest.json" and not changed:
            changed = True
            original_write_bytes(assigned_view, b"changed-during-seal")
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes_and_change_media)
    with pytest.raises(annotations.AnnotationIntegrityError, match="assignment view digest"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert changed is True
    assert not output_root.exists()
    assert not list(tmp_path.glob(".sealed.staging-*"))


def test_adjudication_pack_contains_only_disputes_and_not_rater_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    first_assignment = json.loads((public_root / "rater-1" / "assignment.json").read_text())
    first_item = first_assignment["items"][0]["item_id"]
    rater_1 = _rating(public_root, "rater_1")
    rater_2_assignment = json.loads((public_root / "rater-2" / "assignment.json").read_text())
    second_item = rater_2_assignment["items"][0]["item_id"]
    rater_2 = _rating(
        public_root,
        "rater_2",
        overrides={second_item: {"orientation": "insufficient_view"}},
    )
    adjudication_root = tmp_path / "adjudication"
    adjudication_map = tmp_path / "private" / "adjudication-map.json"

    receipt = annotations.prepare_adjudication(
        private_map_path=private_map,
        public_root=public_root,
        rater_1_rating_path=rater_1,
        rater_2_rating_path=rater_2,
        adjudication_root=adjudication_root,
        adjudication_map_path=adjudication_map,
        blind_key=b"k" * 32,
    )

    assert receipt.disagreement_count == 1
    assignment = json.loads((adjudication_root / "assignment.json").read_text())
    assert len(assignment["items"]) == 1
    assert assignment["items"][0]["checks"] == ["orientation"]
    assert first_item not in (adjudication_root / "assignment.json").read_text()
    assignment_bytes = (adjudication_root / "assignment.json").read_bytes()
    assert b'"rater_1_status"' not in assignment_bytes
    assert b'"rater_2_status"' not in assignment_bytes


@pytest.mark.parametrize("change_timing", ["before-rating", "publication-boundary"])
def test_seal_rejects_adjudication_media_changed_after_rating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change_timing: str,
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    private_mapping = json.loads(private_map.read_text())
    disputed_item = private_mapping["entries"][0]["item_ids"]["rater_2"]
    rater_2 = _rating(
        public_root,
        "rater_2",
        overrides={disputed_item: {"orientation": "fail"}},
    )
    adjudication_root = tmp_path / "adjudication"
    adjudication_map = tmp_path / "private" / "adjudication-map.json"
    annotations.prepare_adjudication(
        private_map_path=private_map,
        public_root=public_root,
        rater_1_rating_path=rater_1,
        rater_2_rating_path=rater_2,
        adjudication_root=adjudication_root,
        adjudication_map_path=adjudication_map,
        blind_key=b"k" * 32,
    )
    assignment_raw = (adjudication_root / "assignment.json").read_bytes()
    assignment = json.loads(assignment_raw)
    adjudication_rating = tmp_path / "adjudication-ratings.json"
    adjudication_rating.write_bytes(
        annotations.canonical_json_bytes(
            {
                "schema_version": "vlm_fallback.blinded_rating.v1",
                "assignment_sha256": annotations.sha256_bytes(assignment_raw),
                "rater_slot": "adjudicator",
                "rows": [
                    {
                        "item_id": item["item_id"],
                        "labels": {check: "fail" for check in item["checks"]},
                    }
                    for item in assignment["items"]
                ],
            }
        )
        + b"\n"
    )
    assigned_view = next(adjudication_root.rglob("*.png"))
    changed = False
    if change_timing == "before-rating":
        assigned_view.write_bytes(b"replacement-adjudication-media")
        changed = True
    else:
        original_write_bytes = Path.write_bytes

        def write_bytes_and_change_media(path: Path, data: bytes) -> int:
            nonlocal changed
            if path.name == "train_gold_manifest.json" and not changed:
                changed = True
                original_write_bytes(assigned_view, b"changed-during-seal")
            return original_write_bytes(path, data)

        monkeypatch.setattr(Path, "write_bytes", write_bytes_and_change_media)
    output_root = tmp_path / "sealed"

    with pytest.raises(annotations.AnnotationIntegrityError, match="assignment view digest"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            adjudication_map_path=adjudication_map,
            adjudication_root=adjudication_root,
            adjudication_rating_path=adjudication_rating,
            output_root=output_root,
            blind_key=b"k" * 32,
        )

    assert changed is True
    assert not output_root.exists()


def test_seal_requires_adjudication_and_emits_split_bound_gold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    private_mapping = json.loads(private_map.read_text())
    disputed_item = next(
        entry["item_ids"]["rater_2"]
        for entry in private_mapping["entries"]
        if entry["case_id"] == "secret-case-3"
    )
    rater_2 = _rating(
        public_root,
        "rater_2",
        overrides={disputed_item: {"spatial_relation": "fail"}},
    )
    adjudication_root = tmp_path / "adjudication"
    adjudication_map = tmp_path / "private" / "adjudication-map.json"
    annotations.prepare_adjudication(
        private_map_path=private_map,
        public_root=public_root,
        rater_1_rating_path=rater_1,
        rater_2_rating_path=rater_2,
        adjudication_root=adjudication_root,
        adjudication_map_path=adjudication_map,
        blind_key=b"k" * 32,
    )

    with pytest.raises(annotations.AnnotationIntegrityError, match="adjudication.*required"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=rater_1,
            rater_2_rating_path=rater_2,
            output_root=tmp_path / "blocked",
            blind_key=b"k" * 32,
        )

    # The generic helper targets rater directories; write the one-item
    # adjudication response directly.
    adjudication_assignment_raw = (adjudication_root / "assignment.json").read_bytes()
    adjudication_assignment = json.loads(adjudication_assignment_raw)
    adjudication_rating = tmp_path / "adjudication-ratings.json"
    adjudication_rating.write_bytes(
        annotations.canonical_json_bytes(
            {
                "schema_version": "vlm_fallback.blinded_rating.v1",
                "assignment_sha256": annotations.sha256_bytes(adjudication_assignment_raw),
                "rater_slot": "adjudicator",
                "rows": [
                    {
                        "item_id": adjudication_assignment["items"][0]["item_id"],
                        "labels": {"spatial_relation": "fail"},
                    }
                ],
            }
        )
        + b"\n"
    )
    reformatted_rater_1 = tmp_path / "reformatted-rater-1.json"
    reformatted_rater_1.write_text(
        json.dumps(json.loads(rater_1.read_text()), indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(annotations.AnnotationIntegrityError, match="rater response digest"):
        annotations.seal_annotations(
            private_map_path=private_map,
            public_root=public_root,
            rater_1_rating_path=reformatted_rater_1,
            rater_2_rating_path=rater_2,
            adjudication_map_path=adjudication_map,
            adjudication_root=adjudication_root,
            adjudication_rating_path=adjudication_rating,
            output_root=tmp_path / "reformatted-blocked",
            blind_key=b"k" * 32,
        )

    output_root = tmp_path / "sealed"
    receipt = annotations.seal_annotations(
        private_map_path=private_map,
        public_root=public_root,
        rater_1_rating_path=rater_1,
        rater_2_rating_path=rater_2,
        adjudication_map_path=adjudication_map,
        adjudication_root=adjudication_root,
        adjudication_rating_path=adjudication_rating,
        output_root=output_root,
        blind_key=b"k" * 32,
    )

    assert receipt.disagreement_count == 1
    dev_gold = json.loads((output_root / "dev_gold_manifest.json").read_text())
    test_payload = json.loads((output_root / "test_annotation_payload.json").read_text())
    train_gold = json.loads((output_root / "train_gold_manifest.json").read_text())
    assert [row["case_id"] for row in dev_gold["rows"]] == ["secret-case-2"]
    assert [row["case_id"] for row in test_payload["rows"]] == ["secret-case-3"]
    assert [row["case_id"] for row in train_gold["rows"]] == ["secret-case-1"]
    assert test_payload["rows"][0]["gold"]["spatial_relation"] == "fail"
    assert test_payload["schema_version"] == "vlm_fallback.test_annotation_payload.v2"
    assert test_payload["agreement"]["disagreement_count"] == 1
    assert test_payload["agreement"]["raw_agreement"] == "0.958333"
    assert test_payload["agreement"]["rater_1_rating_sha256"] == annotations.sha256_bytes(
        rater_1.read_bytes()
    )
    assert test_payload["agreement"]["rater_2_rating_sha256"] == annotations.sha256_bytes(
        rater_2.read_bytes()
    )
    assert test_payload["agreement"]["adjudication_rating_sha256"] == (
        annotations.sha256_bytes(adjudication_rating.read_bytes())
    )
    assert (output_root / "test_annotation_payload.json").stat().st_mode & 0o077 == 0
    candidate_raw = (output_root / "sealed_test_annotation_manifest.candidate.json").read_bytes()
    candidate = json.loads(candidate_raw)
    assert candidate["state"] == "sealed"
    assert candidate["case_ids"] == ["secret-case-3"]
    assert candidate["dev_gold_manifest_sha256"] == annotations.sha256_bytes(
        (output_root / "dev_gold_manifest.json").read_bytes()
    )
    assert candidate["test_annotation_payload_sha256"] == annotations.sha256_bytes(
        (output_root / "test_annotation_payload.json").read_bytes()
    )
    gold_seal = json.loads((output_root / "visible_gold_seal.json").read_text())
    assert gold_seal == {
        "schema_version": "vlm_fallback.visible_gold_seal.v1",
        "study_id": spec["study_id"],
        "spec_sha256": "a" * 64,
        "state": "sealed",
        "test_gold_opened": False,
        "annotation_manifest_sha256": annotations.sha256_bytes(candidate_raw),
    }


def test_seal_reports_degenerate_cohen_kappa_as_undefined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_root = tmp_path / "sealed"

    annotations.seal_annotations(
        private_map_path=private_map,
        public_root=public_root,
        rater_1_rating_path=rater_1,
        rater_2_rating_path=rater_2,
        output_root=output_root,
        blind_key=b"k" * 32,
    )

    agreement = json.loads((output_root / "test_annotation_payload.json").read_text())["agreement"]
    assert agreement["cohen_kappa_per_check"]["object_presence"] is None
    assert agreement["cohen_kappa_undefined_reason_per_check"] == {
        check: "expected_agreement_is_one" for check in CHECKS
    }


def test_seal_binds_both_implementation_sources_and_train_gold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, public_root, private_map, _ = _export(tmp_path, monkeypatch)
    assignment = json.loads((public_root / "rater-1" / "assignment.json").read_text())
    mapping = json.loads(private_map.read_text())
    annotation_sha = annotations.sha256_bytes(Path(annotations.__file__).read_bytes())
    protocol_sha = annotations.sha256_bytes(Path(annotations.protocol.__file__).read_bytes())
    implementation_sha = annotations.sha256_bytes(
        annotations.canonical_json_bytes(
            {
                "annotation_source_sha256": annotation_sha,
                "protocol_source_sha256": protocol_sha,
            }
        )
    )
    expected_identity = {
        "annotation_source_sha256": annotation_sha,
        "protocol_source_sha256": protocol_sha,
        "implementation_sha256": implementation_sha,
    }
    assert {field: assignment[field] for field in expected_identity} == expected_identity
    assert {field: mapping[field] for field in expected_identity} == expected_identity
    rater_1 = _rating(public_root, "rater_1")
    rater_2 = _rating(public_root, "rater_2")
    output_root = tmp_path / "sealed"

    receipt = annotations.seal_annotations(
        private_map_path=private_map,
        public_root=public_root,
        rater_1_rating_path=rater_1,
        rater_2_rating_path=rater_2,
        output_root=output_root,
        blind_key=b"k" * 32,
    )

    train_sha = annotations.sha256_bytes((output_root / "train_gold_manifest.json").read_bytes())
    test_payload = json.loads((output_root / "test_annotation_payload.json").read_text())
    candidate = json.loads(
        (output_root / "sealed_test_annotation_manifest.candidate.json").read_text()
    )
    assert receipt.annotation_source_sha256 == annotation_sha
    assert receipt.protocol_source_sha256 == protocol_sha
    assert receipt.implementation_sha256 == implementation_sha
    assert receipt.train_gold_manifest_sha256 == train_sha
    assert test_payload["implementation_identity"] == {
        "annotation_source_sha256": annotation_sha,
        "protocol_source_sha256": protocol_sha,
        "implementation_sha256": implementation_sha,
    }
    assert test_payload["train_gold_manifest_sha256"] == train_sha
    assert candidate["test_annotation_payload_sha256"] == annotations.sha256_bytes(
        (output_root / "test_annotation_payload.json").read_bytes()
    )


def test_cli_export_requires_private_key_permissions_and_prints_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _install_spec(monkeypatch, _spec(repo_root))
    key_path = tmp_path / "blind.key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o644)
    arguments = [
        "export",
        "--spec",
        str(tmp_path / "spec.json"),
        "--repo-root",
        str(repo_root),
        "--public-root",
        str(tmp_path / "public"),
        "--private-map",
        str(tmp_path / "private.json"),
        "--blind-key-file",
        str(key_path),
    ]

    with pytest.raises(annotations.AnnotationIntegrityError, match="permissions"):
        annotations.main(arguments)
    assert not (tmp_path / "public").exists()

    key_path.chmod(0o600)
    assert annotations.main(arguments) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["case_count"] == 3
    assert set(output["assignment_sha256"]) == {"rater_1", "rater_2"}
    assert os.stat(tmp_path / "private.json").st_mode & 0o077 == 0

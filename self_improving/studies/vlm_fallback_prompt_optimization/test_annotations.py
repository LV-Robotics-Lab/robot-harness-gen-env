from __future__ import annotations

import json
import os
from pathlib import Path

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
    expected_tool_sha = annotations.sha256_bytes(Path(annotations.__file__).read_bytes())
    assert first["tool_source_sha256"] == expected_tool_sha
    assert second["tool_source_sha256"] == expected_tool_sha
    assert json.loads(private_map.read_text())["tool_source_sha256"] == expected_tool_sha
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

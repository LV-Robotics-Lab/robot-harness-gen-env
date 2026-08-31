from __future__ import annotations

import json
from pathlib import Path

import pytest

from self_improving.studies.vlm_fallback_prompt_optimization import protocol

STUDY_ROOT = Path(__file__).resolve().parent
REPO_ROOT = STUDY_ROOT.parents[2]


def test_frozen_spec_and_inventory_are_valid() -> None:
    spec = protocol.load_spec(STUDY_ROOT / "experiment_spec.json")

    summary = protocol.validate_spec(spec, repo_root=REPO_ROOT, verify_files=True)

    assert summary == {
        "study_id": "vlm-fallback-prompt-optimization-2026-08-31",
        "experiment_ids": ["A_visible_semantic_correction", "B_typed_failure_prompt_fallback"],
        "sample_counts": {
            "A_visible_semantic_correction": {"train": 13, "dev": 12, "test": 14},
            "B_typed_failure_prompt_fallback": {"train": 12, "dev": 10, "test": 14},
        },
        "verified_artifact_count": 213,
    }


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(schema_version="wrong"), "schema_version"),
        (lambda value: value.update(study_id="wrong"), "study_id"),
        (lambda value: value.update(physical_gate_authority="vlm"), "physical_gate_authority"),
        (lambda value: value["models"].pop(), "models"),
        (lambda value: value.update(models={}), "models"),
        (lambda value: value["models"][0].update(local_files_only=False), "local_files_only"),
        (lambda value: value["models"][0].update(revision="short"), "models.revision"),
        (lambda value: value["models"][0].update(revision="z" * 40), "models.revision"),
        (lambda value: value["models"][0].update(revision="A" * 40), "models.revision"),
        (lambda value: value.update(experiments={}), "experiments"),
        (
            lambda value: value["experiments"][0].update(
                experiment_id="B_typed_failure_prompt_fallback"
            ),
            "experiment_ids",
        ),
        (lambda value: value["experiments"].__setitem__(0, "bad"), "experiment_ids"),
        (lambda value: value["experiments"][0].update(samples=[]), "samples"),
        (lambda value: value["experiments"][0].update(samples={}), "samples"),
        (lambda value: value["experiments"][0]["samples"].__setitem__(0, "bad"), "case_id"),
        (
            lambda value: value["experiments"][0]["samples"][1].update(
                case_id=value["experiments"][0]["samples"][0]["case_id"]
            ),
            "duplicate case_id",
        ),
        (
            lambda value: value["experiments"][0]["samples"][0].update(split="holdout"),
            "split",
        ),
        (
            lambda value: value["experiments"][0]["samples"][0].update(group_id=""),
            "group_id",
        ),
        (
            lambda value: value["experiments"][0]["samples"][0].update(group_id=7),
            "group_id",
        ),
        (
            lambda value: value["experiments"][0].update(
                declared_sample_count=value["experiments"][0]["declared_sample_count"] + 1
            ),
            "declared_sample_count",
        ),
        (
            lambda value: value["experiments"][0]["samples"][0].update(artifacts=[]),
            "artifacts",
        ),
        (
            lambda value: value["experiments"][0]["samples"][0].update(artifacts={}),
            "artifacts",
        ),
        (
            lambda value: value["experiments"][0]["samples"][0].update(bundle_sha256="0" * 64),
            "bundle_sha256",
        ),
    ],
)
def test_spec_validation_fails_closed(mutation, match: str) -> None:
    spec = json.loads((STUDY_ROOT / "experiment_spec.json").read_text(encoding="utf-8"))
    mutation(spec)

    with pytest.raises(ValueError, match=match):
        protocol.validate_spec(spec, repo_root=REPO_ROOT, verify_files=True)


def test_bundle_manifest_rejects_missing_and_escaping_paths(tmp_path: Path) -> None:
    present = tmp_path / "present.txt"
    present.write_text("evidence\n", encoding="utf-8")
    assert protocol.build_bundle_manifest(tmp_path, ["present.txt"])[0]["size_bytes"] == 9

    with pytest.raises(ValueError, match="missing artifact"):
        protocol.build_bundle_manifest(tmp_path, ["missing.txt"])
    with pytest.raises(ValueError, match="escapes repository root"):
        protocol.build_bundle_manifest(tmp_path, ["../outside.txt"])
    with pytest.raises(ValueError, match="escapes repository root"):
        protocol.build_bundle_manifest(tmp_path, [str(present.resolve())])

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes repository root"):
        protocol.build_bundle_manifest(tmp_path, ["link.txt"])


def test_spec_can_be_checked_without_touching_artifacts() -> None:
    spec = protocol.load_spec(STUDY_ROOT / "experiment_spec.json")

    summary = protocol.validate_spec(spec, repo_root=REPO_ROOT, verify_files=False)

    assert summary["verified_artifact_count"] == 0


def test_load_spec_requires_an_object(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        protocol.load_spec(path)


def test_visible_metrics_treat_abstention_as_coverage_not_a_pass() -> None:
    rows = [
        {
            "case_id": "one",
            "gold": {"presence": "pass", "relation": "fail", "orientation": "not_applicable"},
            "prediction": {"presence": "pass", "relation": "fail", "orientation": "abstain"},
        },
        {
            "case_id": "two",
            "gold": {"presence": "fail", "relation": "pass", "orientation": "pass"},
            "prediction": {"presence": "pass", "relation": "abstain", "orientation": "pass"},
        },
    ]

    result = protocol.visible_metrics(rows)

    assert result["eligible_decisions"] == 5
    assert result["covered_decisions"] == 4
    assert result["coverage"] == pytest.approx(0.8)
    assert result["selective_accuracy"] == pytest.approx(0.75)
    assert result["unsafe_visible_pass_count"] == 1
    assert result["per_check"]["presence"]["f1_fail"] == pytest.approx(0.0)
    assert result["per_check"]["relation"]["f1_fail"] == pytest.approx(1.0)
    assert result["macro_f1_fail"] == pytest.approx(0.5)


def test_visible_metrics_reject_malformed_or_empty_rows() -> None:
    with pytest.raises(ValueError, match="at least one row"):
        protocol.visible_metrics([])
    with pytest.raises(ValueError, match="same checks"):
        protocol.visible_metrics(
            [{"case_id": "bad", "gold": {"presence": "pass"}, "prediction": {}}]
        )


def test_visible_metrics_handle_only_not_applicable_or_no_fail_gold() -> None:
    no_eligible = protocol.visible_metrics(
        [
            {
                "case_id": "na",
                "gold": {"presence": "not_applicable"},
                "prediction": {"presence": "not_applicable"},
            }
        ]
    )
    assert no_eligible["coverage"] == 0.0
    assert no_eligible["selective_accuracy"] == 0.0
    assert no_eligible["macro_f1_fail"] == 0.0

    no_fail_gold = protocol.visible_metrics(
        [
            {
                "case_id": "pass-abstain",
                "gold": {"presence": "pass"},
                "prediction": {"presence": "abstain"},
            }
        ]
    )
    assert no_fail_gold["covered_decisions"] == 0
    assert no_fail_gold["macro_f1_fail"] == 0.0
    with pytest.raises(ValueError, match="gold status"):
        protocol.visible_metrics(
            [
                {
                    "case_id": "bad",
                    "gold": {"presence": "maybe"},
                    "prediction": {"presence": "pass"},
                }
            ]
        )
    with pytest.raises(ValueError, match="prediction status"):
        protocol.visible_metrics(
            [
                {
                    "case_id": "bad",
                    "gold": {"presence": "pass"},
                    "prediction": {"presence": "maybe"},
                }
            ]
        )


def test_routing_metrics_keep_safe_blockers_separate_from_completion() -> None:
    rows = [
        {
            "case_id": "feasible-ok",
            "gold_route": "compile",
            "predicted_route": "compile",
            "recoverable": True,
            "robust_completion": True,
            "unsafe_publication": False,
        },
        {
            "case_id": "feasible-miss",
            "gold_route": "repair_prompt",
            "predicted_route": "compile",
            "recoverable": True,
            "robust_completion": False,
            "unsafe_publication": True,
        },
        {
            "case_id": "blocked",
            "gold_route": "abstain_blocked",
            "predicted_route": "abstain_blocked",
            "recoverable": False,
            "robust_completion": False,
            "unsafe_publication": False,
        },
    ]

    result = protocol.routing_metrics(rows)

    assert result == {
        "case_count": 3,
        "route_accuracy": pytest.approx(2 / 3),
        "recoverable_case_count": 2,
        "robust_completion_rate": pytest.approx(0.5),
        "unrecoverable_case_count": 1,
        "safe_abstention_rate": pytest.approx(1.0),
        "unsafe_publication_count": 1,
    }


def test_routing_metrics_reject_empty_rows() -> None:
    with pytest.raises(ValueError, match="at least one row"):
        protocol.routing_metrics([])


def test_routing_metrics_handle_single_class_denominators() -> None:
    recoverable_only = protocol.routing_metrics(
        [
            {
                "case_id": "r",
                "gold_route": "compile",
                "predicted_route": "compile",
                "recoverable": True,
                "robust_completion": True,
                "unsafe_publication": False,
            }
        ]
    )
    assert recoverable_only["safe_abstention_rate"] == 0.0

    unrecoverable_only = protocol.routing_metrics(
        [
            {
                "case_id": "u",
                "gold_route": "abstain_blocked",
                "predicted_route": "wrong",
                "recoverable": False,
                "robust_completion": False,
                "unsafe_publication": False,
            }
        ]
    )
    assert unrecoverable_only["robust_completion_rate"] == 0.0
    assert unrecoverable_only["safe_abstention_rate"] == 0.0


def test_cluster_bootstrap_is_paired_reproducible_and_group_weighted() -> None:
    rows = [
        {"group_id": "g1", "baseline": 0.0, "candidate": 1.0},
        {"group_id": "g1", "baseline": 1.0, "candidate": 1.0},
        {"group_id": "g2", "baseline": 0.0, "candidate": 0.0},
    ]

    first = protocol.paired_cluster_bootstrap_delta(rows, resamples=100, seed=17)
    second = protocol.paired_cluster_bootstrap_delta(rows, resamples=100, seed=17)

    assert first == second
    assert first["groups"] == 2
    assert first["point_delta"] == pytest.approx(0.25)
    assert first["ci_lower"] == pytest.approx(0.0)
    assert first["ci_upper"] == pytest.approx(0.5)


def test_cluster_bootstrap_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="at least one row"):
        protocol.paired_cluster_bootstrap_delta([], resamples=10, seed=1)
    with pytest.raises(ValueError, match="resamples"):
        protocol.paired_cluster_bootstrap_delta(
            [{"group_id": "g", "baseline": 0, "candidate": 1}],
            resamples=0,
            seed=1,
        )

    singleton = protocol.paired_cluster_bootstrap_delta(
        [{"group_id": "g", "baseline": 0, "candidate": 1}],
        resamples=1,
        seed=1,
    )
    assert singleton["ci_lower"] == singleton["ci_upper"] == 1.0


def test_exact_mcnemar_covers_discordant_and_tied_cases() -> None:
    assert protocol.exact_mcnemar([False, False, True], [True, True, True]) == {
        "baseline_only_correct": 0,
        "candidate_only_correct": 2,
        "discordant_pairs": 2,
        "two_sided_exact_p": pytest.approx(0.5),
    }
    assert protocol.exact_mcnemar([True], [True])["two_sided_exact_p"] == 1.0

    with pytest.raises(ValueError, match="same non-zero length"):
        protocol.exact_mcnemar([], [])
    with pytest.raises(ValueError, match="same non-zero length"):
        protocol.exact_mcnemar([True], [True, False])


def test_cli_validate_and_inventory(capsys) -> None:
    spec_path = STUDY_ROOT / "experiment_spec.json"
    assert protocol.main(["validate", str(spec_path), "--repo-root", str(REPO_ROOT)]) == 0
    validate_result = json.loads(capsys.readouterr().out)
    assert validate_result["verified_artifact_count"] == 213

    assert protocol.main(["inventory", str(spec_path)]) == 0
    inventory_result = json.loads(capsys.readouterr().out)
    assert inventory_result["models"][0]["available"] is True
    assert inventory_result["models"][0]["identity_match"] is True
    assert (
        inventory_result["models"][0]["observed_snapshot_manifest_sha256"]
        == "b0f4cf794ec552f5e23de019107801e5adfec633d3b5c74564cdf6b65e9f1cad"
    )
    assert inventory_result["expensive_execution_started"] is False


def test_inventory_reports_missing_model_without_guessing(tmp_path: Path) -> None:
    spec = protocol.load_spec(STUDY_ROOT / "experiment_spec.json")
    spec["models"][0]["local_snapshot_path"] = str(tmp_path / "missing")

    result = protocol.inventory_summary(spec)

    assert result["models"][0]["available"] is False
    assert result["models"][0]["observed_snapshot_manifest_sha256"] is None
    assert result["models"][0]["identity_match"] is False


def test_hf_snapshot_manifest_requires_files(tmp_path: Path) -> None:
    blob = tmp_path / "blob-sha"
    blob.write_text("weights\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.json").symlink_to(blob)

    assert protocol.build_hf_snapshot_manifest(snapshot) == [
        {"path": "config.json", "blob": "blob-sha", "size_bytes": 8}
    ]

    (snapshot / "directory").mkdir()
    with pytest.raises(ValueError, match="non-file snapshot entry"):
        protocol.build_hf_snapshot_manifest(snapshot)

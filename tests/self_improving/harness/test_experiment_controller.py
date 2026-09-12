"""Protocol doubles test scheduling only, never simulator or model success."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from self_improving.harness.experiment_controller import run_experiment


class ScriptedService:
    def __init__(self, responses=(), history=()):
        self.responses = list(responses)
        self.history = list(history)
        self.calls = []
        self.facts = {}

    def status(self, *, workflow_id):
        return deepcopy(
            dict(
                workflow_id=workflow_id,
                facts=self.facts,
                history=self.history,
                snapshot=dict(status="active", revision=len(self.history), active_operation=None),
            )
        )

    def execute_stage(self, workflow_id, stage, idempotency_key, proposal=None):
        self.calls.append((stage, idempotency_key, proposal))
        body = deepcopy(self.responses.pop(0)) if self.responses else dict(status="passed")
        if stage == "compile":
            body.setdefault("scene_ref", {"sha256": str(len(self.history)) * 64})
        self.history.append(
            dict(
                stage=stage,
                body=body,
                status=body["status"],
                receipt={"sha256": str(len(self.history)) * 64},
            )
        )


def test_public_resume_runs_each_stage_once_and_preserves_parent_status():
    service = ScriptedService()
    controller = SimpleNamespace(service=service)
    result = run_experiment(controller, "workflow")
    assert [stage for stage, _, _ in service.calls] == [
        "interpret",
        "acquire",
        "compile",
        "replay",
        "observe",
        "visual",
        "validate",
    ]
    assert result["status"] == "passed"
    assert result["snapshot"]["status"] == "active"
    assert result["parent_terminal"] is False
    assert run_experiment(controller, "workflow")["status"] == "passed"
    assert len(service.calls) == 7


def test_scene_repair_replays_all_downstream_stages_despite_stale_success_facts():
    failure = dict(
        status="failed", error_code="visual_layout", repair_kind="scene", reason="wrong side"
    )
    service = ScriptedService([*([dict(status="passed")] * 5), failure])
    service.facts = {"environment.validation": {"sha256": "old"}}
    result = run_experiment(SimpleNamespace(service=service), "workflow")
    assert result["status"] == "passed"
    assert [stage for stage, _, _ in service.calls] == [
        "interpret",
        "acquire",
        "compile",
        "replay",
        "observe",
        "visual",
        "revise_scene",
        "compile",
        "replay",
        "observe",
        "visual",
        "validate",
    ]
    assert service.calls[6][2] == {"failure_receipt": service.history[5]["receipt"]}


@pytest.mark.parametrize("has_model_receipt", [False, True])
def test_asset_repair_requires_retained_model_proposal(has_model_receipt):
    failure = dict(
        status="failed", error_code="wrong_scale", repair_kind="asset", asset_patch={"scale": 0.5}
    )
    if has_model_receipt:
        failure["model_proposal_receipt"] = {"sha256": "f" * 64}
    service = ScriptedService([*([dict(status="passed")] * 5), failure])
    result = run_experiment(SimpleNamespace(service=service), "workflow")
    if has_model_receipt:
        assert result["status"] == "passed"
        assert service.calls[6][0] == "revise_asset"
        assert service.calls[6][2] == {"scale": 0.5}
        assert service.calls[7][0] == "compile"
    else:
        assert result["status"] == "blocked"
        assert result["error_code"] == "missing_model_repair_proposal"
        assert len(service.calls) == 6


@pytest.mark.parametrize("kind", ["scene", "asset"])
def test_repair_budget_is_reconstructed_from_history(kind):
    passed = dict(status="passed")
    failure = dict(
        status="failed",
        repair_kind=kind,
        error_code="mismatch",
        asset_patch={"scale": 0.5},
        model_proposal_receipt={"sha256": "f" * 64},
    )
    responses = [passed] * 5 + [{**failure, "reason": "first"}]
    responses += [passed] * 4 + [{**failure, "reason": "second"}]
    responses += [passed] * 4 + [{**failure, "reason": "third"}]
    service = ScriptedService(responses)
    controller = SimpleNamespace(service=service)
    result = run_experiment(controller, "workflow")
    assert result["status"] == "failed"
    assert result["error_code"] == "repair_budget_exhausted"
    assert sum(stage == "revise_" + kind for stage, _, _ in service.calls) == 2
    before = len(service.calls)
    assert run_experiment(controller, "workflow")["error_code"] == "repair_budget_exhausted"
    assert len(service.calls) == before


def test_identical_diagnostic_and_scene_stops_after_one_ineffective_repair():
    passed = dict(status="passed")
    compiled = dict(status="passed", scene_ref={"sha256": "c" * 64})
    failure = dict(
        status="failed",
        repair_kind="scene",
        error_code="mismatch",
        diagnostic_summary={"position": "left instead of right"},
    )
    responses = [
        passed,
        passed,
        compiled,
        passed,
        passed,
        failure,
        passed,
        compiled,
        passed,
        passed,
        failure,
    ]
    service = ScriptedService(responses)
    result = run_experiment(SimpleNamespace(service=service), "workflow")
    assert result["status"] == "failed"
    assert result["error_code"] == "repeated_failure"
    assert len(service.calls) == 11


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            {"status": "blocked", "error_code": "blocked_external_resource"},
            "blocked_external_resource",
        ),
        ({"status": "failed", "repair_kind": "numeric"}, "numeric_repair_not_implemented"),
        ({"status": "failed", "error_code": "unclassified"}, "unclassified"),
    ],
)
def test_unavailable_or_unclassified_repairs_do_not_consume_model_retries(body, expected):
    service = ScriptedService([body])
    result = run_experiment(SimpleNamespace(service=service), "workflow")
    assert result["error_code"] == expected
    assert len(service.calls) == 1


def test_incomplete_operation_is_not_reissued():
    service = ScriptedService(history=[dict(stage="interpret", status="running", body={})])
    assert (
        run_experiment(SimpleNamespace(service=service), "workflow")["error_code"]
        == "recoverable_operation_pending"
    )
    assert not service.calls


def test_noncommitting_service_cannot_make_driver_spin():
    class Noncommitting(ScriptedService):
        def execute_stage(self, *args, **kwargs):
            self.calls.append(args)

    service = Noncommitting()
    assert (
        run_experiment(SimpleNamespace(service=service), "workflow")["error_code"]
        == "stage_did_not_commit"
    )
    assert len(service.calls) == 1


def test_interruption_retains_last_service_snapshot():
    class Interrupting(ScriptedService):
        def execute_stage(self, *args, **kwargs):
            raise RuntimeError("child disconnected")

    result = run_experiment(SimpleNamespace(service=Interrupting()), "workflow")
    assert result["status"] == "blocked"
    assert result["error_code"] == "stage_execution_interrupted"
    assert result["snapshot"]["revision"] == 0


def test_source_drift_during_stage_cannot_hide_the_original_failure():
    class Drifting(ScriptedService):
        changed = False

        def execute_stage(self, *args, **kwargs):
            self.changed = True
            raise RuntimeError("child source changed")

        def status(self, *, workflow_id):
            if self.changed:
                raise ValueError("deployment source changed")
            return super().status(workflow_id=workflow_id)

    result = run_experiment(SimpleNamespace(service=Drifting()), "workflow")
    assert result["error_code"] == "stage_execution_interrupted"
    assert result["snapshot_current"] is False
    assert result["reason"] == "child source changed"
    assert result["status_read_error"] == "deployment source changed"

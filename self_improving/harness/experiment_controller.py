"""Bounded experimental scheduling over the existing controller/service journal."""

import hashlib
import json

_STAGES = ("interpret", "acquire", "compile", "replay", "observe", "visual", "validate")


def _failure_signatures(history):
    scene, signatures = None, []
    for item in history:
        body = item.get("body", {})
        status = body.get("status", item.get("status"))
        if item["stage"] == "compile" and status == "passed":
            scene = body.get("scene_ref", {}).get("sha256")
        if status == "failed":
            diagnostic = body.get(
                "diagnostic_summary", body.get("reason", body.get("diagnostics", []))
            )
            signatures.append(
                hashlib.sha256(
                    json.dumps(
                        [body.get("error_code", "stage_failed"), scene, diagnostic],
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                ).hexdigest()
            )
    return signatures


def _result(state, status, code=None, **details):
    return {
        **state,
        "status": status,
        "snapshot_current": True,
        "parent_terminal": state.get("snapshot", {}).get("status")
        in {"succeeded", "failed", "cancelled"},
        "qualification_status": "development_unqualified",
        **({"error_code": code} if code else {}),
        **details,
    }


def _progress(history):
    next_index, failure = 0, None
    for item in history:
        stage = item["stage"]
        status = item.get("body", {}).get("status", item.get("status"))
        if status == "running":
            return next_index, item
        if status not in {"passed", "failed", "blocked"}:
            raise ValueError("journal stage has no terminal business status")
        if stage in {"revise_scene", "revise_asset"}:
            if status == "passed":
                next_index, failure = 2, None
            else:
                failure = item
        elif stage in _STAGES and _STAGES.index(stage) == next_index:
            if status == "passed":
                next_index, failure = next_index + 1, None
            else:
                failure = item
        else:
            raise ValueError("journal stages are not a sequential experiment cycle")
    return next_index, failure


def run_experiment(controller, workflow_id):
    """Drive bounded stages; the service alone persists operations and trusted facts.

    Returned ``passed`` means the recorded experimental stage sequence passed, not that
    the parent workflow was promoted, published or changed to a terminal state.
    """
    service = controller.service
    for _ in range(40):
        state = service.status(workflow_id=workflow_id)
        history = state.get("history", [])
        if state.get("snapshot", {}).get("active_operation") is not None:
            return _result(state, "blocked", "recoverable_operation_pending")
        try:
            index, failure = _progress(history)
        except (KeyError, TypeError, ValueError) as error:
            return _result(state, "blocked", "invalid_experiment_history", reason=str(error))
        if failure is not None:
            body = failure.get("body", {})
            if body.get("status", failure.get("status")) == "running":
                return _result(state, "blocked", "recoverable_operation_pending")
            if body.get("status") == "blocked":
                return _result(
                    state,
                    "blocked",
                    body.get("error_code", "stage_blocked"),
                    failed_stage=failure["stage"],
                )
            if failure["stage"] in {"revise_scene", "revise_asset"}:
                return _result(
                    state, "failed", "repair_stage_failed", failed_stage=failure["stage"]
                )
            signatures = _failure_signatures(history)
            if signatures and signatures.count(signatures[-1]) > 1:
                return _result(
                    state, "failed", "repeated_failure", failure_fingerprint=signatures[-1]
                )
            kind = body.get("repair_kind")
            if kind == "scene" and failure.get("receipt"):
                stage, proposal = "revise_scene", {"failure_receipt": failure["receipt"]}
            elif kind == "asset":
                patch = body.get("asset_patch")
                if (
                    not body.get("model_proposal_receipt")
                    or not isinstance(patch, dict)
                    or not patch
                    or set(patch) - {"scale", "mass_kg", "friction"}
                ):
                    return _result(
                        state,
                        "blocked",
                        "missing_model_repair_proposal",
                        failed_stage=failure["stage"],
                    )
                stage, proposal = "revise_asset", patch
            elif kind == "numeric":
                return _result(
                    state,
                    "blocked",
                    "numeric_repair_not_implemented",
                    failed_stage=failure["stage"],
                )
            else:
                return _result(
                    state,
                    "failed",
                    body.get("error_code", "stage_failed"),
                    failed_stage=failure["stage"],
                )
            count = sum(item["stage"] == stage for item in history)
            if count >= 2:
                return _result(
                    state,
                    "failed",
                    "repair_budget_exhausted",
                    repair_kind=kind,
                    repair_attempts=count,
                )
        elif index == len(_STAGES):
            return _result(state, "passed")
        else:
            stage, proposal = _STAGES[index], None
        key = (
            "experiment-"
            + hashlib.sha256(
                json.dumps([str(workflow_id), len(history), stage], separators=(",", ":")).encode()
            ).hexdigest()
        )
        try:
            service.execute_stage(workflow_id, stage, key, proposal=proposal)
        except Exception as error:
            try:
                current = service.status(workflow_id=workflow_id)
            except Exception as status_error:
                return _result(
                    state,
                    "blocked",
                    "stage_execution_interrupted",
                    stage=stage,
                    reason=str(error),
                    snapshot_current=False,
                    status_read_error=str(status_error),
                )
            return _result(
                current, "blocked", "stage_execution_interrupted", stage=stage, reason=str(error)
            )
        current = service.status(workflow_id=workflow_id)
        if len(current.get("history", [])) <= len(history):
            return _result(current, "blocked", "stage_did_not_commit", stage=stage)
    return _result(service.status(workflow_id=workflow_id), "blocked", "experiment_step_budget")

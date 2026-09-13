"""Explicit managed subprocess double; no real search/model/qualification claim."""

import hashlib
import json

import pytest

from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.contracts import SceneIR
from tests.self_improving.harness.x2env.test_codex import executable
from tests.self_improving.harness.x2env.test_resolver import inputs


def test_managed_search_query_preserves_original_entity_and_evidence(tmp_path):
    from self_improving.harness.x2env.search_advisory import plan_search

    store, _, _, scene_ref, _ = inputs(tmp_path)
    raw = store.read_artifact(scene_ref)
    entity = SceneIR.model_validate_json(raw).entities[0]
    path = executable(
        tmp_path, {"query": "rectangular block", "reason": "A broad lexical description."}
    )
    backend = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "process-double", store
    )
    result = plan_search(
        backend, scene_ref, entity, store=store, output_root=tmp_path / "query", timeout=10
    )
    assert result.status == "completed" and result.query == "rectangular block"
    assert result.original_category == entity.category
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["scene_ir"] == scene_ref.model_dump()
    assert receipt["entity"] == entity.model_dump(mode="json")
    assert receipt["external_agent_executed"] is True
    assert receipt["authority"] == "search_query_advisory_only"
    assert store.read_artifact(scene_ref) == raw


@pytest.mark.parametrize("fault", [None, "scene", "entity", "status"])
def test_revised_search_is_bound_to_previous_failed_entity_evidence(tmp_path, fault):
    from self_improving.harness.x2env.search_advisory import plan_search

    store, _, _, scene_ref, _ = inputs(tmp_path)
    entity = SceneIR.model_validate_json(store.read_artifact(scene_ref)).entities[0]
    failure = store.write_artifact(
        json.dumps(
            {
                "status": "succeeded" if fault == "status" else "blocked",
                "resolved": {
                    "scene_ir": {} if fault == "scene" else scene_ref.model_dump(),
                    "assets": [],
                },
                "unresolved_entities": [] if fault == "entity" else [entity.id],
                "error_code": "web_assets_unresolved",
                "candidates": [{"entity_id": entity.id, "error_code": "unsupported mesh"}],
            }
        ).encode(),
        "application/json",
    )
    program = executable(
        tmp_path, {"query": "different description", "reason": "failed candidates"}
    )
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "double", store
    )
    result = plan_search(
        backend,
        scene_ref,
        entity,
        store=store,
        output_root=tmp_path / "revision",
        timeout=10,
        previous_failure=failure,
    )
    if fault:
        assert result.status == "failed"
        assert not (tmp_path / "revision/process.json").exists()
        return
    assert result.status == "completed"
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["previous_failure"] == failure.model_dump()
    context = json.loads((tmp_path / "revision/context.json").read_bytes())
    assert context["previous_failure"]["error_code"] == "web_assets_unresolved"


@pytest.mark.parametrize(
    "fault", ["url", "authority", "tools", "entity", "missing_backend", "cross_store"]
)
def test_invalid_or_unavailable_planner_cannot_supply_query(tmp_path, fault):
    from self_improving.harness.x2env.search_advisory import plan_search
    from self_improving.harness.x2env.store import Store

    store, _, _, scene_ref, _ = inputs(tmp_path)
    entity = SceneIR.model_validate_json(store.read_artifact(scene_ref)).entities[0]
    response = {"query": "vessel", "reason": "lexical description"}
    if fault == "url":
        response["query"] = "https://example.org/selected.glb"
    if fault == "authority":
        response["license"] = "CC0-1.0"
    path = executable(tmp_path, response, tool=fault == "tools")
    backend = CodexBackend(path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store)
    if fault == "entity":
        entity = entity.model_copy(update={"category": "different"})
    if fault == "missing_backend":
        backend = None
    if fault == "cross_store":
        backend.store = Store(tmp_path / "other")
    result = plan_search(
        backend, scene_ref, entity, store=store, output_root=tmp_path / "attempt", timeout=10
    )
    assert result.status != "completed" and result.query is None
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["error_code"] == result.error_code
    if fault == "missing_backend":
        assert result.status == "blocked" and result.error_code == "blocked_external_resource"
        assert receipt["external_agent_executed"] is False

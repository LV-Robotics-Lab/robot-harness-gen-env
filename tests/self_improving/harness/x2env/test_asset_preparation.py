"""Managed preparation through a real subprocess double, never a real model claim."""

import hashlib
import json

import pytest

from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.contracts import SceneIR
from tests.self_improving.harness.x2env.test_codex import executable
from tests.self_improving.harness.x2env.test_resolver import inputs
from tests.self_improving.harness.x2env.test_web_resolver import ProviderDouble


def test_prepare_binds_known_dimensions_source_bytes_and_actual_model_attempt(tmp_path):
    store, _, _, scene_ref, _ = inputs(tmp_path, dimensions=[0.1, 0.1, 0.1])
    entity = SceneIR.model_validate_json(store.read_artifact(scene_ref)).entities[0]
    provider = ProviderDouble(store, tmp_path)
    candidate = provider.search(entity, "web", 8).candidates[0]
    fetched = provider.fetch(candidate, tmp_path / "fetch")
    path = executable(
        tmp_path,
        dict(
            dimensions_m=[0.1, 0.1, 0.1],
            up_axis="Y",
            mass_kg=0.1,
            friction=0.6,
            base_color=None,
            declared_color=None,
        ),
    )
    backend = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    )
    result = backend.prepare_asset(
        scene_ref, entity, candidate, fetched, output_root=tmp_path / "attempt", timeout=10
    )
    assert result.status == "completed"
    params = result.parameters
    assert params.dimensions_basis == "input_explicit"
    assert params.mass_basis == params.friction_basis == "codex_estimate"
    assert params.up_axis_basis == "source_metadata" and params.up_axis == "Y"
    evidence = json.loads(store.read_artifact(params.evidence))
    assert evidence["fetch_receipt"] == fetched.receipt.model_dump(mode="json")
    assert evidence["parameters"]["advisory_receipt"] == result.receipt.model_dump(mode="json")
    log = json.loads(store.read_artifact(result.receipt))
    assert log["external_agent_executed"] is True and log["model"] == "test-double"
    assert "not_measured_real_object" in (tmp_path / "attempt" / "prompt.txt").read_text()


@pytest.mark.parametrize("fault", ["dimensions", "axis", "mass", "authority", "tools"])
def test_untrusted_preparation_never_yields_parameters(tmp_path, fault):
    store, _, _, scene_ref, _ = inputs(tmp_path, dimensions=[0.1, 0.1, 0.1])
    entity = SceneIR.model_validate_json(store.read_artifact(scene_ref)).entities[0]
    provider = ProviderDouble(store, tmp_path)
    candidate = provider.search(entity, "web", 8).candidates[0]
    fetched = provider.fetch(candidate, tmp_path / "fetch")
    answer = dict(
        dimensions_m=[0.1, 0.1, 0.1],
        up_axis="Y",
        mass_kg=0.1,
        friction=0.6,
        base_color=None,
        declared_color=None,
    )
    if fault == "dimensions":
        answer["dimensions_m"] = [0.2, 0.1, 0.1]
    if fault == "axis":
        answer["up_axis"] = "Z"
    if fault == "mass":
        answer["mass_kg"] = -0.1
    if fault == "authority":
        answer["evidence"] = {"sha256": "a" * 64}
    path = executable(tmp_path, answer, tool=fault == "tools")
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).prepare_asset(
        scene_ref, entity, candidate, fetched, output_root=tmp_path / "attempt", timeout=10
    )
    assert result.status == "failed" and result.parameters is None
    assert store.read_artifact(result.receipt)
    assert (tmp_path / "attempt" / "codex.jsonl").is_file()


def test_partial_dimensions_keep_axis_provenance(tmp_path):
    store, _, _, scene_ref, _ = inputs(tmp_path, dimensions=[0.1, None, None])
    entity = SceneIR.model_validate_json(store.read_artifact(scene_ref)).entities[0]
    provider = ProviderDouble(store, tmp_path)
    candidate = provider.search(entity, "web", 8).candidates[0]
    fetched = provider.fetch(candidate, tmp_path / "fetch")
    path = executable(
        tmp_path,
        dict(
            dimensions_m=[0.1, 0.1, 0.1],
            up_axis="Y",
            mass_kg=0.1,
            friction=0.6,
            base_color=None,
            declared_color=None,
        ),
    )
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).prepare_asset(
        scene_ref, entity, candidate, fetched, output_root=tmp_path / "attempt", timeout=10
    )
    assert result.status == "completed"
    assert json.loads(store.read_artifact(result.receipt))["dimensions_axis_basis"] == [
        "input_explicit",
        "codex_estimate",
        "codex_estimate",
    ]


def test_managed_preparation_evidence_matches_existing_web_resolver_contract(tmp_path):
    from self_improving.harness.x2env.web_resolver import WebAssetResolver
    from tests.self_improving.harness.x2env.test_resolver import VisualDouble
    from tests.self_improving.harness.x2env.test_web_resolver import preview_double

    store, registry, _, scene, image = inputs(tmp_path)
    path = executable(
        tmp_path,
        dict(
            dimensions_m=[0.1, 0.1, 0.1],
            up_axis="Y",
            mass_kg=0.1,
            friction=0.6,
            base_color=None,
            declared_color=None,
        ),
    )
    backend = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    )

    def prepare(entity, candidate, fetched):
        return backend.prepare_asset(
            scene, entity, candidate, fetched, output_root=tmp_path / "preparation", timeout=10
        ).parameters

    result = WebAssetResolver(
        store,
        registry,
        ProviderDouble(store, tmp_path),
        VisualDouble(store),
        preview_double(store, image),
        prepare,
    ).resolve(scene, allowed_sources=("web",), allow_cousin=False, output_root=tmp_path / "resolve")
    assert result.status == "succeeded"
    version = registry.inspect(result.resolved.assets[0].version_sha256)
    params = json.loads(store.read_artifact(version.receipt))["parameters"]
    from self_improving.harness.x2env.contracts import ArtifactRef

    advisory = json.loads(
        store.read_artifact(ArtifactRef.model_validate(params["advisory_receipt"]))
    )
    assert advisory["external_agent_executed"] is True

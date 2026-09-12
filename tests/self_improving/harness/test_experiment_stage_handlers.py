import json
from pathlib import Path

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.experiment_stage_handlers import execute_stage
from self_improving.harness.schemas.common import ArtifactRef


def saved(store, tmp_path, value, name):
    path = tmp_path / name
    path.write_text(json.dumps(value))
    return store.put_file(path, name=name, media_type="application/json", schema_version=None)


def test_generated_primitive_acquisition_and_compile_use_real_components(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    source = saved(store, tmp_path, {"text": "a red block on table"}, "input.json")
    intent = {
        "category": "block",
        "color": "red",
        "source": "generated",
        "selection": "exact",
        "dimensions_m": None,
        "xy_m": [0.0, 0.0],
        "yaw_deg": 0.0,
        "support": "table",
        "object_count": 1,
        "input_sha256": source.sha256,
        "unknowns": [],
    }
    intent_ref = saved(store, tmp_path, {"intent": intent}, "intent.json")
    parameters = {"input_ref": source, "dependencies": {"experiment.intent": intent_ref}}
    settings = {"asset_library_root": str(tmp_path / "library")}
    asset = execute_stage(
        "acquire", parameters, store=store, root=tmp_path / "acquire", settings=settings
    )
    assert asset["status"] == "passed"
    assert asset["receipt"]["source_kind"] == "generated"
    parameters["dependencies"]["experiment.asset"] = saved(store, tmp_path, asset, "asset.json")
    compiled = execute_stage(
        "compile", parameters, store=store, root=tmp_path / "compile", settings=settings
    )
    assert compiled["status"] == "passed"
    scene = json.loads(
        store.resolve(ArtifactRef.model_validate(compiled["scene_ref"])).path.read_bytes()
    )
    assert scene["schema_version"] == "harness.genesis_scene_spec.v1"
    assert compiled["physics_status"] == "not_run"
    original = (Path(asset["root"]) / "receipt.json").read_bytes()
    parameters["proposal"] = {"friction": 0.4}
    revised = execute_stage(
        "revise_asset", parameters, store=store, root=tmp_path / "revise", settings=settings
    )
    assert revised["status"] == "passed"
    assert revised["version_id"] != asset["version_id"]
    assert revised["receipt"]["parent_version"] == asset["version_id"]
    assert (Path(asset["root"]) / "receipt.json").read_bytes() == original


def test_arbitrary_asset_patch_is_rejected_before_loading_asset(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    source = saved(store, tmp_path, {}, "input.json")
    with pytest.raises(ValueError, match="patch"):
        execute_stage(
            "revise_asset",
            {"input_ref": source, "proposal": {"verifier": "pass"}},
            store=store,
            root=tmp_path / "operation",
            settings={},
        )


def test_missing_local_catalog_returns_required_resource(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    source = saved(store, tmp_path, {}, "input.json")
    intent = saved(
        store,
        tmp_path,
        {"intent": {"source": "local", "input_sha256": source.sha256}},
        "intent.json",
    )
    result = execute_stage(
        "acquire",
        {"input_ref": source, "dependencies": {"experiment.intent": intent}},
        store=store,
        root=tmp_path / "operation",
        settings={},
    )
    assert result["status"] == "blocked"
    assert result["required_resources"] == ["catalog_path"]


def test_failed_generation_retains_diagnostic_artifact(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    source = saved(store, tmp_path, {}, "input.json")
    intent = saved(
        store,
        tmp_path,
        {
            "intent": {
                "source": "generated",
                "category": "unsupported_car",
                "selection": "exact",
                "input_sha256": source.sha256,
            }
        },
        "intent.json",
    )
    result = execute_stage(
        "acquire",
        {"input_ref": source, "dependencies": {"experiment.intent": intent}},
        store=store,
        root=tmp_path / "operation",
        settings={"asset_library_root": str(tmp_path / "library")},
    )
    assert result["status"] == "failed"
    assert result["diagnostics"]
    record = json.loads(
        store.resolve(ArtifactRef.model_validate(result["diagnostics"][0])).path.read_bytes()
    )
    assert record["code"] == "asset_acquisition_failed"

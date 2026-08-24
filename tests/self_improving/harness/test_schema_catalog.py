from __future__ import annotations

import json
from pathlib import Path

import pytest

from self_improving.harness.schema_catalog import (
    DEFAULT_SCHEMA_ROOT,
    SCHEMA_MODELS,
    SchemaSnapshotMismatch,
    expected_schema_snapshots,
    export_schema_snapshots,
    render_schema_document,
    schema_documents,
    schema_model,
    schema_snapshot_name,
)
from self_improving.harness.schemas.common import ArtifactRef

EXPECTED_SCHEMA_IDS = {
    "harness.artifact_ref.v1",
    "harness.blocker.v1",
    "harness.environment_package.v1",
    "harness.event.v1",
    "harness.run_state.v1",
    "harness.skill_descriptor.v1",
    "harness.skill_invocation.v1",
    "harness.skill_qualification.v1",
    "harness.text2env_compile_input.v1",
    "harness.text2env_compile_output.v1",
    "harness.text2env_replay_input.v1",
    "harness.text2env_replay_output.v1",
    "harness.text2env_validate_input.v1",
    "harness.text2env_validate_output.v1",
}


def test_schema_catalog_is_exact_and_immutable() -> None:
    assert set(SCHEMA_MODELS) == EXPECTED_SCHEMA_IDS
    assert schema_model("harness.artifact_ref.v1") is ArtifactRef
    with pytest.raises(KeyError):
        schema_model("harness.missing.v1")
    with pytest.raises(TypeError):
        SCHEMA_MODELS["harness.other.v1"] = ArtifactRef  # type: ignore[index]


def test_public_documents_are_strict_draft_2020_12_schemas() -> None:
    documents = schema_documents()
    assert list(documents) == sorted(EXPECTED_SCHEMA_IDS)
    for schema_id, document in documents.items():
        assert document["$id"] == schema_id
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["additionalProperties"] is False
        if schema_id != "harness.artifact_ref.v1":
            assert "schema_version" not in document["properties"]

    assert documents["harness.artifact_ref.v1"]["required"] == [
        "name",
        "uri",
        "media_type",
        "sha256",
        "bytes",
        "schema_version",
    ]
    assert "from_status" in documents["harness.event.v1"]["required"]
    run_required = documents["harness.run_state.v1"]["required"]
    assert {"invocation_digest", "ended_at", "output", "blocker"} <= set(run_required)

    compile_input = documents["harness.text2env_compile_input.v1"]
    assert "config" in compile_input["required"]
    config = compile_input["$defs"]["CompileConfig"]
    assert config["properties"]["generate_missing_assets"]["default"] is False

    runtime_input = documents["harness.text2env_replay_input.v1"]
    runtime = runtime_input["$defs"]["RuntimeConfig"]
    assert runtime["properties"]["contact_window_steps"]["default"] == 120


def test_schema_rendering_and_snapshot_names_are_canonical() -> None:
    name = schema_snapshot_name("harness.artifact_ref.v1")
    assert name == "harness.artifact_ref.v1.schema.json"
    rendered = render_schema_document({"z": 1, "a": "值"})
    assert rendered == '{\n  "a": "值",\n  "z": 1\n}\n'

    snapshots = expected_schema_snapshots()
    assert set(snapshots) == {
        f"{schema_id}.schema.json" for schema_id in EXPECTED_SCHEMA_IDS
    }
    for content in snapshots.values():
        assert content.endswith("\n")
        assert json.loads(content)["$id"] in EXPECTED_SCHEMA_IDS


def test_export_writes_and_verifies_exact_snapshots(tmp_path: Path) -> None:
    paths = export_schema_snapshots(tmp_path)
    assert len(paths) == len(EXPECTED_SCHEMA_IDS)
    assert all(path.is_file() for path in paths)
    assert export_schema_snapshots(tmp_path, check=True) == paths


def test_snapshot_check_reports_missing_changed_and_unexpected(tmp_path: Path) -> None:
    paths = export_schema_snapshots(tmp_path)
    paths[0].unlink()
    paths[1].write_text("{}\n", encoding="utf-8")
    (tmp_path / "harness.unexpected.v1.schema.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(SchemaSnapshotMismatch) as error:
        export_schema_snapshots(tmp_path, check=True)

    assert any(problem.startswith("missing ") for problem in error.value.problems)
    assert any(problem.startswith("changed ") for problem in error.value.problems)
    assert any(problem.startswith("unexpected ") for problem in error.value.problems)
    assert str(error.value).startswith("schema snapshot mismatch:")


def test_committed_snapshots_have_no_drift() -> None:
    paths = export_schema_snapshots(DEFAULT_SCHEMA_ROOT, check=True)
    assert len(paths) == len(EXPECTED_SCHEMA_IDS)

"""The public schema exporter detects snapshots drifting from Python contracts."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_measured_layout_schema_cannot_offer_height_or_asset_size_fields(tmp_path):
    import jsonschema
    import pytest

    from self_improving.harness.x2env.schema_export import export

    export(tmp_path)
    schema = json.loads((tmp_path / "MeasuredLayoutValues.codex.json").read_bytes())
    valid = {"choices": [{"entity_id": "box", "path": "pose.position[0]", "value": 0.2}]}
    jsonschema.validate(valid, schema)
    for path in ("pose.position[2]", "dimensions[2]", "frame", "relations"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"choices": [{**valid["choices"][0], "path": path}]}, schema)
    assert (tmp_path / "MeasuredLayoutValues.json").is_file()
    assert "MeasuredLayoutValues" in (tmp_path / "api-fields.md").read_text()


def test_export_exposes_versioned_generated_layout_values_and_strict_transport(tmp_path):
    from self_improving.harness.x2env.schema_export import export

    export(tmp_path)
    schema = json.loads((tmp_path / "GroundingValuesV2.json").read_text())
    transport = json.loads((tmp_path / "GroundingValuesV2.codex.json").read_text())
    assert schema["properties"]["entities"]["maxItems"] == 8
    assert schema["properties"]["entities"]["minItems"] == 1
    assert "prefixItems" not in json.dumps(transport)
    assert transport["additionalProperties"] is False
    assert "GroundingValuesV2" in (tmp_path / "api-fields.md").read_text()
    (tmp_path / "GroundingValuesV2.codex.json").write_text("{}")
    assert export(tmp_path, check=True) == ("GroundingValuesV2.codex.json",)


def test_export_interface_reports_missing_and_changed_files_without_repairing(tmp_path):
    from self_improving.harness.x2env.schema_export import export

    assert "SceneIR.json" in export(tmp_path, check=True)
    assert export(tmp_path) == ()
    assert export(tmp_path, check=True) == ()
    (tmp_path / "SceneIR.json").write_text("{}")
    assert export(tmp_path, check=True) == ("SceneIR.json",)


def test_export_then_check_detects_manual_schema_drift(tmp_path):
    command = [
        sys.executable,
        str(ROOT / "script/export_x2env_schemas.py"),
        "--output",
        str(tmp_path),
    ]
    generated = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stderr
    assert (tmp_path / "SceneIR.json").is_file()
    assert (tmp_path / "api-fields.md").is_file()
    checked = subprocess.run(command + ["--check"], capture_output=True, text=True, timeout=30)
    assert checked.returncode == 0, checked.stderr
    (tmp_path / "SceneIR.json").write_text("{}\n")
    drift = subprocess.run(command + ["--check"], capture_output=True, text=True, timeout=30)
    assert drift.returncode == 1
    assert "SceneIR.json" in drift.stdout
    assert (tmp_path / "SceneIR.json").read_text() == "{}\n"

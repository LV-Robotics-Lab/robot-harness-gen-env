"""Generate schema snapshots from the sole editable typed contract source."""

import json
from pathlib import Path

from . import contracts


def structured_output_schema(model: type[contracts.Model]) -> dict:
    """Strict transport projection; nullable fields remain present, not omitted."""
    schema = model.model_json_schema()

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
            node.pop("default", None)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


def export(output: Path, *, check: bool = False) -> tuple[str, ...]:
    models = sorted(
        (
            value
            for value in vars(contracts).values()
            if isinstance(value, type)
            and issubclass(value, contracts.Model)
            and value is not contracts.Model
        ),
        key=lambda model: model.__name__,
    )
    files = {}
    rows = [
        "# Canonical x2env API fields",
        "",
        "Generated; edit Python contracts, not this file.",
        "",
        "| Model | Field | Required |",
        "|---|---|---|",
    ]
    for model in models:
        files[model.__name__ + ".json"] = (
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
        for name, field in model.model_fields.items():
            rows.append(f"| {model.__name__} | {name} | {str(field.is_required()).lower()} |")
    files["api-fields.md"] = "\n".join(rows) + "\n"
    files["SceneIntentProposal.codex.json"] = (
        json.dumps(
            structured_output_schema(contracts.SceneIntentProposal), indent=2, sort_keys=True
        )
        + "\n"
    )
    drift = []
    if not check:
        output.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = output / name
        if check:
            if not path.is_file() or path.read_text() != content:
                drift.append(name)
        else:
            path.write_text(content)
    return tuple(drift)

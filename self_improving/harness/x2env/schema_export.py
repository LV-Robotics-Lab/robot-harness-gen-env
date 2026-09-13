"""Generate schema snapshots from the sole editable typed contract source."""

import json
from pathlib import Path

from . import contracts


def structured_output_schema(
    model: type[contracts.Model], *, bundle: contracts.InputBundle | None = None
) -> dict:
    """Strict transport projection; nullable fields remain present, not omitted."""
    schema = model.model_json_schema()

    def visit(node):
        if isinstance(node, dict):
            if "prefixItems" in node:
                members = node["prefixItems"]
                if (
                    node.get("type") != "array"
                    or not isinstance(members, list)
                    or not members
                    or any(member != members[0] for member in members)
                    or node.get("minItems") != len(members)
                    or node.get("maxItems") != len(members)
                    or ("items" in node and node["items"] is not False)
                ):
                    raise ValueError("unsupported_tuple_output_schema")
                node["items"] = members[0]
                del node["prefixItems"]
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
            node.pop("default", None)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    if bundle is not None:
        if model is not contracts.SceneIntentProposal or not isinstance(
            bundle, contracts.InputBundle
        ):
            raise ValueError("invalid_schema_binding_context")
        bundle = contracts.InputBundle.model_validate_json(bundle.model_dump_json())
        sources = [bundle.text.sha256] if bundle.text else []
        sources.extend(image.source.sha256 for image in bundle.images)
        if bundle.video:
            sources.append(bundle.video.source.sha256)
        if not sources:
            raise ValueError("empty_schema_binding_sources")
        scene = schema["$defs"]["SceneIR"]["properties"]
        scene["input_sha256"]["const"] = bundle.request_sha256
        scene["revision"]["const"] = 0
        schema["$defs"]["FieldProvenance"]["properties"]["input_sha256"]["enum"] = sorted(
            set(sources)
        )
    return schema


def export(output: Path, *, check: bool = False) -> tuple[str, ...]:
    from .design_grounding_v2 import GroundingValuesV2

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
    models.append(GroundingValuesV2)
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
    files["GroundingValuesV2.codex.json"] = (
        json.dumps(structured_output_schema(GroundingValuesV2), indent=2, sort_keys=True)
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

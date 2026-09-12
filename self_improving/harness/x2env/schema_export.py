"""Generate schema snapshots from the sole editable typed contract source."""

import json
from pathlib import Path

from . import contracts


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

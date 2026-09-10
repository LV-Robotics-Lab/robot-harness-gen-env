"""Stable lazy interface for extensible public Harness schema snapshots."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path

from .schemas.base import HarnessModel

DEFAULT_SCHEMA_ROOT = Path(__file__).with_name("json_schemas")


class _LazySchemaModels(Mapping[str, type[HarnessModel]]):
    """Mapping-compatible facade whose extensible directory loads only on demand."""

    def __getitem__(self, schema_id: str) -> type[HarnessModel]:
        from ._schema_catalog_models import schema_model as resolve

        return resolve(schema_id, __package__)

    def __iter__(self) -> Iterator[str]:
        from ._schema_catalog_models import schema_ids

        return iter(schema_ids())

    def __len__(self) -> int:
        from ._schema_catalog_models import schema_ids

        return len(schema_ids())

    def copy(self) -> dict[str, type[HarnessModel]]:
        return dict(self.items())

    def __or__(self, other: object) -> dict[str, type[HarnessModel]]:
        return self.copy() | other  # type: ignore[operator]

    def __ror__(self, other: object) -> dict[str, type[HarnessModel]]:
        return other | self.copy()  # type: ignore[operator]


SCHEMA_MODELS: Mapping[str, type[HarnessModel]] = _LazySchemaModels()


class SchemaSnapshotMismatch(RuntimeError):
    """Raised when committed JSON Schema snapshots differ from the models."""

    def __init__(self, problems: tuple[str, ...]):
        self.problems = problems
        super().__init__("schema snapshot mismatch: " + "; ".join(problems))


def schema_model(schema_id: str) -> type[HarnessModel]:
    """Resolve qualified Text2Env schemas without loading the global directory."""

    from .schemas._qualified_exports import schema_model as resolve_qualified

    try:
        return resolve_qualified(schema_id, f"{__package__}.schemas")
    except KeyError:
        return SCHEMA_MODELS[schema_id]


def schema_documents() -> dict[str, dict[str, object]]:
    """Build every public JSON Schema document in stable identifier order."""

    return {
        schema_id: _make_schema_document_standalone(
            SCHEMA_MODELS[schema_id].model_json_schema(mode="validation")
        )
        for schema_id in sorted(SCHEMA_MODELS)
    }


def _make_schema_document_standalone(document: dict[str, object]) -> dict[str, object]:
    """Keep resource identifiers only on the root of a bundled schema document."""

    definitions = document.get("$defs")
    pending = list(definitions.values()) if isinstance(definitions, dict) else []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            value.pop("$id", None)
            value.pop("$schema", None)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return document


def schema_snapshot_name(schema_id: str) -> str:
    return f"{schema_id}.schema.json"


def render_schema_document(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def expected_schema_snapshots() -> dict[str, str]:
    return {
        schema_snapshot_name(schema_id): render_schema_document(document)
        for schema_id, document in schema_documents().items()
    }


def export_schema_snapshots(
    root: Path = DEFAULT_SCHEMA_ROOT,
    *,
    check: bool = False,
) -> tuple[Path, ...]:
    """Write snapshots or fail if an existing snapshot directory has drifted."""

    expected = expected_schema_snapshots()
    paths = tuple(root / name for name in sorted(expected))
    if check:
        existing_names = {path.name for path in root.glob("*.schema.json")}
        expected_names = set(expected)
        problems = [f"missing {name}" for name in sorted(expected_names - existing_names)]
        problems.extend(f"unexpected {name}" for name in sorted(existing_names - expected_names))
        for name in sorted(expected_names & existing_names):
            if (root / name).read_text(encoding="utf-8") != expected[name]:
                problems.append(f"changed {name}")
        if problems:
            raise SchemaSnapshotMismatch(tuple(problems))
        return paths

    root.mkdir(parents=True, exist_ok=True)
    for path in paths:
        path.write_text(expected[path.name], encoding="utf-8")
    return paths

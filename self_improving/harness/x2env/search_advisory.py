"""One managed lexical-query suggestion, never category mutation or asset selection."""

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .contracts import ArtifactRef, Model, SceneIR


class ArtifactStore(Protocol):
    def read_artifact(self, ref: ArtifactRef) -> bytes: ...
    def write_artifact(self, data: bytes, media_type: str) -> ArtifactRef: ...


class SearchQuery(Model):
    query: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def lexical_only(self):
        if (
            self.query != self.query.strip()
            or not re.fullmatch(r"[\w -]+", self.query)
            or not any(c.isalnum() for c in self.query)
        ):
            raise ValueError("invalid_lexical_search_query")
        return self


class SearchAdvisoryResult(Model):
    status: Literal["completed", "failed", "blocked"]
    original_category: str
    query: str | None
    receipt: ArtifactRef
    error_code: str | None = None


def plan_search(backend, scene_ir, entity, *, store: ArtifactStore, output_root, timeout=600):
    """Caller supplies remaining budget; one existing managed backend invocation at most."""
    root = Path(output_root)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("invalid search advisory timeout")
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("search advisory root must be new absolute nonsymbolic directory")
    root.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    evidence = []
    status, error, proposal = "failed", None, None

    def record(name, data, media_type="application/json"):
        (root / name).write_bytes(data)
        ref = store.write_artifact(data, media_type)
        evidence.append(ref)
        return ref

    try:
        scene = SceneIR.model_validate_json(store.read_artifact(scene_ir))
        if entity not in scene.entities or entity.role != "foreground":
            raise ValueError("unbound_search_entity")
        if backend is None:
            raise FileNotFoundError("managed_codex_search_backend")
        if backend.store is not store:
            raise ValueError("search_store_mismatch")
        if (
            not backend.executable.is_absolute()
            or hashlib.sha256(backend.executable.read_bytes()).hexdigest() != backend.executable_sha
        ):
            raise ValueError("executable_identity_mismatch")
        context = {
            "scene_ir": scene_ir.model_dump(),
            "entity": entity.model_dump(mode="json"),
            "original_category": entity.category,
            "provider_semantics": (
                "one lexical token/substring query over asset names and source paths; "
                "no automatic synonym expansion; candidates require independent license, "
                "geometry and visual checks"
            ),
        }
        record("context.json", json.dumps(context, sort_keys=True).encode())
        prompt = (
            "You advise the Harness on one broad, concise lexical search query "
            "for the requested entity. Use ordinary searchable object terms, "
            "optionally a general synonym supported by the intent. "
            "Preserve the original entity/category; "
            "the query is not a replacement SceneIR category. "
            "Return one query using letters, digits, spaces, underscores or hyphens only, "
            "and a short reason. Do not return a URL, filename, selected asset, license, "
            "qualification or execution receipt. Do not use tools. There is one search call, "
            "no iterative alternate-query retries. Context:\n"
            + json.dumps(context, ensure_ascii=False)
        )
        raw = backend._invoke(root, prompt, [], SearchQuery, record, timeout, start)
        proposal = SearchQuery.model_validate_json(raw)
        status = "completed"
    except FileNotFoundError as exc:
        status, error = "blocked", "blocked_external_resource"
        record("error.json", json.dumps({"reason": str(exc)}).encode())
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        known = {
            "unbound_search_entity",
            "search_store_mismatch",
            "executable_identity_mismatch",
            "model_timeout",
            "model_exit_failure",
            "model_incomplete_turn",
            "advisory_tool_violation",
        }
        error = str(exc) if str(exc) in known else "invalid_search_advisory"
        record("error.json", json.dumps({"reason": str(exc)}).encode())
    receipt = record(
        "result.json",
        json.dumps(
            {
                "schema_version": "x2env.search_advisory.v1",
                "status": status,
                "error_code": error,
                "authority": "search_query_advisory_only",
                "scene_ir": scene_ir.model_dump(),
                "entity": entity.model_dump(mode="json"),
                "original_category": entity.category,
                "query": proposal.query if proposal else None,
                "proposal": proposal.model_dump() if proposal else None,
                "model": backend.model if backend else None,
                "executable_sha256": backend.executable_sha if backend else None,
                "external_agent_executed": (root / "process.json").is_file(),
                "evidence": [r.model_dump() for r in evidence],
                "wall_seconds": time.monotonic() - start,
            },
            sort_keys=True,
        ).encode(),
    )
    return SearchAdvisoryResult(
        status=status,
        original_category=entity.category,
        query=proposal.query if proposal else None,
        receipt=receipt,
        error_code=error,
    )

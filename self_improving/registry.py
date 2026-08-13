"""First-party registry and checkout audit for the consolidated platform."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModuleSpec:
    """A source module owned by this repository or referenced as a submodule."""

    name: str
    path: str
    layer: str
    kind: str = "first_party"
    required: bool = True
    mutable: bool = True


MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec("gen_env_core", "scene_gen", "core"),
    ModuleSpec("stage5", "self_improving/stage5", "orchestration"),
    ModuleSpec("alchedata", "self_improving/alchedata", "closed_loop"),
    ModuleSpec("asset_pipeline", "self_improving/asset_pipeline/active", "assets"),
    ModuleSpec(
        "agenticsim_runtime",
        "self_improving/sim_adapters/agenticsim_runtime",
        "sim_adapter",
    ),
    ModuleSpec("yeyuxuan_onboarding", "self_improving/onboarding/yeyuxuan", "provenance"),
    ModuleSpec(
        "stage04_archive",
        "self_improving/legacy/stage04",
        "archive",
        required=False,
        mutable=False,
    ),
    ModuleSpec(
        "openreal2sim",
        "external/OpenReal2Sim",
        "external",
        kind="submodule",
    ),
    ModuleSpec(
        "digital_cousins",
        "external/digital-cousins",
        "external",
        kind="submodule",
    ),
)


def audit_repository(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Return a machine-readable audit without importing migrated runtimes."""

    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    records: list[dict[str, Any]] = []
    for module in MODULES:
        target = root / module.path
        exists = target.is_dir()
        initialized = exists
        if module.kind == "submodule":
            initialized = exists and (target / ".git").exists()
        status = "ready" if initialized else ("uninitialized" if exists else "missing")
        records.append({**asdict(module), "status": status})

    required_failures = [
        record["name"]
        for record in records
        if record["required"] and record["status"] != "ready"
    ]
    return {
        "schema_version": "robot_harness.self_improving_audit.v1",
        "repo_root": str(root.resolve()),
        "ready": not required_failures,
        "required_failures": required_failures,
        "modules": records,
    }

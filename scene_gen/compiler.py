"""Importable Text2Env compiler with explicit outcomes and progress callbacks."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from .asset_generator import ensure_assets_for_scene
from .builder import build_scene_package
from .catalog import AssetCatalog, load_catalog
from .parser import parse_rule_based
from .schema import ResolvedSceneSpec, SceneSpec, SceneSpecError
from .solver import SceneSolveError, solve_scene
from .validator import validate_resolved_scene


@dataclass(frozen=True)
class CompileRequest:
    """All explicit inputs needed for one deterministic compile."""

    request: str
    seed: int
    asset_catalog_path: Path
    out_root: Path
    generate_missing_assets: bool = False
    generated_objects_root: Path | None = None


@dataclass(frozen=True)
class CompileEvent:
    """A callback emitted exactly when an actual compiler stage starts or completes."""

    stage: str
    phase: Literal["started", "completed"]
    artifact_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class CompileOutcome:
    """Typed in-process result of parse, solve, package, and static validation."""

    scene_spec: SceneSpec
    resolved_scene: ResolvedSceneSpec
    asset_catalog: AssetCatalog
    output_dir: Path
    manifest: dict[str, Any]
    static_validation: dict[str, Any]
    asset_generation_report: dict[str, Any] | None
    asset_admission_report: dict[str, Any] | None


CompileObserver = Callable[[CompileEvent], None]


class AssetAdmitter(Protocol):
    """Optional platform Adapter that promotes generated assets before solve."""

    def admit(
        self,
        *,
        scene_spec: SceneSpec,
        asset_catalog: AssetCatalog,
        generation_report: dict[str, Any],
    ) -> tuple[AssetCatalog, dict[str, Any]]: ...


class CompileFailure(RuntimeError):
    """An expected, typed compiler refusal that a Harness Adapter can map."""

    def __init__(
        self,
        *,
        code: str,
        stage: str,
        message: str,
        details: dict[str, Any],
    ) -> None:
        self.code = code
        self.stage = stage
        self.details = details
        super().__init__(message)


def compile_scene(
    request: CompileRequest,
    *,
    observer: CompileObserver | None = None,
    asset_admitter: AssetAdmitter | None = None,
) -> CompileOutcome:
    """Compile once without CLI parsing, output discovery, or prompt reparsing."""

    notify = observer or (lambda event: None)
    notify(CompileEvent(stage="parse", phase="started"))
    try:
        spec = parse_rule_based(request.request, seed=request.seed)
    except (SceneSpecError, ValidationError) as error:
        details = (
            {"validation_errors": error.errors()}
            if isinstance(error, ValidationError)
            else {"error": str(error)}
        )
        raise CompileFailure(
            code="T2E_REQUEST_REJECTED",
            stage="parse",
            message=str(error),
            details=details,
        ) from error
    notify(CompileEvent(stage="parse", phase="completed"))

    notify(CompileEvent(stage="catalog", phase="started"))
    try:
        catalog = load_catalog(request.asset_catalog_path)
    except (OSError, ValueError) as error:
        raise CompileFailure(
            code="T2E_CATALOG_INVALID",
            stage="catalog",
            message=str(error),
            details={
                "catalog_path": str(request.asset_catalog_path.expanduser().resolve()),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        ) from error
    notify(
        CompileEvent(
            stage="catalog",
            phase="completed",
            artifact_paths=(request.asset_catalog_path.expanduser().resolve(),),
        )
    )

    output_dir = (request.out_root / spec.scene_id).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    generation_report: dict[str, Any] | None = None
    admission_report: dict[str, Any] | None = None
    if request.generate_missing_assets:
        notify(CompileEvent(stage="asset_generation", phase="started"))
        try:
            catalog, generation_report = ensure_assets_for_scene(
                spec,
                catalog,
                objects_root=request.generated_objects_root,
            )
        except SceneSpecError as error:
            raise CompileFailure(
                code="T2E_ASSET_UNAVAILABLE",
                stage="asset_generation",
                message=str(error),
                details={"scene_id": spec.scene_id, "error": str(error)},
            ) from error
        generation_path = output_dir / "asset_generation_report.json"
        _write_json(generation_path, generation_report)
        notify(
            CompileEvent(
                stage="asset_generation",
                phase="completed",
                artifact_paths=(generation_path,),
            )
        )
        if asset_admitter is not None:
            notify(CompileEvent(stage="asset_admission", phase="started"))
            catalog, admission_report = asset_admitter.admit(
                scene_spec=spec,
                asset_catalog=catalog,
                generation_report=generation_report,
            )
        catalog_path = output_dir / "effective_asset_catalog.json"
        _write_json(catalog_path, catalog.canonical_dict())
        if admission_report is not None:
            admission_path = output_dir / "asset_admission_report.json"
            _write_json(admission_path, admission_report)
            notify(
                CompileEvent(
                    stage="asset_admission",
                    phase="completed",
                    artifact_paths=(admission_path, catalog_path),
                )
            )

    notify(CompileEvent(stage="solve", phase="started"))
    try:
        resolved = solve_scene(spec, catalog)
    except SceneSpecError as error:
        raise CompileFailure(
            code="T2E_ASSET_UNAVAILABLE",
            stage="solve",
            message=str(error),
            details={"scene_id": spec.scene_id, "error": str(error)},
        ) from error
    except SceneSolveError as error:
        raise CompileFailure(
            code="T2E_SOLVER_EXHAUSTED",
            stage="solve",
            message=str(error),
            details=error.report,
        ) from error
    notify(CompileEvent(stage="solve", phase="completed"))

    notify(CompileEvent(stage="package", phase="started"))
    manifest = build_scene_package(spec, resolved, output_dir)
    notify(
        CompileEvent(
            stage="package",
            phase="completed",
            artifact_paths=(output_dir / "package_manifest.json",),
        )
    )

    notify(CompileEvent(stage="static_validation", phase="started"))
    report = validate_resolved_scene(
        resolved,
        catalog=catalog,
        package_root=output_dir,
        require_runtime=False,
    )
    validation_path = output_dir / "validation_report.json"
    _write_json(validation_path, report)
    notify(
        CompileEvent(
            stage="static_validation",
            phase="completed",
            artifact_paths=(validation_path,),
        )
    )
    return CompileOutcome(
        scene_spec=spec,
        resolved_scene=resolved,
        asset_catalog=catalog,
        output_dir=output_dir,
        manifest=manifest,
        static_validation=report,
        asset_generation_report=generation_report,
        asset_admission_report=admission_report,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

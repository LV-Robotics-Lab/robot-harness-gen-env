"""Harness Adapter for the deterministic ``text2env.compile`` Module."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from scene_gen import (
    CompileEvent,
    CompileFailure,
    CompileOutcome,
    CompileRequest,
    compile_scene,
)
from scene_gen.builder import verify_package
from scene_gen.catalog import AssetCatalog, load_catalog

from ..artifacts import LocalArtifactStore
from ..assets import AssetAdmissionError, GeneratedAssetAdmitter
from ..registry import HandlerResult, RunContext, SkillBlocked
from ..schemas import (
    ArtifactRef,
    Blocker,
    EnvironmentPackage,
    SkillDescriptor,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
)


@dataclass(frozen=True)
class Text2EnvCompileHandler:
    """Execute one compile in an isolated run root and publish only CAS artifacts."""

    artifact_store: LocalArtifactStore
    work_root: Path
    generated_staging_root: Path
    asset_library_root: Path
    admission_date: date
    allowed_asset_roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        if not self.allowed_asset_roots:
            raise ValueError("allowed_asset_roots must not be empty")

    def __call__(
        self,
        value: Text2EnvCompileInput,
        context: RunContext,
    ) -> HandlerResult:
        run_root = self.work_root.expanduser().resolve() / str(context.run_id)
        run_root.mkdir(parents=True, exist_ok=False)
        collector = _ArtifactCollector(
            store=self.artifact_store,
            input_catalog=value.asset_catalog,
            input_catalog_path=self.artifact_store.resolve(value.asset_catalog).path,
        )
        try:
            _validate_catalog_trust(
                load_catalog(collector.input_catalog_path),
                allowed_roots=self.allowed_asset_roots,
            )
        except (OSError, ValueError) as error:
            raise SkillBlocked(
                Blocker(
                    code="HARN_DEPENDENCY_UNAVAILABLE",
                    message="asset catalog failed schema, path, or availability checks",
                    stage="catalog",
                    retryable=False,
                    details={
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    unknowns=(),
                    artifact_refs=collector.artifacts,
                )
            ) from error
        context.emit("compile.started")

        def observe(event: CompileEvent) -> None:
            refs = collector.capture_event(event)
            context.emit(
                f"compile.{event.stage}.{event.phase}",
                artifact_refs=refs,
            )

        try:
            outcome = compile_scene(
                CompileRequest(
                    request=value.request,
                    seed=value.seed,
                    asset_catalog_path=collector.input_catalog_path,
                    out_root=run_root,
                    generate_missing_assets=value.config.generate_missing_assets,
                    generated_objects_root=(
                        self.generated_staging_root.expanduser().resolve()
                        / str(context.run_id)
                    ),
                ),
                observer=observe,
                asset_admitter=GeneratedAssetAdmitter(
                    library_root=self.asset_library_root.expanduser().resolve(),
                    admission_date=self.admission_date,
                ),
            )
        except CompileFailure as error:
            blocker_code = (
                "HARN_DEPENDENCY_UNAVAILABLE"
                if error.code == "T2E_CATALOG_INVALID"
                else error.code
            )
            raise SkillBlocked(
                Blocker(
                    code=blocker_code,
                    message=str(error),
                    stage=error.stage,
                    retryable=False,
                    details=_json_object(error.details),
                    unknowns=(),
                    artifact_refs=collector.artifacts,
                )
            ) from error
        except AssetAdmissionError as error:
            raise SkillBlocked(
                Blocker(
                    code="T2E_ASSET_UNAVAILABLE",
                    message=str(error),
                    stage="asset_admission",
                    retryable=False,
                    details={"error_type": type(error).__name__},
                    unknowns=(),
                    artifact_refs=collector.artifacts,
                )
            ) from error

        canonical_catalog_path = outcome.output_dir / "effective_asset_catalog.canonical.json"
        canonical_catalog_path.write_text(
            json.dumps(
                outcome.asset_catalog.canonical_dict(),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        catalog_ref = collector.capture(canonical_catalog_path, name="effective_asset_catalog")
        if catalog_ref.sha256 != outcome.asset_catalog.digest():
            raise RuntimeError("canonical catalog bytes do not match AssetCatalog.digest")

        scene_spec_ref = collector.capture(outcome.output_dir / "scene_spec.json")
        resolved_ref = collector.capture(outcome.output_dir / "resolved_scene.json")
        manifest_ref = collector.capture(outcome.output_dir / "package_manifest.json")
        validation_ref = collector.capture(outcome.output_dir / "validation_report.json")
        package_problems = _package_binding_problems(outcome)
        if package_problems:
            raise SkillBlocked(
                Blocker(
                    code="T2E_PACKAGE_INVALID",
                    message="compiled package failed hash-binding verification",
                    stage="package",
                    retryable=False,
                    details={"problems": package_problems},
                    unknowns=(),
                    artifact_refs=collector.artifacts,
                )
            )

        environment_package = EnvironmentPackage(
            package_id=outcome.resolved_scene.digest(),
            route_id="text2env",
            producer_skill_ref="text2env.compile@1.0.0",
            seed=value.seed,
            scene_spec_sha256=outcome.scene_spec.digest(),
            resolved_scene_sha256=outcome.resolved_scene.digest(),
            asset_catalog=catalog_ref,
            package_manifest=manifest_ref,
        )
        output = Text2EnvCompileOutput(
            scene_spec=scene_spec_ref,
            resolved_scene=resolved_ref,
            environment_package=environment_package,
            static_validation=validation_ref,
        )
        output_refs = (
            scene_spec_ref,
            resolved_ref,
            catalog_ref,
            manifest_ref,
            validation_ref,
        )
        context.emit("compile.completed", artifact_refs=output_refs)
        return HandlerResult(output=output, artifacts=collector.artifacts)


def text2env_compile_descriptor(
    *,
    qualification_artifact: ArtifactRef,
    implementation_sha256: str,
) -> SkillDescriptor:
    """Build the one exact immutable descriptor for this qualified Adapter."""

    return SkillDescriptor(
        skill_id="text2env.compile",
        version="1.0.0",
        mcp_tool_name="text2env_compile_v1_0_0",
        input_schema="harness.text2env_compile_input.v1",
        output_schema="harness.text2env_compile_output.v1",
        implementation_name="self_improving.harness.handlers.text2env_compile",
        implementation_version="1",
        implementation_sha256=implementation_sha256,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification_artifact,
    )


@dataclass
class _ArtifactCollector:
    store: LocalArtifactStore
    input_catalog: ArtifactRef
    input_catalog_path: Path

    def __post_init__(self) -> None:
        snapshot = self.store.put_file(
            self.input_catalog_path,
            name="input_asset_catalog",
            media_type="application/json",
            schema_version=self.input_catalog.schema_version,
        )
        if snapshot.sha256 != self.input_catalog.sha256:
            raise RuntimeError("input catalog CAS snapshot changed content identity")
        self._by_path: dict[Path, ArtifactRef] = {
            self.input_catalog_path.resolve(): snapshot
        }
        self._ordered: list[ArtifactRef] = [snapshot]

    @property
    def artifacts(self) -> tuple[ArtifactRef, ...]:
        return tuple(self._ordered)

    def capture_event(self, event: CompileEvent) -> tuple[ArtifactRef, ...]:
        if event.phase != "completed":
            return ()
        paths = event.artifact_paths
        if event.stage == "package" and paths:
            manifest_path = paths[0]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            package_files = tuple(
                manifest_path.parent / record["path"] for record in manifest["files"]
            )
            paths = (*package_files, *paths)
        return tuple(self.capture(path) for path in paths)

    def capture(self, path: Path, *, name: str | None = None) -> ArtifactRef:
        resolved = path.expanduser().resolve()
        existing = self._by_path.get(resolved)
        if existing is not None:
            return existing
        media_type, schema_version = _artifact_type(resolved)
        artifact = self.store.put_file(
            resolved,
            name=name or resolved.stem,
            media_type=media_type,
            schema_version=schema_version,
        )
        self._by_path[resolved] = artifact
        self._ordered.append(artifact)
        return artifact


def _artifact_type(path: Path) -> tuple[str, str | None]:
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        schema_version = payload.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise ValueError(f"JSON artifact has no schema_version: {path}")
        return "application/json", schema_version
    if path.suffix == ".py":
        return "text/x-python", None
    if path.suffix == ".txt":
        return "text/plain", None
    return "application/octet-stream", None


def _json_object(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, default=str))


class _CatalogTrustError(ValueError):
    pass


def _validate_catalog_trust(
    catalog: AssetCatalog,
    *,
    allowed_roots: tuple[Path, ...],
) -> None:
    roots = tuple(root.expanduser().resolve() for root in allowed_roots)
    _trusted_path(Path(catalog.objects_root), roots, require="optional")
    for entry in catalog.entries:
        _trusted_path(
            Path(entry.asset_path),
            roots,
            require="directory" if entry.available else "optional",
        )
        for model in entry.models:
            declared = {
                "model_path": model.model_path,
                "metadata_path": model.metadata_path,
                "visual_path": model.visual_path,
                "collision_path": model.collision_path,
                "urdf_path": model.urdf_path,
            }
            trusted = {
                name: _trusted_path(Path(path), roots, require="optional")
                for name, path in declared.items()
                if path is not None
            }
            if not model.usable:
                continue
            if "model_path" not in trusted or not trusted["model_path"].exists():
                raise _CatalogTrustError(
                    f"usable model has no existing model_path: {entry.asset_id}:{model.model_id}"
                )
            required_files = (
                ("urdf_path",)
                if entry.load_type == "urdf"
                else ("metadata_path", "visual_path", "collision_path")
            )
            missing = [
                name
                for name in required_files
                if name not in trusted or not trusted[name].is_file()
            ]
            if missing:
                raise _CatalogTrustError(
                    f"usable model files missing for {entry.asset_id}:{model.model_id}: {missing}"
                )


def _trusted_path(
    path: Path,
    roots: tuple[Path, ...],
    *,
    require: str,
) -> Path:
    resolved = path.expanduser().resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise _CatalogTrustError(f"asset path escapes configured roots: {resolved}")
    if require == "directory" and not resolved.is_dir():
        raise _CatalogTrustError(f"required asset directory is missing: {resolved}")
    return resolved


def _package_binding_problems(outcome: CompileOutcome) -> list[str]:
    problems: list[str] = []
    expected = {
        "source_scene_spec_sha256": outcome.scene_spec.digest(),
        "resolved_scene_sha256": outcome.resolved_scene.digest(),
        "asset_catalog_sha256": outcome.asset_catalog.digest(),
    }
    for field, digest in expected.items():
        if outcome.manifest.get(field) != digest:
            problems.append(f"manifest.{field}")
    if outcome.static_validation.get("resolved_scene_sha256") != outcome.resolved_scene.digest():
        problems.append("static_validation.resolved_scene_sha256")
    if verify_package(outcome.output_dir)["status"] != "pass":
        problems.append("package_verification")
    return problems

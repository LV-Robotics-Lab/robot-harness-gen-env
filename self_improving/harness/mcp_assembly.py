"""Restartable production assemblies for exact Golden Workflow MCP tools."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .application import CompileApplicationSettings, create_compile_application
from .artifacts import LocalArtifactStore
from .golden_compile_execution import create_text2env_compile_execution_binding
from .golden_execution import create_golden_execution_catalog
from .golden_replay_execution import create_text2env_replay_execution_binding
from .golden_run import GoldenRunHarness
from .golden_store import SQLiteGoldenRunStore
from .golden_validate_execution import create_text2env_validate_execution_binding
from .mcp_adapter import (
    HarnessMCPAdapter,
    MCPWorkflowSkillInputProvider,
    create_harness_mcp_adapter,
)
from .replay_application import ReplayApplicationSettings, create_replay_application
from .schemas.common import ArtifactRef
from .validate_application import ValidateApplicationSettings, create_validate_application
from .validate_input_builder import Text2EnvValidateV2InputBuilder


class CompileMCPServerSettings(BaseModel):
    """Operator-owned local roots and the one frozen MCP authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["harness.compile_mcp_server_settings.v1"]
    principal_id: str
    registry_snapshot: ArtifactRef
    compile_state_root: Path
    external_catalog_roots: tuple[Path, ...]
    allowed_asset_roots: tuple[Path, ...]
    admission_date: date
    asset_library_root: Path
    golden_database_path: Path


class GoldenMCPServerSettings(CompileMCPServerSettings):
    """Operator-owned roots for the exact compile/replay/validate catalog."""

    schema_version: Literal["harness.golden_mcp_server_settings.v1"]
    qualification_cas_roots: tuple[Path, ...]
    replay_state_root: Path
    replay_qualification_bundle_root: Path
    validate_state_root: Path
    validate_qualification_bundle_root: Path
    implementation_root: Path
    scene_gen_root: Path
    ledger_contract_root: Path
    interpreter: Path
    runtime_runner: Path
    runtime_module_root: Path
    runtime_capability_path: Path
    media_launcher: Path
    static_ffmpeg: Path
    delegated_cgroup_root: Path
    runtime_timeout_seconds: float
    capability_timeout_seconds: float


def create_compile_mcp_adapter(settings: CompileMCPServerSettings) -> HarnessMCPAdapter:
    """Reopen every durable authority and bind the exact compile application."""

    if type(settings) is not CompileMCPServerSettings:
        raise TypeError("settings must be the exact CompileMCPServerSettings")
    if not settings.principal_id:
        raise ValueError("principal_id must be a non-empty exact string")
    application = create_compile_application(
        CompileApplicationSettings(
            state_root=settings.compile_state_root,
            external_catalog_roots=settings.external_catalog_roots,
            allowed_asset_roots=settings.allowed_asset_roots,
            admission_date=settings.admission_date,
            asset_library_root=settings.asset_library_root,
        )
    )
    store = LocalArtifactStore(application.artifact_root)
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(settings.golden_database_path),
        execution_catalog=create_golden_execution_catalog(
            bindings=(create_text2env_compile_execution_binding(application=application),)
        ),
    )
    return create_harness_mcp_adapter(
        harness=harness,
        principal_id=settings.principal_id,
        registry_snapshot=settings.registry_snapshot,
    )


def create_golden_mcp_adapter(settings: GoldenMCPServerSettings) -> HarnessMCPAdapter:
    """Reopen the three exact applications over one CAS and workflow authority."""

    if type(settings) is not GoldenMCPServerSettings:
        raise TypeError("settings must be the exact GoldenMCPServerSettings")
    if not settings.principal_id:
        raise ValueError("principal_id must be a non-empty exact string")
    compile_application = create_compile_application(
        CompileApplicationSettings(
            state_root=settings.compile_state_root,
            external_catalog_roots=settings.external_catalog_roots,
            allowed_asset_roots=settings.allowed_asset_roots,
            admission_date=settings.admission_date,
            asset_library_root=settings.asset_library_root,
        )
    )
    store = LocalArtifactStore(compile_application.artifact_root)
    _import_qualification_cas(
        artifact_store=store,
        roots=settings.qualification_cas_roots,
    )
    replay_application = create_replay_application(
        ReplayApplicationSettings(
            state_root=settings.replay_state_root,
            qualification_bundle_root=settings.replay_qualification_bundle_root,
            evidence_artifact_root=store.root,
            implementation_root=settings.implementation_root,
            scene_gen_root=settings.scene_gen_root,
            ledger_contract_root=settings.ledger_contract_root,
            allowed_asset_roots=settings.allowed_asset_roots,
            interpreter=settings.interpreter,
            runtime_runner=settings.runtime_runner,
            runtime_module_root=settings.runtime_module_root,
            runtime_capability_path=settings.runtime_capability_path,
            media_launcher=settings.media_launcher,
            static_ffmpeg=settings.static_ffmpeg,
            delegated_cgroup_root=settings.delegated_cgroup_root,
            runtime_timeout_seconds=settings.runtime_timeout_seconds,
            capability_timeout_seconds=settings.capability_timeout_seconds,
        )
    )
    validate_application = create_validate_application(
        ValidateApplicationSettings(
            state_root=settings.validate_state_root,
            evidence_artifact_root=store.root,
            qualification_bundle_root=settings.validate_qualification_bundle_root,
            implementation_root=settings.implementation_root,
            scene_gen_root=settings.scene_gen_root,
            ledger_contract_root=settings.ledger_contract_root,
        )
    )
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(settings.golden_database_path),
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                create_text2env_compile_execution_binding(application=compile_application),
                create_text2env_replay_execution_binding(application=replay_application),
                create_text2env_validate_execution_binding(application=validate_application),
            )
        ),
    )
    validate_input_builder = Text2EnvValidateV2InputBuilder(
        artifact_store=store,
        compile_application=compile_application,
        replay_application=replay_application,
    )
    return create_harness_mcp_adapter(
        harness=harness,
        principal_id=settings.principal_id,
        registry_snapshot=settings.registry_snapshot,
        skill_input_providers=(
            MCPWorkflowSkillInputProvider(
                skill_ref="text2env.validate@2.0.0",
                build=validate_input_builder.build,
            ),
        ),
    )


def _import_qualification_cas(
    *,
    artifact_store: LocalArtifactStore,
    roots: tuple[Path, ...],
) -> None:
    if not roots:
        raise ValueError("qualification_cas_roots must not be empty")
    for value in roots:
        source = value.expanduser().resolve(strict=True)
        digest_root = source / "sha256"
        if not digest_root.is_dir():
            raise ValueError(f"qualification CAS has no sha256 directory: {source}")
        members = tuple(sorted(digest_root.glob("*/*")))
        if not members:
            raise ValueError(f"qualification CAS is empty: {source}")
        for path in members:
            expected = path.name
            if (
                path.is_symlink()
                or not path.is_file()
                or len(expected) != 64
                or path.parent.name != expected[:2]
            ):
                raise ValueError(f"qualification CAS has an invalid member: {path}")
            imported = artifact_store.put_file(
                path,
                name=f"qualification_{expected}",
                media_type="application/octet-stream",
                schema_version=None,
            )
            if imported.sha256 != expected:
                raise ValueError(f"qualification CAS member digest mismatch: {path}")


__all__ = [
    "CompileMCPServerSettings",
    "GoldenMCPServerSettings",
    "create_compile_mcp_adapter",
    "create_golden_mcp_adapter",
]

"""Restartable production assembly for the first exact MCP compile tool."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .application import CompileApplicationSettings, create_compile_application
from .artifacts import LocalArtifactStore
from .golden_compile_execution import create_text2env_compile_execution_binding
from .golden_execution import create_golden_execution_catalog
from .golden_run import GoldenRunHarness
from .golden_store import SQLiteGoldenRunStore
from .mcp_adapter import HarnessMCPAdapter, create_harness_mcp_adapter
from .schemas.common import ArtifactRef


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


__all__ = ["CompileMCPServerSettings", "create_compile_mcp_adapter"]

"""Production assembly for the one qualified ``text2env.compile`` Skill.

This module is deliberately narrower than the Registry.  Callers may choose
state and trust roots, then submit compile requests; they cannot inject a
descriptor, handler, or qualification receipt through the public factory.

The assembly records the Invocation before the first event, appends the event
stream, and only then stores the terminal ``RunState`` in the same SQLite
authority.  A restarted facade can therefore recover both intent and outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import ClassVar, Literal
from uuid import UUID, uuid4

from .artifacts import LocalArtifactStore
from .event_journal import EventPage, SQLiteEventJournal
from .handlers.text2env_compile import (
    Text2EnvCompileHandler,
    text2env_compile_descriptor,
)
from .handlers.text2env_compile_dependencies import (
    Text2EnvCompileDependencyResolver,
)
from .qualification import load_qualification_bundle
from .registry import SkillRegistry
from .run_store import SQLiteRunStore
from .schemas import ArtifactRef, Invocation, RunState, SkillDescriptor

_DISTRIBUTION_ROOT = Path(__file__).resolve().parents[2]
_COMPILE_QUALIFICATION_ROOT = (
    Path(__file__).resolve().parent / "qualified_skills" / "text2env.compile" / "1.0.0"
)
_IMPLEMENTATION_ROOT = _DISTRIBUTION_ROOT
_SCENE_GEN_ROOT = _DISTRIBUTION_ROOT / "scene_gen"
_LEDGER_CONTRACT_ROOT = (
    _DISTRIBUTION_ROOT / "self_improving" / "asset_pipeline" / "active" / "1_asset_reuse" / "lib"
)
_COMPILE_SKILL_ID = "text2env.compile"
_COMPILE_VERSION = "1.0.0"
_ASSET_CATALOG_SCHEMA = "robotwin.asset_catalog.v1"


@dataclass(frozen=True)
class CompileApplicationSettings:
    """Filesystem and trust policy chosen by the application operator."""

    state_root: Path
    external_catalog_roots: tuple[Path, ...]
    allowed_asset_roots: tuple[Path, ...]
    admission_date: date


class CompileApplicationConfigurationError(ValueError):
    """The application cannot establish the requested local trust boundary."""


class ExternalCatalogError(ValueError):
    """An external catalog is missing, not a file, or outside configured roots."""


class CompileApplication:
    """A fixed-Skill facade over CAS, qualification, Registry, and event journal."""

    durable_run_state: ClassVar[Literal[True]] = True

    def __init__(
        self,
        *,
        state_root: Path,
        artifact_store: LocalArtifactStore,
        event_journal: SQLiteEventJournal,
        run_store: SQLiteRunStore,
        registry: SkillRegistry,
        external_catalog_roots: tuple[Path, ...],
    ) -> None:
        self._state_root = state_root
        self._artifact_store = artifact_store
        self._event_journal = event_journal
        self._run_store = run_store
        self._registry = registry
        self._external_catalog_roots = external_catalog_roots

    @property
    def skills(self) -> tuple[SkillDescriptor, ...]:
        """Return the sole immutable descriptor admitted by this assembly."""

        return self._registry.list()

    @property
    def artifact_root(self) -> Path:
        return self._artifact_store.root

    @property
    def journal_path(self) -> Path:
        return self._event_journal.path.resolve()

    def snapshot_asset_catalog(self, source: Path) -> ArtifactRef:
        """Freeze one explicitly trusted external catalog into application CAS."""

        candidate = Path(source).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise ExternalCatalogError(f"external catalog is missing: {candidate}") from error
        if not resolved.is_file():
            raise ExternalCatalogError(f"external catalog must be a regular file: {resolved}")
        if not any(resolved.is_relative_to(root) for root in self._external_catalog_roots):
            raise ExternalCatalogError(
                f"external catalog is outside every trusted root: {resolved}"
            )
        return self._artifact_store.put_file(
            resolved,
            name="input_asset_catalog",
            media_type="application/json",
            schema_version=_ASSET_CATALOG_SCHEMA,
        )

    def compile(
        self,
        *,
        request: str,
        seed: int,
        asset_catalog_path: Path,
        generate_missing_assets: bool = False,
    ) -> RunState:
        """Snapshot external input and invoke the exact qualified compile Skill."""

        catalog = self.snapshot_asset_catalog(asset_catalog_path)
        return self._registry.invoke(
            _COMPILE_SKILL_ID,
            _COMPILE_VERSION,
            {
                "request": request,
                "seed": seed,
                "asset_catalog": catalog.model_dump(mode="json"),
                "config": {"generate_missing_assets": generate_missing_assets},
            },
        )

    def resolve_artifact(self, artifact: ArtifactRef) -> Path:
        """Resolve and reverify a compile artifact from application CAS."""

        return self._artifact_store.resolve(artifact).path

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        """Replay committed progress events for workbench and audit consumers."""

        return self._event_journal.read(
            after_event_id=after_event_id,
            run_id=run_id,
            limit=limit,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        """Recover the immutable request identity for one started execution."""

        return self._run_store.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        """Recover one terminal result after verifying Invocation and journal binding."""

        return self._run_store.read_run_state(run_id)


def create_compile_application(
    settings: CompileApplicationSettings,
) -> CompileApplication:
    """Assemble the fixed, packaged, qualified production compile application."""

    state_root = _prepare_state_root(settings.state_root)
    external_catalog_roots = _checked_trust_roots(
        settings.external_catalog_roots,
        label="external_catalog_roots",
    )
    allowed_asset_roots = _checked_trust_roots(
        settings.allowed_asset_roots,
        label="allowed_asset_roots",
    )
    if type(settings.admission_date) is not date:
        raise CompileApplicationConfigurationError("admission_date must be a date")

    artifact_store = LocalArtifactStore(state_root / "cas")
    loaded = load_qualification_bundle(
        _COMPILE_QUALIFICATION_ROOT,
        skill_ref=f"{_COMPILE_SKILL_ID}@{_COMPILE_VERSION}",
        artifact_store=artifact_store,
        implementation_root=_IMPLEMENTATION_ROOT,
        scene_gen_root=_SCENE_GEN_ROOT,
        ledger_contract_root=_LEDGER_CONTRACT_ROOT,
    )

    work_root = state_root / "work"
    generated_staging_root = state_root / "generated-staging"
    asset_library_root = state_root / "asset-library"
    for directory in (work_root, generated_staging_root, asset_library_root):
        directory.mkdir(parents=True, exist_ok=True)
    handler = Text2EnvCompileHandler(
        artifact_store=artifact_store,
        work_root=work_root,
        generated_staging_root=generated_staging_root,
        asset_library_root=asset_library_root,
        admission_date=settings.admission_date,
        allowed_asset_roots=(*allowed_asset_roots, asset_library_root.resolve()),
    )
    dependency_resolver = Text2EnvCompileDependencyResolver(
        artifact_store=artifact_store,
        handler=handler,
        scene_gen_root=_SCENE_GEN_ROOT,
        ledger_contract_root=_LEDGER_CONTRACT_ROOT,
    )
    database_path = state_root / "harness.sqlite3"
    event_journal = SQLiteEventJournal(database_path)
    run_store = SQLiteRunStore(database_path)
    registry = SkillRegistry(
        artifact_resolver=artifact_store,
        dependency_resolver=dependency_resolver,
        event_sink=event_journal,
        clock=_utc_now,
        run_id_factory=uuid4,
        run_store=run_store,
    )
    registry.register(
        text2env_compile_descriptor(
            qualification_artifact=loaded.qualification_artifact,
            implementation_sha256=loaded.implementation_sha256,
        ),
        handler,
    )
    return CompileApplication(
        state_root=state_root,
        artifact_store=artifact_store,
        event_journal=event_journal,
        run_store=run_store,
        registry=registry,
        external_catalog_roots=external_catalog_roots,
    )


def _prepare_state_root(value: Path) -> Path:
    root = Path(value).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise CompileApplicationConfigurationError(f"state_root must be a directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _checked_trust_roots(values: tuple[Path, ...], *, label: str) -> tuple[Path, ...]:
    if not values:
        raise CompileApplicationConfigurationError(f"{label} must not be empty")
    roots: list[Path] = []
    for value in values:
        candidate = Path(value).expanduser()
        try:
            root = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise CompileApplicationConfigurationError(
                f"{label} contains a missing root: {candidate}"
            ) from error
        if not root.is_dir():
            raise CompileApplicationConfigurationError(f"{label} roots must be directories: {root}")
        roots.append(root)
    return tuple(roots)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

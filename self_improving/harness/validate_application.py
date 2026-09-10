"""Production assembly for qualified legacy ``text2env.validate@2.0.0``."""

from __future__ import annotations

import fcntl
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Iterator, Literal
from uuid import UUID, uuid4

from pydantic import UUID4, TypeAdapter, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .event_journal import EventJournalCorruptionError, EventPage, SQLiteEventJournal
from .handlers.text2env_validate_v2 import (
    Text2EnvValidateV2Handler,
    text2env_validate_v2_descriptor,
)
from .qualification import load_qualification_bundle
from .registry import ControlledRunIdentityError, ControlledRunPendingError, SkillRegistry
from .run_store import RunStoreCorruptionError, SQLiteRunStore
from .schemas.common import ArtifactRef, Invocation, RunState, SkillDescriptor
from .schemas.text2env_validate_v2 import Text2EnvValidateV2Input
from .validate_v2_dependencies import (
    TEXT2ENV_VALIDATE_V2_SKILL_REF,
    Text2EnvValidateV2DependencyResolver,
    validate_v2_policy_sha256,
)

_QUALIFICATION_ROOT = (
    Path(__file__).resolve().parent / "qualified_skills" / "text2env.validate" / "2.0.0"
)
_DISTRIBUTION_ROOT = Path(__file__).resolve().parents[2]
_SCENE_GEN_ROOT = _DISTRIBUTION_ROOT / "scene_gen"
_LEDGER_CONTRACT_ROOT = (
    _DISTRIBUTION_ROOT / "self_improving" / "asset_pipeline" / "active" / "1_asset_reuse" / "lib"
)
_SKILL_ID = "text2env.validate"
_VERSION = "2.0.0"
_UUID4 = TypeAdapter(UUID4)
_FACTORY_AUTHORITY = object()
_REOPENABLE_FILES = frozenset(
    {"harness.sqlite3", "harness.sqlite3-journal", "harness.sqlite3-shm", "harness.sqlite3-wal"}
)
_REOPENABLE_DIRECTORIES = frozenset({"validate-work"})


@dataclass(frozen=True, slots=True)
class ValidateApplicationSettings:
    state_root: Path
    evidence_artifact_root: Path
    qualification_bundle_root: Path = _QUALIFICATION_ROOT
    implementation_root: Path = _DISTRIBUTION_ROOT
    scene_gen_root: Path = _SCENE_GEN_ROOT
    ledger_contract_root: Path = _LEDGER_CONTRACT_ROOT


class ValidateApplicationConfigurationError(ValueError):
    """The requested validate authority is unsafe or differs from qualification."""


class ValidateApplicationInputError(ValueError):
    """The validate input is not exact, canonical, and wholly present in evidence CAS."""


class ValidateApplicationRunIdentityError(RuntimeError):
    """A controlled child identity is already bound to another immutable intent."""


class ValidateApplicationRunPendingError(RuntimeError):
    """A controlled child began but has no terminal record safe to repeat."""


class ValidateApplication:
    """Factory-sealed facade over one qualified deterministic legacy validator."""

    durable_run_state: ClassVar[Literal[True]] = True

    def __init__(
        self,
        *,
        settings: ValidateApplicationSettings,
        artifact_store: LocalArtifactStore,
        event_journal: SQLiteEventJournal,
        run_store: SQLiteRunStore,
        registry: SkillRegistry,
    ) -> None:
        self._settings = settings
        self._artifact_store = artifact_store
        self._event_journal = event_journal
        self._run_store = run_store
        self._registry = registry
        self._factory_authority: object | None = None
        self._assembly_identity: tuple[object, ...] | None = None

    @classmethod
    def _from_factory(
        cls,
        *,
        authority: object,
        settings: ValidateApplicationSettings,
        artifact_store: LocalArtifactStore,
        event_journal: SQLiteEventJournal,
        run_store: SQLiteRunStore,
        registry: SkillRegistry,
    ) -> "ValidateApplication":
        if authority is not _FACTORY_AUTHORITY:
            raise ValidateApplicationConfigurationError("validate factory authority is absent")
        value = cls(
            settings=settings,
            artifact_store=artifact_store,
            event_journal=event_journal,
            run_store=run_store,
            registry=registry,
        )
        value._factory_authority = authority
        value._assert_golden_execution_ready()
        return value

    def _assert_golden_execution_ready(self) -> None:
        def invalid(detail: str) -> None:
            raise ValidateApplicationConfigurationError(
                f"validate production assembly is invalid: {detail}"
            )

        settings = self._settings
        if self._factory_authority is not _FACTORY_AUTHORITY:
            invalid("factory-issued authority is absent")
        if type(settings) is not ValidateApplicationSettings:
            invalid("settings type differs from factory contract")
        _validate_reopenable_state_root(settings.state_root)
        if type(self._artifact_store) is not LocalArtifactStore:
            invalid("artifact store is not the exact local CAS")
        if self._artifact_store.root != settings.evidence_artifact_root:
            invalid("artifact store differs from configured evidence CAS")
        database_path = (settings.state_root / "harness.sqlite3").resolve()
        if (
            type(self._event_journal) is not SQLiteEventJournal
            or type(self._run_store) is not SQLiteRunStore
            or self._event_journal.path.resolve() != database_path
            or self._run_store.path.resolve() != database_path
        ):
            invalid("event journal and run store do not share the state database")
        registry = self._registry
        if type(registry) is not SkillRegistry:
            invalid("registry is not the exact production Registry")
        if (
            registry._artifact_resolver is not self._artifact_store
            or registry._event_sink is not self._event_journal
            or registry._run_store is not self._run_store
            or registry._clock is not _utc_now
            or registry._run_id_factory is not uuid4
            or registry._evidence_invariant_policy is not None
        ):
            invalid("Registry authorities differ from the factory authority set")
        resolver = registry._dependency_resolver
        if type(resolver) is not Text2EnvValidateV2DependencyResolver:
            invalid("dependency resolver is not validate v2")
        registration = registry._registrations.get((_SKILL_ID, _VERSION))
        if len(registry._registrations) != 1 or registration is None:
            invalid("Registry does not contain the one exact validate Skill")
        handler = registration.handler
        descriptor = registration.descriptor
        if type(handler) is not Text2EnvValidateV2Handler:
            invalid("handler is not the exact validate v2 handler")
        if type(descriptor) is not SkillDescriptor:
            invalid("descriptor type differs from the fixed validate contract")
        assert type(descriptor) is SkillDescriptor
        if (
            descriptor.skill_id != _SKILL_ID
            or descriptor.version != _VERSION
            or descriptor.input_schema != "harness.text2env_validate_input.v2"
            or descriptor.output_schema != "harness.text2env_validate_output.v2"
            or descriptor.max_attempts != 1
        ):
            invalid("descriptor differs from the fixed validate contract")
        if (
            handler.artifact_store is not self._artifact_store
            or handler.scratch_parent.resolve() != (settings.state_root / "validate-work").resolve()
            or resolver.distribution_root.resolve() != settings.implementation_root
            or handler.policy_dependency.sha256
            != validate_v2_policy_sha256(distribution_root=settings.implementation_root)
        ):
            invalid("handler or dependency identity differs from qualification")
        live = (
            str(settings.state_root),
            str(settings.evidence_artifact_root),
            str(settings.qualification_bundle_root),
            descriptor.model_dump_json(),
            handler.policy_dependency.model_dump_json(),
        )
        if self._assembly_identity is None:
            self._assembly_identity = live
        elif self._assembly_identity != live:
            invalid("production assembly changed after construction")

    @property
    def skills(self) -> tuple[SkillDescriptor, ...]:
        self._assert_golden_execution_ready()
        return self._registry.list()  # type: ignore[return-value]

    @property
    def artifact_root(self) -> Path:
        self._assert_golden_execution_ready()
        return self._artifact_store.root

    @property
    def journal_path(self) -> Path:
        self._assert_golden_execution_ready()
        return self._event_journal.path.resolve()

    def invoke_typed(
        self,
        parameters: Text2EnvValidateV2Input,
        *,
        run_id: UUID | None = None,
    ) -> RunState:
        self._assert_golden_execution_ready()
        if run_id is not None:
            try:
                run_id = _UUID4.validate_python(run_id, strict=True)
            except ValidationError as error:
                raise ValidateApplicationRunIdentityError(
                    "controlled run_id must be an exact UUID4"
                ) from error
        if type(parameters) is not Text2EnvValidateV2Input:
            raise ValidateApplicationInputError(
                "typed validate input must be Text2EnvValidateV2Input"
            )
        try:
            trusted = Text2EnvValidateV2Input.model_validate(
                parameters.model_dump(mode="python")
            )
            for name in type(trusted).model_fields:
                if name == "gate_profile":
                    continue
                self._artifact_store.resolve(getattr(trusted, name))
        except (ArtifactResolutionError, TypeError, ValueError) as error:
            raise ValidateApplicationInputError(
                "typed validate input is unavailable or corrupt in the evidence CAS"
            ) from error
        payload = trusted.model_dump(mode="json")
        if run_id is None:
            return self._registry.invoke(_SKILL_ID, _VERSION, payload)
        try:
            return self._registry.invoke_with_run_id(
                _SKILL_ID,
                _VERSION,
                payload,
                run_id=run_id,
            )
        except ControlledRunIdentityError as error:
            raise ValidateApplicationRunIdentityError(str(error)) from error
        except ControlledRunPendingError as error:
            raise ValidateApplicationRunPendingError(str(error)) from error

    def resolve_artifact(self, artifact: ArtifactRef) -> Path:
        self._assert_golden_execution_ready()
        return self._artifact_store.resolve(artifact).path

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        self._assert_golden_execution_ready()
        return self._event_journal.read(
            after_event_id=after_event_id,
            run_id=run_id,
            limit=limit,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        self._assert_golden_execution_ready()
        return self._run_store.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        self._assert_golden_execution_ready()
        return self._run_store.read_run_state(run_id)


def create_validate_application(settings: ValidateApplicationSettings) -> ValidateApplication:
    """Verify qualification and assemble the one fixed validate Registry."""

    if type(settings) is not ValidateApplicationSettings:
        raise TypeError("settings must be ValidateApplicationSettings")
    try:
        with _assembly_lock(settings.state_root):
            return _create_validate_application_locked(settings)
    except ValidateApplicationConfigurationError:
        raise
    except (EventJournalCorruptionError, RunStoreCorruptionError, sqlite3.Error) as error:
        raise ValidateApplicationConfigurationError(
            "validate SQLite authority is invalid or corrupt"
        ) from error


def _create_validate_application_locked(
    settings: ValidateApplicationSettings,
) -> ValidateApplication:
    state_root = _directory(settings.state_root, "state_root", create=True)
    evidence_root = _directory(settings.evidence_artifact_root, "evidence_artifact_root")
    qualification_root = _directory(settings.qualification_bundle_root, "qualification_root")
    implementation_root = _directory(settings.implementation_root, "implementation_root")
    scene_gen_root = _directory(settings.scene_gen_root, "scene_gen_root")
    ledger_contract_root = _directory(settings.ledger_contract_root, "ledger_contract_root")
    normalized = ValidateApplicationSettings(
        state_root=state_root,
        evidence_artifact_root=evidence_root,
        qualification_bundle_root=qualification_root,
        implementation_root=implementation_root,
        scene_gen_root=scene_gen_root,
        ledger_contract_root=ledger_contract_root,
    )
    _validate_reopenable_state_root(state_root)
    scratch = state_root / "validate-work"
    scratch.mkdir(exist_ok=True)
    store = LocalArtifactStore(evidence_root)
    loaded = load_qualification_bundle(
        qualification_root,
        skill_ref=TEXT2ENV_VALIDATE_V2_SKILL_REF,
        artifact_store=store,
        implementation_root=implementation_root,
        scene_gen_root=scene_gen_root,
        ledger_contract_root=ledger_contract_root,
    )
    resolver = Text2EnvValidateV2DependencyResolver(distribution_root=implementation_root)
    (policy_dependency,) = resolver.resolve(
        TEXT2ENV_VALIDATE_V2_SKILL_REF,
        Text2EnvValidateV2Input.model_construct(),
    )
    handler = Text2EnvValidateV2Handler(
        artifact_store=store,
        scratch_parent=scratch,
        policy_dependency=policy_dependency,
    )
    database = state_root / "harness.sqlite3"
    journal = SQLiteEventJournal(database)
    run_store = SQLiteRunStore(database)
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=resolver,
        event_sink=journal,
        clock=_utc_now,
        run_id_factory=uuid4,
        run_store=run_store,
    )
    registry.register(
        text2env_validate_v2_descriptor(
            qualification_artifact=loaded.qualification_artifact,
            implementation_sha256=loaded.implementation_sha256,
        ),
        handler,
    )
    return ValidateApplication._from_factory(
        authority=_FACTORY_AUTHORITY,
        settings=normalized,
        artifact_store=store,
        event_journal=journal,
        run_store=run_store,
        registry=registry,
    )


@contextmanager
def _assembly_lock(value: Path) -> Iterator[None]:
    absolute = Path(value).expanduser().absolute()
    absolute.parent.mkdir(parents=True, exist_ok=True)
    lock_path = absolute.parent / f".{absolute.name}.validate-assembly.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _directory(value: Path, label: str, *, create: bool = False) -> Path:
    path = Path(value).expanduser().absolute()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    try:
        path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValidateApplicationConfigurationError(f"{label} is missing") from error
    if not resolved.is_dir() or path.is_symlink() or resolved != path:
        raise ValidateApplicationConfigurationError(f"{label} must be a canonical directory")
    return resolved


def _validate_reopenable_state_root(root: Path) -> None:
    for child in root.iterdir():
        if child.name in _REOPENABLE_FILES and child.is_file() and not child.is_symlink():
            continue
        if child.name in _REOPENABLE_DIRECTORIES and child.is_dir() and not child.is_symlink():
            continue
        raise ValidateApplicationConfigurationError(
            f"state_root contains an unexpected entry: {child.name}"
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ValidateApplication",
    "ValidateApplicationConfigurationError",
    "ValidateApplicationInputError",
    "ValidateApplicationRunIdentityError",
    "ValidateApplicationRunPendingError",
    "ValidateApplicationSettings",
    "create_validate_application",
]

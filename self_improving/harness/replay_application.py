"""Production assembly for the evidence-repeatable ``text2env.replay`` Skill.

The public factory accepts only operator locators and policy values.  It reloads
the complete qualification evidence, constructs the production handler and
dependency resolver internally, evaluates their exact fixed-case dependency
closure, and only then installs that same handler object.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Iterator, Literal
from uuid import UUID, uuid4

from pydantic import UUID4, TypeAdapter, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .event_journal import EventJournalCorruptionError, EventPage, SQLiteEventJournal
from .handlers.text2env_replay import (
    Text2EnvReplayHandler,
    Text2EnvReplayWiring,
    build_text2env_replay_wiring,
)
from .media_sandbox import NativeCgroupSandbox
from .media_verifier import SubprocessReplayMediaVerifier
from .package_store import PackageStore
from .registry import (
    ControlledRunIdentityError,
    ControlledRunPendingError,
    RegistryRegistrationError,
    SkillRegistry,
    _EvidenceInvariantRegistration,
)
from .replay_dependencies import (
    REPLAY_HANDLER_CONFIG_DEPENDENCY,
    REPLAY_RUNTIME_ASSET_DEPENDENCY,
    TEXT2ENV_REPLAY_SKILL_REF,
    Text2EnvReplayDependencyResolver,
    canonical_identity_sha256,
    replay_dependency_set,
)
from .replay_qualification import (
    _ReplayQualificationInspection,
    publish_replay_qualification_bundle,
    verify_replay_qualification_bundle,
)
from .run_store import RunStoreCorruptionError, SQLiteRunStore
from .runtime_capability import (
    RuntimeCapabilityError,
    canonical_capability_bytes,
    validate_runtime_capability_document,
)
from .runtime_executor import SubprocessRoboTwinRuntimeExecutor
from .schemas import (
    ArtifactRef,
    ExecutionReproducibility,
    Invocation,
    RunState,
    SkillDescriptorV2,
    Text2EnvReplayInput,
)

_DISTRIBUTION_ROOT = Path(__file__).resolve().parents[2]
_SCENE_GEN_ROOT = _DISTRIBUTION_ROOT / "scene_gen"
_LEDGER_CONTRACT_ROOT = (
    _DISTRIBUTION_ROOT / "self_improving" / "asset_pipeline" / "active" / "1_asset_reuse" / "lib"
)
_TASK_CONFIG = "demo_clean"
_MIN_VISIBLE_PIXELS = 64
_CHECKPOINT_STEPS = 120
_REPLAY_SKILL_ID = "text2env.replay"
_REPLAY_VERSION = "1.0.0"
_REPLAY_INPUT_SCHEMA = "harness.text2env_replay_input.v1"
_REPLAY_OUTPUT_SCHEMA = "harness.text2env_replay_output.v1"
_REPLAY_MCP_TOOL_NAME = "text2env_replay_v1_0_0"
_REPLAY_APPLICATION_FACTORY_AUTHORITY = object()
_UUID4_ADAPTER = TypeAdapter(UUID4)
_REOPENABLE_STATE_DIRECTORIES = frozenset(
    {"replay-dependencies", "replay-handler", "runtime-executor"}
)
_REOPENABLE_STATE_FILES = frozenset(
    {
        "harness.sqlite3",
        "harness.sqlite3-journal",
        "harness.sqlite3-shm",
        "harness.sqlite3-wal",
    }
)


@dataclass(frozen=True, slots=True)
class ReplayApplicationSettings:
    """Operator-owned roots and executable locators; no code is injectable."""

    state_root: Path
    qualification_bundle_root: Path
    evidence_artifact_root: Path
    implementation_root: Path
    scene_gen_root: Path
    ledger_contract_root: Path
    allowed_asset_roots: tuple[Path, ...]
    interpreter: Path
    runtime_runner: Path
    runtime_module_root: Path
    runtime_capability_path: Path
    media_launcher: Path
    static_ffmpeg: Path
    delegated_cgroup_root: Path
    runtime_timeout_seconds: float
    capability_timeout_seconds: float


class ReplayApplicationConfigurationError(ValueError):
    """Production replay wiring differs from its loaded qualification evidence."""


class ReplayApplicationInputError(ValueError):
    """Typed replay input is invalid or unavailable in the application CAS."""


class ReplayApplicationRunIdentityError(RuntimeError):
    """A controlled child identity is already bound to another replay intent."""


class ReplayApplicationRunPendingError(RuntimeError):
    """A controlled replay child started but has no terminal record to recover."""


class ReplayApplication:
    """A fixed qualified replay facade over the normal Registry execution kernel."""

    durable_run_state: ClassVar[Literal[True]] = True

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        event_journal: SQLiteEventJournal,
        run_store: SQLiteRunStore,
        registry: SkillRegistry,
    ) -> None:
        self._artifact_store = artifact_store
        self._event_journal = event_journal
        self._run_store = run_store
        self._registry = registry
        self._factory_authority: object | None = None
        self._settings: ReplayApplicationSettings | None = None
        self._state_root: Path | None = None
        self._registration_policy: _ReplayRegistrationPolicy | None = None
        self._assembly_identity: tuple[object, ...] | None = None

    @classmethod
    def _from_verified_assembly(
        cls,
        *,
        authority: object,
        settings: ReplayApplicationSettings,
        state_root: Path,
        artifact_store: LocalArtifactStore,
        event_journal: SQLiteEventJournal,
        run_store: SQLiteRunStore,
        registry: SkillRegistry,
        registration_policy: _ReplayRegistrationPolicy,
    ) -> ReplayApplication:
        if authority is not _REPLAY_APPLICATION_FACTORY_AUTHORITY:
            raise ReplayApplicationConfigurationError(
                "replay application authority was not issued by the production factory"
            )
        application = cls(
            artifact_store=artifact_store,
            event_journal=event_journal,
            run_store=run_store,
            registry=registry,
        )
        application._factory_authority = authority
        application._settings = settings
        application._state_root = state_root
        application._registration_policy = registration_policy
        application._assert_golden_execution_ready()
        return application

    def _assert_golden_execution_ready(self) -> None:
        """Revalidate the complete factory-issued authority before every trusted use."""

        def invalid(detail: str) -> None:
            raise ReplayApplicationConfigurationError(
                f"replay production assembly is invalid: {detail}"
            )

        settings = self._settings
        state_root = self._state_root
        policy = self._registration_policy
        if (
            self._factory_authority is not _REPLAY_APPLICATION_FACTORY_AUTHORITY
            or type(settings) is not ReplayApplicationSettings
            or not isinstance(state_root, Path)
            or type(policy) is not _ReplayRegistrationPolicy
        ):
            invalid("factory-issued authority is absent")
        assert settings is not None
        assert state_root is not None
        assert policy is not None
        if not state_root.is_dir() or state_root.is_symlink():
            invalid("state_root is not an existing canonical directory")
        if type(self._artifact_store) is not LocalArtifactStore:
            invalid("artifact store is not the exact local CAS")
        if self._artifact_store.root != settings.evidence_artifact_root:
            invalid("artifact store differs from the configured evidence CAS")
        if type(self._event_journal) is not SQLiteEventJournal:
            invalid("event journal is not the exact SQLite authority")
        if type(self._run_store) is not SQLiteRunStore:
            invalid("run store is not the exact SQLite authority")
        database_path = (state_root / "harness.sqlite3").resolve()
        if (
            self._event_journal.path.resolve() != database_path
            or self._run_store.path.resolve() != database_path
        ):
            invalid("event journal and run store do not share the state database")
        if type(self._registry) is not SkillRegistry:
            invalid("registry is not the exact production Registry")
        registry = self._registry
        if (
            registry._artifact_resolver is not self._artifact_store
            or registry._event_sink is not self._event_journal
            or registry._run_store is not self._run_store
            or registry._clock is not _utc_now
            or registry._run_id_factory is not uuid4
            or registry._evidence_invariant_policy is not policy
        ):
            invalid("Registry authorities are not the factory authority set")
        if (
            policy.settings is not settings
            or policy.state_root != state_root
            or policy.qualification_root != settings.qualification_bundle_root
            or policy.artifact_store is not self._artifact_store
            or policy.allowed_roots != settings.allowed_asset_roots
            or policy.implementation_root != settings.implementation_root
            or policy.scene_gen_root != settings.scene_gen_root
            or policy.ledger_contract_root != settings.ledger_contract_root
        ):
            invalid("registration policy differs from factory settings")
        registrations = registry._registrations
        registration = registrations.get((_REPLAY_SKILL_ID, _REPLAY_VERSION))
        if len(registrations) != 1 or registration is None:
            invalid("Registry does not contain the one exact replay Skill")
        descriptor = registration.descriptor
        if (
            type(descriptor) is not SkillDescriptorV2
            or descriptor.skill_id != _REPLAY_SKILL_ID
            or descriptor.version != _REPLAY_VERSION
            or descriptor.mcp_tool_name != _REPLAY_MCP_TOOL_NAME
            or descriptor.input_schema != _REPLAY_INPUT_SCHEMA
            or descriptor.output_schema != _REPLAY_OUTPUT_SCHEMA
            or descriptor.reproducibility
            is not ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE
            or descriptor.max_attempts != 2
        ):
            invalid("Registry descriptor differs from the fixed replay contract")
        handler = registration.handler
        resolver = registry._dependency_resolver
        if type(handler) is not Text2EnvReplayHandler:
            invalid("replay handler is not the exact production handler")
        if type(resolver) is not Text2EnvReplayDependencyResolver:
            invalid("dependency resolver is not the exact replay resolver")
        if handler.dependency_resolver is not resolver:
            invalid("handler and Registry do not share the replay resolver")
        if (
            handler.artifact_store is not self._artifact_store
            or resolver.artifact_store is not self._artifact_store
            or type(handler.package_store) is not PackageStore
            or type(resolver.package_store) is not PackageStore
            or handler.package_store.artifact_store is not self._artifact_store
            or resolver.package_store.artifact_store is not self._artifact_store
        ):
            invalid("handler and resolver do not share the application CAS")
        if (
            handler.allowed_asset_roots != settings.allowed_asset_roots
            or resolver.allowed_asset_roots != settings.allowed_asset_roots
            or handler.work_root.expanduser().resolve() != (state_root / "replay-handler").resolve()
            or resolver.work_root.expanduser().resolve()
            != (state_root / "replay-dependencies").resolve()
        ):
            invalid("replay mutable or asset roots differ from factory settings")
        try:
            handler._require_configuration_unchanged()
            executor_identity = canonical_identity_sha256(
                handler.runtime_executor.identity,
                label="runtime executor",
            )
            media_identity = canonical_identity_sha256(
                handler.media_verifier.identity,
                label="media verifier",
            )
        except Exception as error:
            raise ReplayApplicationConfigurationError(
                "replay production assembly is invalid: handler configuration changed"
            ) from error
        if (
            resolver.runtime_executor_identity != handler.runtime_executor.identity
            or resolver.media_verifier_identity != handler.media_verifier.identity
            or resolver.handler_config_sha256 != handler.expected_handler_config_sha256
            or resolver.expected_capability_sha256 != handler.expected_capability_sha256
            or handler.task_config != _TASK_CONFIG
            or handler.min_visible_pixels != _MIN_VISIBLE_PIXELS
            or handler.checkpoint_steps != _CHECKPOINT_STEPS
        ):
            invalid("handler and dependency identities differ")
        live_identity: tuple[object, ...] = (
            _settings_identity(settings),
            descriptor.model_dump_json(),
            executor_identity,
            media_identity,
            handler.expected_handler_config_sha256,
            handler.expected_runtime_asset_snapshot_sha256,
        )
        if self._assembly_identity is None:
            self._assembly_identity = live_identity
        elif self._assembly_identity != live_identity:
            invalid("production handler, dependency, or settings identity changed")

    @property
    def skills(self) -> tuple[SkillDescriptorV2, ...]:
        self._assert_golden_execution_ready()
        skills = self._registry.list()
        if any(type(item) is not SkillDescriptorV2 for item in skills):
            raise RuntimeError("replay application registry contains another descriptor type")
        return skills  # type: ignore[return-value]

    @property
    def artifact_root(self) -> Path:
        self._assert_golden_execution_ready()
        return self._artifact_store.root

    @property
    def journal_path(self) -> Path:
        self._assert_golden_execution_ready()
        return self._event_journal.path.resolve()

    def replay(self, value: Text2EnvReplayInput) -> RunState:
        return self.invoke_typed(value)

    def invoke_typed(
        self,
        parameters: Text2EnvReplayInput,
        *,
        run_id: UUID | None = None,
    ) -> RunState:
        """Invoke replay once under an optional caller-owned durable UUID4 identity."""

        self._assert_golden_execution_ready()
        if run_id is not None:
            try:
                run_id = _UUID4_ADAPTER.validate_python(run_id, strict=True)
            except ValidationError as error:
                raise ReplayApplicationRunIdentityError(
                    "controlled run_id must be an exact UUID4"
                ) from error
        if type(parameters) is not Text2EnvReplayInput:
            raise ReplayApplicationInputError(
                "typed replay input is invalid: expected Text2EnvReplayInput"
            )
        try:
            trusted = Text2EnvReplayInput.model_validate(parameters.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            raise ReplayApplicationInputError("typed replay input is invalid") from error
        for artifact in (
            trusted.environment_package.asset_catalog,
            trusted.environment_package.package_manifest,
        ):
            if artifact.uri != f"artifact://sha256/{artifact.sha256}":
                raise ReplayApplicationInputError(
                    "typed replay input is invalid: package inputs must be in application CAS"
                )
            try:
                self._artifact_store.resolve(artifact)
            except ArtifactResolutionError as error:
                raise ReplayApplicationInputError(
                    "typed replay input package is unavailable or corrupt in application CAS"
                ) from error
        effective_parameters = trusted.model_dump(mode="json")
        if run_id is None:
            return self._registry.invoke(
                _REPLAY_SKILL_ID,
                _REPLAY_VERSION,
                effective_parameters,
            )
        try:
            return self._registry.invoke_with_run_id(
                _REPLAY_SKILL_ID,
                _REPLAY_VERSION,
                effective_parameters,
                run_id=run_id,
            )
        except ControlledRunIdentityError as error:
            raise ReplayApplicationRunIdentityError(str(error)) from error
        except ControlledRunPendingError as error:
            raise ReplayApplicationRunPendingError(str(error)) from error

    def resolve_artifact(self, artifact: ArtifactRef) -> Path:
        """Resolve and reverify one replay artifact from the application CAS."""

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


def create_replay_application(settings: ReplayApplicationSettings) -> ReplayApplication:
    """Serialize assembly, then verify evidence and register the exact handler."""

    if not isinstance(settings, ReplayApplicationSettings):
        raise TypeError("settings must be ReplayApplicationSettings")
    try:
        with _state_root_assembly_lock(settings.state_root):
            return _create_replay_application_locked(settings)
    except ReplayApplicationConfigurationError:
        raise
    except (EventJournalCorruptionError, RunStoreCorruptionError, sqlite3.Error) as error:
        raise ReplayApplicationConfigurationError(
            "replay SQLite authority is invalid or corrupt"
        ) from error


def _create_replay_application_locked(
    settings: ReplayApplicationSettings,
) -> ReplayApplication:
    """Assemble while holding the unique lock for this canonical state root."""

    qualification_root = _directory(settings.qualification_bundle_root, label="qualification_root")
    evidence_root = _directory(settings.evidence_artifact_root, label="evidence_artifact_root")
    allowed_roots = _directories(settings.allowed_asset_roots, label="allowed_asset_roots")
    implementation_root, scene_gen_root, ledger_contract_root = _source_roots(settings)
    interpreter = _file(settings.interpreter, label="interpreter")
    runtime_runner = _file(settings.runtime_runner, label="runtime_runner")
    runtime_module_root = _directory(settings.runtime_module_root, label="runtime_module_root")
    runtime_capability_path = _file(
        settings.runtime_capability_path,
        label="runtime_capability_path",
    )
    media_launcher = _file(settings.media_launcher, label="media_launcher")
    static_ffmpeg = _file(settings.static_ffmpeg, label="static_ffmpeg")
    delegated_cgroup_root = _directory(
        settings.delegated_cgroup_root,
        label="delegated_cgroup_root",
    )
    runtime_timeout_seconds = _positive_timeout(settings.runtime_timeout_seconds)
    capability_timeout_seconds = _positive_timeout(settings.capability_timeout_seconds)
    state_existed = settings.state_root.exists()
    state_root = _directory(settings.state_root, label="state_root", create=True)
    preexisting_names = frozenset(child.name for child in state_root.iterdir())
    normalized_settings = ReplayApplicationSettings(
        state_root=state_root,
        qualification_bundle_root=qualification_root,
        evidence_artifact_root=evidence_root,
        implementation_root=implementation_root,
        scene_gen_root=scene_gen_root,
        ledger_contract_root=ledger_contract_root,
        allowed_asset_roots=allowed_roots,
        interpreter=interpreter,
        runtime_runner=runtime_runner,
        runtime_module_root=runtime_module_root,
        runtime_capability_path=runtime_capability_path,
        media_launcher=media_launcher,
        static_ffmpeg=static_ffmpeg,
        delegated_cgroup_root=delegated_cgroup_root,
        runtime_timeout_seconds=runtime_timeout_seconds,
        capability_timeout_seconds=capability_timeout_seconds,
    )
    artifact_store = LocalArtifactStore(evidence_root)
    database_path = state_root / "harness.sqlite3"
    policy = _ReplayRegistrationPolicy(
        settings=normalized_settings,
        state_root=state_root,
        qualification_root=qualification_root,
        artifact_store=artifact_store,
        allowed_roots=allowed_roots,
        implementation_root=implementation_root,
        scene_gen_root=scene_gen_root,
        ledger_contract_root=ledger_contract_root,
    )
    try:
        _validate_reopenable_state_root(state_root)
        event_journal = SQLiteEventJournal(database_path)
        run_store = SQLiteRunStore(database_path)
        registry = SkillRegistry(
            artifact_resolver=artifact_store,
            dependency_resolver=None,
            event_sink=event_journal,
            clock=_utc_now,
            run_id_factory=uuid4,
            run_store=run_store,
            evidence_invariant_policy=policy,
        )
        try:
            registry.register_evidence_invariant(normalized_settings)
        except RegistryRegistrationError as error:
            raise ReplayApplicationConfigurationError(str(error)) from error
        publish_replay_qualification_bundle(
            qualification_root,
            artifact_store=artifact_store,
            implementation_root=implementation_root,
            scene_gen_root=scene_gen_root,
            ledger_contract_root=ledger_contract_root,
        )
        return ReplayApplication._from_verified_assembly(
            authority=_REPLAY_APPLICATION_FACTORY_AUTHORITY,
            settings=normalized_settings,
            state_root=state_root,
            artifact_store=artifact_store,
            event_journal=event_journal,
            run_store=run_store,
            registry=registry,
            registration_policy=policy,
        )
    except Exception:
        _rollback_new_state_entries(
            state_root,
            preexisting_names=preexisting_names,
            remove_root=not state_existed,
        )
        raise


@dataclass(frozen=True, slots=True)
class _ReplayRegistrationPolicy:
    """Fixed production policy that owns deep verification and handler construction."""

    settings: ReplayApplicationSettings
    state_root: Path
    qualification_root: Path
    artifact_store: LocalArtifactStore
    allowed_roots: tuple[Path, ...]
    implementation_root: Path
    scene_gen_root: Path
    ledger_contract_root: Path

    def verify_and_build(
        self,
        request: object,
        *,
        artifact_resolver: object,
    ) -> _EvidenceInvariantRegistration:
        if type(request) is not ReplayApplicationSettings or request != self.settings:
            raise ReplayApplicationConfigurationError(
                "replay registration request must be the exact operator settings"
            )
        if artifact_resolver is not self.artifact_store:
            raise ReplayApplicationConfigurationError(
                "replay registration must share the application evidence CAS"
            )
        inspected = verify_replay_qualification_bundle(
            self.qualification_root,
            artifact_store=self.artifact_store,
            implementation_root=self.implementation_root,
            scene_gen_root=self.scene_gen_root,
            ledger_contract_root=self.ledger_contract_root,
        )
        _verify_settings_match_evidence(
            self.settings,
            inspected,
            allowed_roots=self.allowed_roots,
        )
        wiring = _assemble_live_wiring(
            settings=self.settings,
            state_root=self.state_root,
            artifact_store=self.artifact_store,
            allowed_roots=self.allowed_roots,
        )
        _verify_live_wiring(
            inspected=inspected,
            wiring=wiring,
            artifact_store=self.artifact_store,
        )
        return _EvidenceInvariantRegistration(
            descriptor=inspected.descriptor,
            handler=wiring.handler,
            dependency_resolver=wiring.dependency_resolver,
            qualification_bytes=inspected.generic.qualification_bytes,
            report_bytes=inspected.generic.report_bytes,
        )


def _assemble_live_wiring(
    *,
    settings: ReplayApplicationSettings,
    state_root: Path,
    artifact_store: LocalArtifactStore,
    allowed_roots: tuple[Path, ...],
) -> Text2EnvReplayWiring:
    """Construct the only production replay handler/resolver pair."""

    runtime = SubprocessRoboTwinRuntimeExecutor(
        interpreter=_file(settings.interpreter, label="interpreter"),
        runner=_file(settings.runtime_runner, label="runtime_runner"),
        work_root=state_root / "runtime-executor",
        timeout_seconds=_positive_timeout(settings.runtime_timeout_seconds),
        capability_timeout_seconds=_positive_timeout(settings.capability_timeout_seconds),
        module_root=_directory(settings.runtime_module_root, label="runtime_module_root"),
    )
    sandbox = NativeCgroupSandbox(
        launcher=_file(settings.media_launcher, label="media_launcher"),
        launcher_source=(_DISTRIBUTION_ROOT / "self_improving/harness/native/media_sandbox.c"),
        ffmpeg=_file(settings.static_ffmpeg, label="static_ffmpeg"),
        delegated_cgroup_root=_directory(
            settings.delegated_cgroup_root,
            label="delegated_cgroup_root",
        ),
    )
    media_verifier = SubprocessReplayMediaVerifier(sandbox=sandbox)
    package_store = PackageStore(artifact_store)
    wiring = build_text2env_replay_wiring(
        artifact_store=artifact_store,
        package_store=package_store,
        runtime_executor=runtime,
        media_verifier=media_verifier,
        work_root=state_root / "replay-handler",
        dependency_work_root=state_root / "replay-dependencies",
        allowed_asset_roots=allowed_roots,
        expected_capability_sha256=_capability_sha256(settings.runtime_capability_path),
        task_config=_TASK_CONFIG,
        min_visible_pixels=_MIN_VISIBLE_PIXELS,
        checkpoint_steps=_CHECKPOINT_STEPS,
    )
    return wiring


def _verify_live_wiring(
    *,
    inspected: _ReplayQualificationInspection,
    wiring: Text2EnvReplayWiring,
    artifact_store: LocalArtifactStore,
) -> None:
    """Re-evaluate the fixed qualified input through the exact live resolver."""

    if (
        type(wiring) is not Text2EnvReplayWiring
        or type(wiring.handler) is not Text2EnvReplayHandler
        or type(wiring.dependency_resolver) is not Text2EnvReplayDependencyResolver
        or wiring.handler.dependency_resolver is not wiring.dependency_resolver
        or wiring.handler.artifact_store is not artifact_store
        or wiring.dependency_resolver.artifact_store is not artifact_store
    ):
        raise ReplayApplicationConfigurationError(
            "production replay issuer requires the internally built concrete wiring"
        )
    try:
        wiring.handler._require_configuration_unchanged()
    except Exception as error:
        raise ReplayApplicationConfigurationError(
            "production replay issuer requires complete production replay wiring"
        ) from error
    try:
        fixed_input = Text2EnvReplayInput.model_validate(
            inspected.evidence.kernel_evaluation.invocation.effective_parameters
        )
        dependencies = wiring.dependency_resolver.resolve(
            TEXT2ENV_REPLAY_SKILL_REF,
            fixed_input,
        )
    except Exception as error:
        raise ReplayApplicationConfigurationError(
            "live replay dependency closure cannot reproduce the qualified fixed case"
        ) from error
    if dependencies != inspected.exact_dependencies.dependencies:
        raise ReplayApplicationConfigurationError(
            "live replay dependency closure differs from qualification evidence"
        )
    dependencies_by_name = {item.name: item for item in dependencies}
    try:
        handler_dependencies = replay_dependency_set(
            runtime_executor_identity=wiring.handler.runtime_executor.identity,
            handler_config_sha256=wiring.handler.expected_handler_config_sha256,
            expected_capability_sha256=wiring.handler.expected_capability_sha256,
            runtime_asset_snapshot_sha256=(
                dependencies_by_name[REPLAY_RUNTIME_ASSET_DEPENDENCY].sha256
            ),
            media_verifier_identity=wiring.handler.media_verifier.identity,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ReplayApplicationConfigurationError(
            "live replay handler execution wiring is incomplete"
        ) from error
    if handler_dependencies != dependencies:
        raise ReplayApplicationConfigurationError(
            "live replay handler execution wiring differs from qualification evidence"
        )
    handler_dependency = next(
        item for item in dependencies if item.name == REPLAY_HANDLER_CONFIG_DEPENDENCY
    )
    if wiring.handler.dependency_identity != handler_dependency:
        raise ReplayApplicationConfigurationError(
            "live replay handler configuration differs from its dependency proof"
        )


def _verify_settings_match_evidence(
    settings: ReplayApplicationSettings,
    inspected: _ReplayQualificationInspection,
    *,
    allowed_roots: tuple[Path, ...],
) -> None:
    evidence = inspected.evidence
    identities = {item.label: item for item in evidence.operator_before.files}
    actual_files = {
        "interpreter": _file(settings.interpreter, label="interpreter"),
        "media_launcher": _file(settings.media_launcher, label="media_launcher"),
        "runtime_capability": _file(
            settings.runtime_capability_path,
            label="runtime_capability_path",
        ),
        "runtime_runner": _file(settings.runtime_runner, label="runtime_runner"),
        "static_ffmpeg": _file(settings.static_ffmpeg, label="static_ffmpeg"),
    }
    if any(str(path) != identities[label].path for label, path in actual_files.items()):
        raise ReplayApplicationConfigurationError(
            "production operator file locators differ from qualification"
        )
    if (
        tuple(str(item) for item in allowed_roots) != evidence.operator_before.allowed_asset_roots
        or str(_directory(settings.runtime_module_root, label="runtime_module_root"))
        != evidence.operator_before.runtime_module_root
        or str(_directory(settings.delegated_cgroup_root, label="delegated_cgroup_root"))
        != evidence.operator_before.delegated_cgroup_root
    ):
        raise ReplayApplicationConfigurationError(
            "production operator roots differ from qualification"
        )


def _capability_sha256(path: Path) -> str:
    capability_path = _file(path, label="runtime_capability_path")
    try:
        document = validate_runtime_capability_document(json.loads(capability_path.read_bytes()))
    except (OSError, ValueError, RuntimeCapabilityError) as error:
        raise ReplayApplicationConfigurationError(
            "runtime capability is not the strict production document"
        ) from error
    payload = canonical_capability_bytes(document)
    if payload != capability_path.read_bytes():
        raise ReplayApplicationConfigurationError("runtime capability is not canonical")
    return hashlib.sha256(payload).hexdigest()


def _file(value: Path, *, label: str) -> Path:
    if not isinstance(value, Path):
        raise ReplayApplicationConfigurationError(f"{label} must be a Path")
    try:
        path = value.expanduser().resolve(strict=True)
    except OSError as error:
        raise ReplayApplicationConfigurationError(f"{label} is unavailable") from error
    if value.expanduser().absolute() != path or value.is_symlink() or not path.is_file():
        raise ReplayApplicationConfigurationError(f"{label} is not a canonical regular file")
    return path


def _directory(value: Path, *, label: str, create: bool = False) -> Path:
    if not isinstance(value, Path):
        raise ReplayApplicationConfigurationError(f"{label} must be a Path")
    path = value.expanduser().absolute()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReplayApplicationConfigurationError(f"{label} is unavailable") from error
    if path != resolved or path.is_symlink() or not path.is_dir():
        raise ReplayApplicationConfigurationError(f"{label} is not a canonical directory")
    return resolved


def _directories(values: tuple[Path, ...], *, label: str) -> tuple[Path, ...]:
    if not isinstance(values, tuple) or not values:
        raise ReplayApplicationConfigurationError(f"{label} must be a nonempty tuple")
    roots = tuple(_directory(value, label=label) for value in values)
    if roots != tuple(sorted(set(roots), key=lambda item: str(item))):
        raise ReplayApplicationConfigurationError(f"{label} must be unique and sorted")
    return roots


def _source_roots(settings: ReplayApplicationSettings) -> tuple[Path, Path, Path]:
    implementation = _directory(settings.implementation_root, label="implementation_root")
    scene_gen = _directory(settings.scene_gen_root, label="scene_gen_root")
    ledger = _directory(settings.ledger_contract_root, label="ledger_contract_root")
    if (
        implementation != _DISTRIBUTION_ROOT
        or scene_gen != _SCENE_GEN_ROOT
        or ledger != _LEDGER_CONTRACT_ROOT
    ):
        raise ReplayApplicationConfigurationError(
            "production source roots must be the actual replay distribution roots"
        )
    return implementation, scene_gen, ledger


def _settings_identity(settings: ReplayApplicationSettings) -> tuple[object, ...]:
    """Return the canonical operator policy identity rechecked by a sealed facade."""

    return (
        str(_directory(settings.state_root, label="state_root")),
        str(_directory(settings.qualification_bundle_root, label="qualification_root")),
        str(_directory(settings.evidence_artifact_root, label="evidence_artifact_root")),
        tuple(str(path) for path in _source_roots(settings)),
        tuple(
            str(path)
            for path in _directories(settings.allowed_asset_roots, label="allowed_asset_roots")
        ),
        str(_file(settings.interpreter, label="interpreter")),
        str(_file(settings.runtime_runner, label="runtime_runner")),
        str(_directory(settings.runtime_module_root, label="runtime_module_root")),
        str(_file(settings.runtime_capability_path, label="runtime_capability_path")),
        str(_file(settings.media_launcher, label="media_launcher")),
        str(_file(settings.static_ffmpeg, label="static_ffmpeg")),
        str(_directory(settings.delegated_cgroup_root, label="delegated_cgroup_root")),
        _positive_timeout(settings.runtime_timeout_seconds),
        _positive_timeout(settings.capability_timeout_seconds),
    )


@contextmanager
def _state_root_assembly_lock(value: Path) -> Iterator[None]:
    """Linearize all factory ownership decisions for one canonical state leaf."""

    if not isinstance(value, Path):
        raise ReplayApplicationConfigurationError("state_root must be a Path")
    candidate = value.expanduser().absolute()
    if not candidate.name:
        raise ReplayApplicationConfigurationError("state_root must be a named directory leaf")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    parent = _directory(candidate.parent, label="state_root parent")
    if candidate.parent != parent:
        raise ReplayApplicationConfigurationError("state_root parent is not canonical")
    lock_path = parent / f".{candidate.name}.replay-application.lock"
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise ReplayApplicationConfigurationError(
            "state_root assembly lock is unavailable"
        ) from error
    try:
        opened = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ReplayApplicationConfigurationError(
                "state_root assembly lock is not one regular file"
            )
        fcntl.flock(file_descriptor, fcntl.LOCK_EX)
        current = os.stat(lock_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ReplayApplicationConfigurationError(
                "state_root assembly lock identity changed while acquiring it"
            )
        yield
    finally:
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(file_descriptor)


def _validate_reopenable_state_root(root: Path) -> None:
    """Admit only the database and exact replay-owned work directories on reopen."""

    for child in root.iterdir():
        if child.is_symlink():
            raise ReplayApplicationConfigurationError(
                f"state_root contains an unsafe entry: {child.name}"
            )
        if child.name in _REOPENABLE_STATE_DIRECTORIES:
            if not child.is_dir():
                raise ReplayApplicationConfigurationError(
                    f"state_root replay directory has the wrong type: {child.name}"
                )
            continue
        if child.name in _REOPENABLE_STATE_FILES:
            if not child.is_file():
                raise ReplayApplicationConfigurationError(
                    f"state_root database entry has the wrong type: {child.name}"
                )
            continue
        raise ReplayApplicationConfigurationError(
            f"state_root contains an unexpected entry: {child.name}"
        )


def _rollback_new_state_entries(
    root: Path,
    *,
    preexisting_names: frozenset[str],
    remove_root: bool,
) -> None:
    """Remove only entries created by the failed assembly, never recovered authority."""

    for child in tuple(root.iterdir()):
        if child.name in preexisting_names:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    if remove_root and not any(root.iterdir()):
        root.rmdir()


def _rollback_state_root(root: Path, *, remove_root: bool) -> None:
    for child in tuple(root.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    if remove_root:
        root.rmdir()


def _positive_timeout(value: float) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
        raise ReplayApplicationConfigurationError("timeouts must be positive and finite")
    return float(value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

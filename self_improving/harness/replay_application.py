"""Production assembly for the evidence-repeatable ``text2env.replay`` Skill.

The public factory accepts only operator locators and policy values.  It reloads
the complete qualification evidence, constructs the production handler and
dependency resolver internally, evaluates their exact fixed-case dependency
closure, and only then installs that same handler object.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Literal
from uuid import UUID, uuid4

from .artifacts import LocalArtifactStore
from .event_journal import EventPage, SQLiteEventJournal
from .handlers.text2env_replay import (
    Text2EnvReplayHandler,
    Text2EnvReplayWiring,
    build_text2env_replay_wiring,
)
from .media_sandbox import NativeCgroupSandbox
from .media_verifier import SubprocessReplayMediaVerifier
from .package_store import PackageStore
from .registry import RegistryRegistrationError, SkillRegistry, _EvidenceInvariantRegistration
from .replay_dependencies import (
    REPLAY_HANDLER_CONFIG_DEPENDENCY,
    REPLAY_RUNTIME_ASSET_DEPENDENCY,
    TEXT2ENV_REPLAY_SKILL_REF,
    Text2EnvReplayDependencyResolver,
    replay_dependency_set,
)
from .replay_qualification import (
    _ReplayQualificationInspection,
    publish_replay_qualification_bundle,
    verify_replay_qualification_bundle,
)
from .run_store import SQLiteRunStore
from .runtime_capability import (
    RuntimeCapabilityError,
    canonical_capability_bytes,
    validate_runtime_capability_document,
)
from .runtime_executor import SubprocessRoboTwinRuntimeExecutor
from .schemas import Invocation, RunState, SkillDescriptorV2, Text2EnvReplayInput

_DISTRIBUTION_ROOT = Path(__file__).resolve().parents[2]
_SCENE_GEN_ROOT = _DISTRIBUTION_ROOT / "scene_gen"
_LEDGER_CONTRACT_ROOT = (
    _DISTRIBUTION_ROOT / "self_improving" / "asset_pipeline" / "active" / "asset_reuse" / "lib"
)
_TASK_CONFIG = "demo_clean"
_MIN_VISIBLE_PIXELS = 64
_CHECKPOINT_STEPS = 120


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

    @property
    def skills(self) -> tuple[SkillDescriptorV2, ...]:
        skills = self._registry.list()
        if any(type(item) is not SkillDescriptorV2 for item in skills):
            raise RuntimeError("replay application registry contains another descriptor type")
        return skills  # type: ignore[return-value]

    @property
    def artifact_root(self) -> Path:
        return self._artifact_store.root

    @property
    def journal_path(self) -> Path:
        return self._event_journal.path.resolve()

    def replay(self, value: Text2EnvReplayInput) -> RunState:
        if not isinstance(value, Text2EnvReplayInput):
            raise TypeError("replay requires Text2EnvReplayInput")
        return self._registry.invoke(
            "text2env.replay",
            "1.0.0",
            value.model_dump(mode="json"),
        )

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        return self._event_journal.read(
            after_event_id=after_event_id,
            run_id=run_id,
            limit=limit,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self._run_store.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self._run_store.read_run_state(run_id)


def create_replay_application(settings: ReplayApplicationSettings) -> ReplayApplication:
    """Verify evidence and live wiring in one call, then register the exact handler."""

    if not isinstance(settings, ReplayApplicationSettings):
        raise TypeError("settings must be ReplayApplicationSettings")
    qualification_root = _directory(
        settings.qualification_bundle_root,
        label="qualification_bundle_root",
    )
    evidence_root = _directory(
        settings.evidence_artifact_root,
        label="evidence_artifact_root",
    )
    allowed_roots = _directories(settings.allowed_asset_roots, label="allowed_asset_roots")
    implementation_root, scene_gen_root, ledger_contract_root = _source_roots(settings)
    state_existed = settings.state_root.exists()
    state_root = _directory(settings.state_root, label="state_root", create=True)
    if any(state_root.iterdir()):
        raise ReplayApplicationConfigurationError("state_root must be empty for atomic assembly")
    artifact_store = LocalArtifactStore(evidence_root)
    database_path = state_root / "harness.sqlite3"
    policy = _ReplayRegistrationPolicy(
        settings=settings,
        state_root=state_root,
        qualification_root=qualification_root,
        artifact_store=artifact_store,
        allowed_roots=allowed_roots,
        implementation_root=implementation_root,
        scene_gen_root=scene_gen_root,
        ledger_contract_root=ledger_contract_root,
    )
    try:
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
            registry.register_evidence_invariant(settings)
        except RegistryRegistrationError as error:
            raise ReplayApplicationConfigurationError(str(error)) from error
        publish_replay_qualification_bundle(
            qualification_root,
            artifact_store=artifact_store,
            implementation_root=implementation_root,
            scene_gen_root=scene_gen_root,
            ledger_contract_root=ledger_contract_root,
        )
        return ReplayApplication(
            artifact_store=artifact_store,
            event_journal=event_journal,
            run_store=run_store,
            registry=registry,
        )
    except Exception:
        _rollback_state_root(state_root, remove_root=not state_existed)
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

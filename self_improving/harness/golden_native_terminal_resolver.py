"""Golden-owned glue between sealed native bindings and terminal admission evidence."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

from pydantic import ValidationError

from .artifacts import LocalArtifactStore
from .genesis_fresh_capture_application import GenesisFreshCaptureApplication
from .genesis_scene_application_v2 import GenesisSceneApplication
from .genesis_scene_validation_application import GenesisSceneValidationApplication
from .golden_execution import GoldenExecutionBinding
from .golden_image2env_execution import (
    _NativeApplicationAdapter,
    _require_native_image_execution_binding,
)
from .image2env_genesis_compile_application import Image2EnvGenesisCompileApplication
from .native_terminal_admission import (
    VerifiedLiveNativeTerminal,
    VerifiedNativeTerminalReload,
    admit_native_terminal,
    reload_native_terminal,
)
from .run_store import ControlledRunLifecycle, SQLiteRunStore
from .schemas.common import ArtifactRef, RunStatus
from .schemas.genesis_fresh_capture import GenesisFreshCaptureInputV1
from .schemas.native_terminal_admission import (
    NATIVE_READ_CLOSURE_ID,
    NATIVE_TERMINAL_ADMISSION_ID,
    NativeReadClosureV1,
    NativeTerminalAdmissionV1,
)
from .typed_qualification_admission import (
    VerifiedTypedQualificationAdmission,
    verify_typed_qualification_admission,
)

_BINDING_SET_TOKEN = object()
_RESOLVER_TOKEN = object()
_SKILLS = (
    "image2env.compile@2.0.0",
    "image2env.replay@2.0.0",
    "genesis.observe@3.0.0",
    "image2env.validate@2.0.0",
)
_APPLICATION_TYPES = (
    Image2EnvGenesisCompileApplication,
    GenesisSceneApplication,
    GenesisFreshCaptureApplication,
    GenesisSceneValidationApplication,
)


class NativeTerminalResolverError(ValueError):
    """A native terminal or its indexed read closure failed closed."""


@dataclass(frozen=True)
class NativeTerminalEvidenceRoots:
    """The only two terminal-admission references placed in a Golden receipt."""

    admission: ArtifactRef
    read_closure: ArtifactRef

    def __post_init__(self) -> None:
        _require_ref(self.admission, NATIVE_TERMINAL_ADMISSION_ID, "terminal admission")
        _require_ref(self.read_closure, NATIVE_READ_CLOSURE_ID, "native read closure")
        if self.admission.sha256 == self.read_closure.sha256:
            raise ValueError("terminal admission and read closure must be distinct")

    def as_artifacts(self) -> tuple[ArtifactRef, ArtifactRef]:
        return tuple(
            sorted(
                (self.admission, self.read_closure),
                key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
            )
        )


@dataclass(frozen=True)
class NativeClosureMember:
    """One requested member after index membership and CAS bytes are verified."""

    sha256: str
    bytes: int
    artifact: ArtifactRef | None
    payload: bytes


class NativeImageExecutionBindings:
    """Opaque four-application capability derived from exact rich native bindings."""

    __slots__ = ("_bindings", "_store", "_token")

    def __init__(
        self,
        bindings: tuple[GoldenExecutionBinding, ...],
        store: LocalArtifactStore,
        *,
        token: object,
    ) -> None:
        if token is not _BINDING_SET_TOKEN:
            raise TypeError("native image binding sets require the exact factory")
        object.__setattr__(self, "_bindings", bindings)
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_token", token)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("native image execution bindings are immutable")

    @property
    def bindings(self) -> tuple[GoldenExecutionBinding, ...]:
        return self._bindings

    @property
    def artifact_store(self) -> LocalArtifactStore:
        return self._store

    def binding_for(self, skill_ref: str) -> GoldenExecutionBinding:
        for binding in self._bindings:
            if binding.skill_ref == skill_ref:
                return binding
        raise LookupError(f"native image binding is unavailable: {skill_ref}")

    def _is_factory_set(self) -> bool:
        return self._token is _BINDING_SET_TOKEN


@dataclass(frozen=True)
class _ResolverEntry:
    binding: GoldenExecutionBinding
    application: object
    authority: SQLiteRunStore
    qualification: VerifiedTypedQualificationAdmission


class NativeTerminalResolver:
    """Sealed terminal producer/reloader bound to four native applications."""

    __slots__ = (
        "_binding_set",
        "_entries",
        "_identity_sha256",
        "_live",
        "_reloaded",
        "_store",
        "_token",
    )

    def __init__(
        self,
        *,
        binding_set: NativeImageExecutionBindings,
        entries: Mapping[str, _ResolverEntry],
        identity_sha256: str,
        token: object,
    ) -> None:
        if token is not _RESOLVER_TOKEN:
            raise TypeError("native terminal resolvers require the exact factory")
        object.__setattr__(self, "_binding_set", binding_set)
        object.__setattr__(self, "_entries", dict(entries))
        object.__setattr__(self, "_identity_sha256", identity_sha256)
        object.__setattr__(self, "_live", {})
        object.__setattr__(self, "_reloaded", {})
        object.__setattr__(self, "_store", binding_set.artifact_store)
        object.__setattr__(self, "_token", token)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("native terminal resolvers are immutable")

    @property
    def identity_sha256(self) -> str:
        return self._identity_sha256

    @property
    def artifact_store(self) -> LocalArtifactStore:
        return self._store

    def owns(
        self,
        *,
        binding: GoldenExecutionBinding,
        qualification: VerifiedTypedQualificationAdmission,
    ) -> bool:
        entry = self._entries.get(binding.skill_ref)
        return (
            entry is not None
            and entry.binding is binding
            and entry.qualification.admission_ref == qualification.admission_ref
            and entry.qualification.admission == qualification.admission
            and self._token is _RESOLVER_TOKEN
        )

    def admit(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
    ) -> NativeTerminalEvidenceRoots:
        """Deeply admit an exact successful child and retain only opaque live authority."""

        entry = self._entry(skill_ref)
        self._require_lifecycle(entry, lifecycle, child_run_id)
        predecessor = None
        if skill_ref == "genesis.observe@3.0.0":
            invocation = lifecycle.invocation
            assert invocation is not None
            parameters = GenesisFreshCaptureInputV1.model_validate(invocation.effective_parameters)
            predecessor = self._live.get(("image2env.replay@2.0.0", parameters.replay_run_id))
            if type(predecessor) is not VerifiedLiveNativeTerminal:
                predecessor = self._admit_replay(parameters.replay_run_id)
        capability = admit_native_terminal(
            application=entry.application,
            artifact_store=self._store,
            run_id=child_run_id,
            qualification_admission=entry.qualification,
            child_authority=entry.authority,
            predecessor_terminal_admission=predecessor,
        )
        if (
            type(capability) is not VerifiedLiveNativeTerminal
            or capability.admission.run_state != lifecycle.terminal
            or capability.admission.invocation != lifecycle.invocation
        ):
            raise NativeTerminalResolverError(
                "native admission differs from the exact supplied child lifecycle"
            )
        self._live[(skill_ref, child_run_id)] = capability
        return _roots(capability)

    def reload(
        self,
        *,
        skill_ref: str,
        child_run_id: UUID,
        evidence: NativeTerminalEvidenceRoots,
    ) -> VerifiedNativeTerminalReload:
        """Strictly reverify persisted roots without granting live authority."""

        entry = self._entry(skill_ref)
        if type(evidence) is not NativeTerminalEvidenceRoots:
            raise TypeError("terminal evidence requires exact typed roots")
        predecessor = None
        if skill_ref == "genesis.observe@3.0.0":
            admission = _read_model(
                self._store,
                evidence.admission,
                NativeTerminalAdmissionV1,
            )
            predecessor_ref = admission.predecessor_terminal_admission
            predecessor = self._reloaded.get(predecessor_ref)
            if type(predecessor) is not VerifiedNativeTerminalReload:
                raise NativeTerminalResolverError(
                    "observation reload requires its reverified replay predecessor"
                )
        capability = reload_native_terminal(
            artifact_store=self._store,
            admission_ref=evidence.admission,
            child_authority=entry.authority,
            expected_skill_ref=skill_ref,
            expected_run_id=child_run_id,
            qualification_admission=entry.qualification,
            predecessor_terminal_admission=predecessor,
        )
        if (
            type(capability) is not VerifiedNativeTerminalReload
            or capability.admission.read_closure != evidence.read_closure
        ):
            raise NativeTerminalResolverError("terminal evidence roots changed during reload")
        self._reloaded[capability.admission_ref] = capability
        return capability

    def read_member(
        self,
        *,
        skill_ref: str,
        child_run_id: UUID,
        evidence: NativeTerminalEvidenceRoots,
        sha256: str,
    ) -> NativeClosureMember:
        """Authorize and return one member named by a verified terminal read index."""

        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ValueError("closure member requires a lowercase SHA-256 identity")
        capability = self.reload(
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            evidence=evidence,
        )
        index = _read_model(
            self._store,
            capability.admission.read_closure,
            NativeReadClosureV1,
        )
        artifacts = tuple(ref for ref in index.artifacts if ref.sha256 == sha256)
        if len(artifacts) > 1:
            raise NativeTerminalResolverError("closure member SHA has ambiguous artifact metadata")
        artifact = artifacts[0] if artifacts else None
        digest = next((member for member in index.digest_members if member.sha256 == sha256), None)
        if artifact is None and digest is None:
            raise LookupError("requested object is not a verified native read-closure member")
        if artifact is not None:
            path = self._store.resolve(artifact).path
            expected_bytes = artifact.bytes
        else:
            assert digest is not None
            path = self._store.resolve_digest(digest.sha256)
            expected_bytes = digest.bytes
        payload = path.read_bytes()
        if len(payload) != expected_bytes or hashlib.sha256(payload).hexdigest() != sha256:
            raise NativeTerminalResolverError("closure member bytes changed after authorization")
        return NativeClosureMember(
            sha256=sha256,
            bytes=expected_bytes,
            artifact=artifact,
            payload=payload,
        )

    def resolve_artifact_member(
        self,
        *,
        skill_ref: str,
        child_run_id: UUID,
        evidence: NativeTerminalEvidenceRoots,
        sha256: str,
    ) -> ArtifactRef | None:
        return self.read_member(
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            evidence=evidence,
            sha256=sha256,
        ).artifact

    def _entry(self, skill_ref: str) -> _ResolverEntry:
        if type(skill_ref) is not str or skill_ref not in self._entries:
            raise NativeTerminalResolverError(f"unsupported native terminal Skill: {skill_ref}")
        return self._entries[skill_ref]

    @staticmethod
    def _require_lifecycle(
        entry: _ResolverEntry,
        lifecycle: ControlledRunLifecycle,
        child_run_id: UUID,
    ) -> None:
        if type(lifecycle) is not ControlledRunLifecycle:
            raise TypeError("terminal resolver requires an exact controlled lifecycle")
        if (
            lifecycle.intent is None
            or lifecycle.invocation is None
            or lifecycle.terminal is None
            or lifecycle.terminal.status is not RunStatus.SUCCEEDED
            or lifecycle.terminal.run_id != child_run_id
            or entry.authority.read_controlled_lifecycle(child_run_id) != lifecycle
        ):
            raise NativeTerminalResolverError(
                "terminal resolver requires the exact successful child lifecycle"
            )

    def _admit_replay(self, replay_run_id: UUID) -> VerifiedLiveNativeTerminal:
        entry = self._entry("image2env.replay@2.0.0")
        lifecycle = entry.authority.read_controlled_lifecycle(replay_run_id)
        self._require_lifecycle(entry, lifecycle, replay_run_id)
        capability = admit_native_terminal(
            application=entry.application,
            artifact_store=self._store,
            run_id=replay_run_id,
            qualification_admission=entry.qualification,
            child_authority=entry.authority,
        )
        if type(capability) is not VerifiedLiveNativeTerminal:
            raise NativeTerminalResolverError("replay terminal retry did not grant live authority")
        self._live[(entry.binding.skill_ref, replay_run_id)] = capability
        return capability


def create_native_image_execution_binding_set(
    *,
    bindings: tuple[GoldenExecutionBinding, ...],
) -> NativeImageExecutionBindings:
    """Seal four rich native bindings after checking exact app/store/SQLite identity."""

    if type(bindings) is not tuple or len(bindings) != len(_SKILLS):
        raise TypeError("native image execution requires an exact four-binding tuple")
    stores: list[LocalArtifactStore] = []
    for binding, skill_ref, application_type in zip(
        bindings,
        _SKILLS,
        _APPLICATION_TYPES,
        strict=True,
    ):
        checked = _require_native_image_execution_binding(binding)
        adapter = checked.application
        authority = checked.child_authority
        policy = checked.policy
        if (
            checked.skill_ref != skill_ref
            or type(adapter) is not _NativeApplicationAdapter
            or type(adapter.application) is not application_type
            or type(authority) is not SQLiteRunStore
            or type(policy.artifact_store) is not LocalArtifactStore
            or _application_store(adapter.application) is not policy.artifact_store
        ):
            raise TypeError("native binding set requires four exact application authorities")
        verify_same_native_sqlite_authority(policy.child_authority, authority)
        verify_same_native_sqlite_authority(
            _application_authority(adapter.application),
            authority,
        )
        stores.append(policy.artifact_store)
    if any(store is not stores[0] for store in stores[1:]):
        raise ValueError("native terminal bindings must share one exact CAS store instance")
    return NativeImageExecutionBindings(bindings, stores[0], token=_BINDING_SET_TOKEN)


def create_native_terminal_resolver(
    *,
    native_bindings: NativeImageExecutionBindings,
    qualification_admissions: tuple[VerifiedTypedQualificationAdmission, ...],
) -> NativeTerminalResolver:
    """Bind four verified typed qualifications to four exact native authorities."""

    if (
        type(native_bindings) is not NativeImageExecutionBindings
        or not native_bindings._is_factory_set()
    ):
        raise TypeError("resolver requires sealed native image execution bindings")
    if type(qualification_admissions) is not tuple or len(qualification_admissions) != len(_SKILLS):
        raise TypeError("resolver requires an exact four-admission tuple")
    entries: dict[str, _ResolverEntry] = {}
    identities = []
    for binding, qualification, skill_ref in zip(
        native_bindings.bindings,
        qualification_admissions,
        _SKILLS,
        strict=True,
    ):
        if (
            type(qualification) is not VerifiedTypedQualificationAdmission
            or not qualification._is_verifier_issued()
            or qualification.admission.skill_ref != skill_ref
        ):
            raise TypeError("resolver requires four exact verified typed admissions")
        reverified = verify_typed_qualification_admission(
            artifact_store=native_bindings.artifact_store,
            admission=qualification.admission,
        )
        if (
            reverified.admission != qualification.admission
            or reverified.admission_ref != qualification.admission_ref
        ):
            raise NativeTerminalResolverError("typed qualification changed during resolver seal")
        adapter = binding.application
        authority = binding.child_authority
        assert type(adapter) is _NativeApplicationAdapter
        assert type(authority) is SQLiteRunStore
        entries[skill_ref] = _ResolverEntry(
            binding=binding,
            application=adapter.application,
            authority=authority,
            qualification=qualification,
        )
        identities.append(qualification.admission_ref.model_dump(mode="json"))
    identity_sha256 = hashlib.sha256(
        _canonical_bytes(
            {
                "domain": "harness.native_terminal_resolver.identity.v1",
                "qualification_admissions": identities,
                "skills": list(_SKILLS),
            }
        )
    ).hexdigest()
    return NativeTerminalResolver(
        binding_set=native_bindings,
        entries=entries,
        identity_sha256=identity_sha256,
        token=_RESOLVER_TOKEN,
    )


def verify_same_native_sqlite_authority(
    first: SQLiteRunStore,
    second: SQLiteRunStore,
) -> None:
    """Require two SQLite handles to name one canonical, non-symlink authority file."""

    if type(first) is not SQLiteRunStore or type(second) is not SQLiteRunStore:
        raise TypeError("native SQLite authority comparison requires exact stores")
    if _sqlite_identity(first) != _sqlite_identity(second):
        raise ValueError("native SQLite handles belong to different authority files")


def _application_store(application: object) -> LocalArtifactStore:
    if type(application) is Image2EnvGenesisCompileApplication:
        return application._store
    if type(application) is GenesisSceneApplication:
        return application.producer.artifact_store
    return application.store


def _application_authority(application: object) -> SQLiteRunStore:
    if type(application) is Image2EnvGenesisCompileApplication:
        return application._run_store
    return application.run_store


def _sqlite_identity(authority: SQLiteRunStore) -> tuple[str, int, int]:
    path = authority.path
    if not path.is_absolute():
        raise ValueError("native SQLite authority path must be absolute")
    resolved = path.resolve(strict=True)
    metadata = path.lstat()
    if path != resolved or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("native SQLite authority must be one canonical regular file")
    return str(resolved), metadata.st_dev, metadata.st_ino


def _roots(
    capability: VerifiedLiveNativeTerminal | VerifiedNativeTerminalReload,
) -> NativeTerminalEvidenceRoots:
    return NativeTerminalEvidenceRoots(
        admission=capability.admission_ref,
        read_closure=capability.admission.read_closure,
    )


def _read_model(store: LocalArtifactStore, ref: ArtifactRef, model):
    payload = store.resolve(ref).path.read_bytes()
    try:
        parsed = model.model_validate_json(payload)
    except ValidationError as error:
        raise NativeTerminalResolverError("native terminal evidence is invalid") from error
    if payload != _canonical_bytes(parsed):
        raise NativeTerminalResolverError("native terminal evidence is not canonical JSON")
    return parsed


def _canonical_bytes(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_ref(ref: ArtifactRef, schema: str, label: str) -> None:
    if (
        type(ref) is not ArtifactRef
        or ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.media_type != "application/json"
        or ref.schema_version != schema
    ):
        raise ValueError(f"{label} requires an exact typed CAS reference")


__all__ = [
    "NativeClosureMember",
    "NativeImageExecutionBindings",
    "NativeTerminalEvidenceRoots",
    "NativeTerminalResolver",
    "NativeTerminalResolverError",
    "create_native_image_execution_binding_set",
    "create_native_terminal_resolver",
    "verify_same_native_sqlite_authority",
]

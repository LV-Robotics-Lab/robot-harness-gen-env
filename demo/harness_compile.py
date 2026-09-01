"""Synchronous Workbench submission over one fixed compile authority."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from demo.harness_feed import HarnessEventFeed, HarnessEventFeedCorruptionError
from scene_gen.schema import SceneSpec
from self_improving.harness.application import CompileApplication
from self_improving.harness.event_journal import EventPage
from self_improving.harness.registry import _invocation_digest
from self_improving.harness.schemas import (
    ArtifactRef,
    Invocation,
    RunState,
    RunStatus,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
)

_SUBMISSION_SCHEMA = "harness.workbench_compile_submission.v1"
_AUDIT_SCHEMA = "harness.workbench_compile_audit.v2"
_SCENE_PREVIEW_SCHEMA = "harness.workbench_compile_scene_preview.v1"
_SCENE_SPEC_SCHEMA = "robotwin.scene_spec.v1"
_SCENE_PREVIEW_MAX_BYTES = 65_536
_SCENE_PREVIEW_MAX_DEPTH = 64
_COMPILE_SKILL_ID = "text2env.compile"
_COMPILE_SKILL_VERSION = "1.0.0"
_COMPILE_DEPENDENCY_VERSIONS = (
    ("asset-library-state", "1"),
    ("catalog-selected-assets", "1"),
    ("ledger-contract", "1"),
    ("scene-gen", "0.1.0"),
    ("text2env-compile-config", "1"),
)
_SAFE_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_ARTIFACT_MEDIA_TYPE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}/[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}"
)
_SAFE_ARTIFACT_SCHEMA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_JAVASCRIPT_SAFE_INTEGER_MAX = 9_007_199_254_740_991


class WorkbenchCompileInputError(ValueError):
    """The browser-facing business input is outside the fixed compile contract."""


class WorkbenchCompileUnavailableError(RuntimeError):
    """The fixed compile authority cannot accept work at present."""


class WorkbenchCompileAuthorityError(WorkbenchCompileUnavailableError):
    """Persisted RunState and journal history cannot prove one terminal result."""


class WorkbenchCompileRunNotFoundError(LookupError):
    """The requested terminal compile run does not exist in this authority."""


class WorkbenchCompileSceneNotPreviewableError(LookupError):
    """The terminal compile run has no successful typed SceneSpec output."""


class WorkbenchCompileSceneTooLargeError(ValueError):
    """The committed SceneSpec is outside the fixed preview size policy."""


@dataclass(frozen=True)
class _ArtifactAuthority:
    ref: ArtifactRef
    bindings: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _TerminalCompileAuthority:
    persisted: RunState
    invocation: Invocation | None
    history: EventPage
    typed_input: Text2EnvCompileInput | None
    typed_output: Text2EnvCompileOutput | None
    artifacts: tuple[_ArtifactAuthority, ...]


class WorkbenchCompile:
    """Hide compile trust policy behind a two-field synchronous submission seam.

    A successful return means the terminal ``RunState`` and its complete event
    history were both re-read from the application's shared SQLite authority.
    It does not claim physical validation or publishability.
    """

    def __init__(
        self,
        *,
        application: CompileApplication,
        asset_catalog_path: Path,
        generate_missing_assets: bool,
    ) -> None:
        if type(application) is not CompileApplication:
            raise TypeError("application must be the exact CompileApplication")
        if not isinstance(asset_catalog_path, Path):
            raise TypeError("asset_catalog_path must be a Path")
        if type(generate_missing_assets) is not bool:
            raise TypeError("generate_missing_assets must be a bool")
        self._application = application
        self._asset_catalog_path = asset_catalog_path.expanduser().resolve(strict=False)
        self._generate_missing_assets = generate_missing_assets
        self._feed = HarnessEventFeed(application.events)
        self._submit_lock = threading.Lock()

    def page(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Project events from the same authority that accepts submissions."""

        if type(after_event_id) is not int or not 0 <= after_event_id <= 2**63 - 1:
            raise ValueError("after_event_id must fit a nonnegative SQLite integer")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        if run_id is not None and not isinstance(run_id, UUID):
            raise ValueError("run_id must be a UUID")
        try:
            return self._feed.page(
                after_event_id=after_event_id,
                run_id=run_id,
                limit=limit,
            )
        except HarnessEventFeedCorruptionError:
            raise
        except Exception as error:
            raise HarnessEventFeedCorruptionError(
                "Harness event history failed integrity checks"
            ) from error

    def submit(self, *, request: object, seed: object) -> dict[str, Any]:
        """Run one real compile and return its verified durable terminal summary."""

        _validate_business_input(request=request, seed=seed)
        request_value = cast(str, request)
        seed_value = cast(int, seed)
        with self._submit_lock:
            try:
                returned = self._application.compile(
                    request=request_value,
                    seed=seed_value,
                    asset_catalog_path=self._asset_catalog_path,
                    generate_missing_assets=self._generate_missing_assets,
                )
            except Exception as error:
                raise WorkbenchCompileUnavailableError("Harness compile is unavailable") from error

            try:
                persisted = self._application.run_state(returned.run_id)
                history = self._application.events(
                    after_event_id=0,
                    run_id=returned.run_id,
                    limit=500,
                )
            except Exception as error:
                raise WorkbenchCompileAuthorityError(
                    "Harness compile authority failed integrity checks"
                ) from error

            _verify_terminal_closure(returned=returned, persisted=persisted, history=history)
            return {
                "schema_version": _SUBMISSION_SCHEMA,
                "run_id": str(persisted.run_id),
                "skill_id": persisted.skill_id,
                "skill_version": persisted.skill_version,
                "status": persisted.status.value,
                "attempt": persisted.attempt,
                "max_attempts": persisted.max_attempts,
                "terminal_event_id": str(history.events[-1].event_id),
                "blocker": _project_blocker(persisted),
            }

    def audit(self, *, run_id: UUID) -> dict[str, Any]:
        """Reconstruct one terminal run's dependency and event authority closure."""

        if type(run_id) is not UUID:
            raise WorkbenchCompileInputError("run_id must be a UUID")
        with self._submit_lock:
            authority = self._terminal_authority(run_id)
            artifacts = _project_artifacts(
                application=self._application,
                authority=authority.artifacts,
            )
            return _audit_projection(authority=authority, artifacts=artifacts)

    def scene_preview(self, *, run_id: UUID) -> dict[str, Any]:
        """Return one bounded, typed projection of a committed SceneSpec output."""

        if type(run_id) is not UUID:
            raise WorkbenchCompileInputError("run_id must be a UUID")
        with self._submit_lock:
            authority = self._terminal_authority(run_id)
            if (
                authority.persisted.status is not RunStatus.SUCCEEDED
                or authority.typed_input is None
                or authority.typed_output is None
                or authority.invocation is None
            ):
                raise WorkbenchCompileSceneNotPreviewableError(
                    "terminal compile run has no previewable SceneSpec"
                )
            scene_ref = authority.typed_output.scene_spec
            if (
                scene_ref.media_type != "application/json"
                or scene_ref.schema_version != _SCENE_SPEC_SCHEMA
            ):
                raise WorkbenchCompileAuthorityError("terminal SceneSpec failed integrity checks")
            if scene_ref.bytes > _SCENE_PREVIEW_MAX_BYTES:
                raise WorkbenchCompileSceneTooLargeError(
                    "terminal SceneSpec exceeds the fixed preview size"
                )
            try:
                scene_path = _scene_cas_path(application=self._application, ref=scene_ref)
                payload = _read_verified_scene_bytes(path=scene_path, ref=scene_ref)
                scene = _parse_scene_spec(payload)
                _verify_scene_bindings(
                    scene=scene,
                    typed_input=authority.typed_input,
                    typed_output=authority.typed_output,
                )
            except Exception as error:
                raise WorkbenchCompileAuthorityError(
                    "terminal SceneSpec failed integrity checks"
                ) from error

            try:
                confirmed = self._terminal_authority(run_id)
            except WorkbenchCompileRunNotFoundError as error:
                raise WorkbenchCompileAuthorityError(
                    "terminal compile authority changed during preview"
                ) from error
            if not _same_terminal_authority(authority, confirmed):
                raise WorkbenchCompileAuthorityError(
                    "terminal compile authority changed during preview"
                )
            scene_projection = scene.model_dump(
                mode="json",
                exclude={"request", "schema_version"},
            )
            return {
                "schema_version": _SCENE_PREVIEW_SCHEMA,
                "run": {
                    "run_id": str(authority.persisted.run_id),
                    "invocation_digest": authority.invocation.invocation_digest,
                    "event_count": len(authority.persisted.events),
                    "terminal_event_id": str(authority.history.last_event_id),
                },
                "artifact": _artifact_projection(
                    _ArtifactAuthority(
                        ref=scene_ref,
                        bindings=(("output", "scene_spec"),),
                    )
                ),
                "scene": scene_projection,
            }

    def _terminal_authority(self, run_id: UUID) -> _TerminalCompileAuthority:
        try:
            persisted = self._application.run_state(run_id)
            invocation = self._application.invocation(run_id)
            history = self._application.events(
                after_event_id=0,
                run_id=run_id,
                limit=500,
            )
            confirmed = self._application.run_state(run_id)
            confirmed_invocation = self._application.invocation(run_id)
            confirmed_history = self._application.events(
                after_event_id=0,
                run_id=run_id,
                limit=500,
            )
        except Exception as error:
            raise WorkbenchCompileAuthorityError(
                "Harness compile authority failed integrity checks"
            ) from error

        if persisted is None:
            if (
                confirmed is None
                and invocation is None
                and confirmed_invocation is None
                and _empty_event_page(history)
                and _empty_event_page(confirmed_history)
            ):
                raise WorkbenchCompileRunNotFoundError("terminal compile run was not found")
            raise WorkbenchCompileAuthorityError(
                "terminal RunState is missing from a partial run authority"
            )
        if (
            persisted.run_id != run_id
            or confirmed is None
            or _canonical_model_bytes(persisted) != _canonical_model_bytes(confirmed)
        ):
            raise WorkbenchCompileAuthorityError("terminal RunState changed during audit")
        if (invocation is None) != (confirmed_invocation is None) or (
            invocation is not None
            and confirmed_invocation is not None
            and _canonical_model_bytes(invocation) != _canonical_model_bytes(confirmed_invocation)
        ):
            raise WorkbenchCompileAuthorityError("Invocation changed during audit")
        if history != confirmed_history:
            raise WorkbenchCompileAuthorityError("event history changed during audit")
        _verify_terminal_history(persisted=persisted, history=history)

        typed_input = _typed_compile_input(persisted=persisted, invocation=invocation)
        typed_output = _typed_compile_output(persisted)
        input_artifacts = (typed_input.asset_catalog,) if typed_input is not None else ()
        artifacts = _artifact_authority(
            persisted=persisted,
            input_artifacts=input_artifacts,
            typed_output=typed_output,
        )
        return _TerminalCompileAuthority(
            persisted=persisted,
            invocation=invocation,
            history=history,
            typed_input=typed_input,
            typed_output=typed_output,
            artifacts=artifacts,
        )


def _validate_business_input(*, request: object, seed: object) -> None:
    if type(request) is not str or not 3 <= len(request) <= 2000:
        raise WorkbenchCompileInputError("request must contain 3-2000 characters")
    if type(seed) is not int or not 0 <= seed <= 2_147_483_647:
        raise WorkbenchCompileInputError("seed must be a nonnegative 32-bit integer")


def _verify_terminal_closure(
    *,
    returned: RunState,
    persisted: RunState | None,
    history: EventPage,
) -> None:
    if persisted is None:
        raise WorkbenchCompileAuthorityError("terminal RunState is missing")
    if _canonical_model_bytes(returned) != _canonical_model_bytes(persisted):
        raise WorkbenchCompileAuthorityError("returned and persisted RunState differ")
    _verify_terminal_history(persisted=persisted, history=history)


def _verify_terminal_history(*, persisted: RunState, history: EventPage) -> None:
    if (
        persisted.skill_id != _COMPILE_SKILL_ID
        or persisted.skill_version != _COMPILE_SKILL_VERSION
        or persisted.status is RunStatus.RUNNING
    ):
        raise WorkbenchCompileAuthorityError("terminal RunState identity is invalid")
    if persisted.invocation_digest is None:
        if (
            persisted.attempt != 0
            or persisted.max_attempts != 0
            or persisted.status not in {RunStatus.BLOCKED, RunStatus.FAILED}
        ):
            raise WorkbenchCompileAuthorityError(
                "preflight terminal RunState is invalid for fixed compile"
            )
    elif persisted.attempt != 1 or persisted.max_attempts != 1:
        raise WorkbenchCompileAuthorityError(
            "execution terminal RunState is invalid for fixed compile"
        )
    if (
        history.has_more
        or len(history.events) != len(persisted.events)
        or not history.events
        or history.last_event_id != history.events[-1].event_id
    ):
        raise WorkbenchCompileAuthorityError("terminal event history is incomplete")
    event_ids = [stored.event_id for stored in history.events]
    if event_ids[0] <= 0 or any(
        current <= previous for previous, current in zip(event_ids, event_ids[1:])
    ):
        raise WorkbenchCompileAuthorityError("terminal event cursors are not strictly increasing")
    expected_attempt = 0 if persisted.invocation_digest is None else 1
    if any(event.attempt != expected_attempt for event in persisted.events):
        raise WorkbenchCompileAuthorityError(
            "terminal event attempts are invalid for fixed compile"
        )
    for stored, expected in zip(history.events, persisted.events, strict=True):
        envelope = stored.envelope
        if (
            envelope.run_id != persisted.run_id
            or envelope.skill_id != persisted.skill_id
            or envelope.skill_version != persisted.skill_version
            or _canonical_model_bytes(envelope.event) != _canonical_model_bytes(expected)
        ):
            raise WorkbenchCompileAuthorityError("terminal event history differs from RunState")


def _project_blocker(state: RunState) -> dict[str, Any] | None:
    if state.blocker is None:
        return None
    return {
        "code": state.blocker.code,
        "retryable": state.blocker.retryable,
    }


def _typed_compile_input(
    *,
    persisted: RunState,
    invocation: Invocation | None,
) -> Text2EnvCompileInput | None:
    if persisted.invocation_digest is None:
        if invocation is not None:
            raise WorkbenchCompileAuthorityError(
                "preflight terminal state has an invalid Invocation binding"
            )
        return None
    if invocation is None or (
        invocation.run_id != persisted.run_id
        or invocation.skill_id != persisted.skill_id
        or invocation.skill_version != persisted.skill_version
        or invocation.invocation_digest != persisted.invocation_digest
        or invocation.max_attempts != persisted.max_attempts
    ):
        raise WorkbenchCompileAuthorityError("terminal RunState has an invalid Invocation binding")
    dependency_versions = tuple(
        (dependency.name, dependency.version) for dependency in invocation.dependencies
    )
    if dependency_versions != _COMPILE_DEPENDENCY_VERSIONS:
        raise WorkbenchCompileAuthorityError(
            "terminal Invocation has an invalid dependency order or version"
        )
    try:
        typed_input = Text2EnvCompileInput.model_validate(invocation.effective_parameters)
        expected_digest = _invocation_digest(
            skill_id=invocation.skill_id,
            skill_version=invocation.skill_version,
            effective_parameters=typed_input,
            dependencies=invocation.dependencies,
            max_attempts=invocation.max_attempts,
        )
    except Exception as error:
        raise WorkbenchCompileAuthorityError(
            "terminal Invocation content identity is invalid"
        ) from error
    if invocation.invocation_digest != expected_digest:
        raise WorkbenchCompileAuthorityError("terminal Invocation content identity is invalid")
    return typed_input


def _typed_compile_output(persisted: RunState) -> Text2EnvCompileOutput | None:
    if persisted.status is not RunStatus.SUCCEEDED:
        return None
    try:
        return Text2EnvCompileOutput.model_validate(persisted.output)
    except Exception as error:
        raise WorkbenchCompileAuthorityError("terminal compile output is invalid") from error


def _artifact_authority(
    *,
    persisted: RunState,
    input_artifacts: tuple[ArtifactRef, ...],
    typed_output: Text2EnvCompileOutput | None,
) -> tuple[_ArtifactAuthority, ...]:
    try:
        if typed_output is not None:
            output_artifacts = (
                typed_output.scene_spec,
                typed_output.resolved_scene,
                typed_output.environment_package.asset_catalog,
                typed_output.environment_package.package_manifest,
                typed_output.static_validation,
            )
            output_bindings = (
                (typed_output.scene_spec, ("output", "scene_spec")),
                (typed_output.resolved_scene, ("output", "resolved_scene")),
                (
                    typed_output.environment_package.asset_catalog,
                    ("output", "environment_package.asset_catalog"),
                ),
                (
                    typed_output.environment_package.package_manifest,
                    ("output", "environment_package.package_manifest"),
                ),
                (
                    typed_output.static_validation,
                    ("output", "static_validation"),
                ),
            )
        else:
            output_artifacts = ()
            output_bindings = ()
        event_artifacts = tuple(
            artifact for event in persisted.events for artifact in event.artifact_refs
        )
        blocker_artifacts = persisted.blocker.artifact_refs if persisted.blocker is not None else ()
        if persisted.invocation_digest is None and (
            persisted.artifacts or event_artifacts or blocker_artifacts
        ):
            raise ValueError("fixed compile preflight cannot publish artifacts")
        if len(persisted.artifacts) > 500:
            raise ValueError("terminal compile artifact inventory is too large")
        event_identities = {_artifact_identity(artifact) for artifact in event_artifacts}
        state_identities = [_artifact_identity(artifact) for artifact in persisted.artifacts]
        state_identity_set = set(state_identities)
        if len(state_identities) != len(state_identity_set):
            raise ValueError("terminal compile artifact identities are duplicated")
        if persisted.status is RunStatus.SUCCEEDED and any(
            _artifact_identity(artifact) not in state_identity_set for artifact in input_artifacts
        ):
            raise ValueError("terminal compile input artifact identity is missing")
        bindings_by_identity: dict[tuple[str, str | None, str], list[tuple[str, str]]] = {}
        for artifact in input_artifacts:
            identity = _artifact_identity(artifact)
            if identity in state_identity_set:
                bindings_by_identity.setdefault(identity, []).append(("input", "asset_catalog"))
        for artifact, binding in output_bindings:
            bindings_by_identity.setdefault(_artifact_identity(artifact), []).append(binding)
        if persisted.events[-1].artifact_refs != persisted.artifacts:
            raise ValueError("terminal event artifacts differ from terminal compile artifacts")
        authorized_event_refs = (
            *persisted.artifacts,
            *input_artifacts,
            *output_artifacts,
            *blocker_artifacts,
        )
        if event_identities != state_identity_set or any(
            artifact not in authorized_event_refs for artifact in event_artifacts
        ):
            raise ValueError("terminal compile artifacts differ from committed event artifacts")
        required_artifacts = (*output_artifacts, *blocker_artifacts)
        if any(artifact not in persisted.artifacts for artifact in required_artifacts):
            raise ValueError("terminal compile artifact bindings are incomplete")
        for artifact in dict.fromkeys(
            (*persisted.artifacts, *event_artifacts, *input_artifacts, *output_artifacts)
        ):
            _require_safe_artifact_metadata(artifact)
        return tuple(
            _ArtifactAuthority(
                ref=artifact,
                bindings=tuple(bindings_by_identity.get(_artifact_identity(artifact), ())),
            )
            for artifact in persisted.artifacts
        )
    except Exception as error:
        raise WorkbenchCompileAuthorityError(
            "terminal compile artifact inventory failed integrity checks"
        ) from error


def _project_artifacts(
    *,
    application: CompileApplication,
    authority: tuple[_ArtifactAuthority, ...],
) -> list[dict[str, Any]]:
    try:
        for item in authority:
            application.resolve_artifact(item.ref)
        return [_artifact_projection(item) for item in authority]
    except Exception as error:
        raise WorkbenchCompileAuthorityError(
            "terminal compile artifact inventory failed integrity checks"
        ) from error


def _artifact_projection(item: _ArtifactAuthority) -> dict[str, Any]:
    return {
        "name": item.ref.name,
        "media_type": item.ref.media_type,
        "schema_version": item.ref.schema_version,
        "sha256": item.ref.sha256,
        "bytes": str(item.ref.bytes),
        "bindings": [{"direction": direction, "role": role} for direction, role in item.bindings],
    }


def _audit_projection(
    *,
    authority: _TerminalCompileAuthority,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    persisted = authority.persisted
    invocation = authority.invocation
    if invocation is None:
        invocation_projection = {
            "status": "not_created_preflight",
            "digest": None,
            "dependencies": [],
        }
    else:
        invocation_projection = {
            "status": "bound",
            "digest": invocation.invocation_digest,
            "dependencies": [
                dependency.model_dump(mode="json") for dependency in invocation.dependencies
            ],
        }
    serialized_state = persisted.model_dump(mode="json")
    return {
        "schema_version": _AUDIT_SCHEMA,
        "run": {
            "run_id": str(persisted.run_id),
            "skill_id": persisted.skill_id,
            "skill_version": persisted.skill_version,
            "status": persisted.status.value,
            "attempt": persisted.attempt,
            "max_attempts": persisted.max_attempts,
            "started_at": serialized_state["started_at"],
            "ended_at": serialized_state["ended_at"],
            "event_count": len(persisted.events),
            "terminal_event_id": str(authority.history.last_event_id),
            "blocker": _project_blocker(persisted),
        },
        "invocation": invocation_projection,
        "artifacts": artifacts,
    }


def _read_verified_scene_bytes(*, path: Path, ref: ArtifactRef) -> bytes:
    if ref.bytes > _SCENE_PREVIEW_MAX_BYTES:
        raise WorkbenchCompileSceneTooLargeError(
            "terminal SceneSpec exceeds the fixed preview size"
        )
    try:
        required_flags = os.O_NOFOLLOW | os.O_NONBLOCK
    except AttributeError as error:
        raise ValueError("required descriptor flags are unavailable") from error
    flags = os.O_RDONLY | required_flags | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("terminal SceneSpec is not a regular CAS object")
        if before.st_size != ref.bytes or before.st_size > _SCENE_PREVIEW_MAX_BYTES:
            raise ValueError("terminal SceneSpec byte count changed before preview")
        chunks: list[bytes] = []
        remaining = _SCENE_PREVIEW_MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity:
            raise ValueError("terminal SceneSpec changed during preview")
        if len(payload) != ref.bytes or len(payload) > _SCENE_PREVIEW_MAX_BYTES:
            raise ValueError("terminal SceneSpec byte count changed during preview")
        if hashlib.sha256(payload).hexdigest() != ref.sha256:
            raise ValueError("terminal SceneSpec digest changed during preview")
        return payload
    finally:
        os.close(descriptor)


def _scene_cas_path(*, application: CompileApplication, ref: ArtifactRef) -> Path:
    """Locate the fixed CAS leaf without invoking the unbounded legacy resolver."""

    return application.artifact_root / "sha256" / ref.sha256[:2] / ref.sha256


def _parse_scene_spec(payload: bytes) -> SceneSpec:
    text = payload.decode("utf-8")
    if text.startswith("\ufeff"):
        raise ValueError("terminal SceneSpec must not contain a byte-order mark")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("terminal SceneSpec contains a duplicate JSON member")
            value[key] = item
        return value

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("terminal SceneSpec contains a non-finite number")
        return parsed

    def reject_constant(_value: str) -> None:
        raise ValueError("terminal SceneSpec contains a non-standard JSON constant")

    try:
        document = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_float=finite_float,
            parse_constant=reject_constant,
        )
        if type(document) is not dict:
            raise ValueError("terminal SceneSpec root must be an object")
        if _json_depth(document) > _SCENE_PREVIEW_MAX_DEPTH:
            raise ValueError("terminal SceneSpec nesting is too deep")
        scene = SceneSpec.model_validate(document)
        if not _same_strict_json_value(document, scene.canonical_dict()):
            raise ValueError("terminal SceneSpec JSON types or defaults are not canonical")
        return scene
    except (RecursionError, TypeError, ValueError) as error:
        raise ValueError("terminal SceneSpec is invalid") from error


def _json_depth(value: Any, depth: int = 1) -> int:
    if isinstance(value, dict):
        return max((depth, *(_json_depth(item, depth + 1) for item in value.values())))
    if isinstance(value, list):
        return max((depth, *(_json_depth(item, depth + 1) for item in value)))
    return depth


def _same_strict_json_value(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _same_strict_json_value(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _same_strict_json_value(left, right)
            for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


def _verify_scene_bindings(
    *,
    scene: SceneSpec,
    typed_input: Text2EnvCompileInput,
    typed_output: Text2EnvCompileOutput,
) -> None:
    package = typed_output.environment_package
    if (
        scene.request != typed_input.request
        or scene.seed != typed_input.seed
        or scene.seed != package.seed
        or scene.digest() != package.scene_spec_sha256
    ):
        raise ValueError("terminal SceneSpec semantic bindings are invalid")


def _same_terminal_authority(
    first: _TerminalCompileAuthority,
    second: _TerminalCompileAuthority,
) -> bool:
    return (
        _canonical_model_bytes(first.persisted) == _canonical_model_bytes(second.persisted)
        and (
            first.invocation is None
            and second.invocation is None
            or first.invocation is not None
            and second.invocation is not None
            and _canonical_model_bytes(first.invocation)
            == _canonical_model_bytes(second.invocation)
        )
        and first.history == second.history
    )


def _artifact_identity(artifact: ArtifactRef) -> tuple[str, str | None, str]:
    return artifact.media_type, artifact.schema_version, artifact.sha256


def _require_safe_artifact_metadata(artifact: ArtifactRef) -> None:
    if type(artifact.name) is not str or _SAFE_ARTIFACT_NAME.fullmatch(artifact.name) is None:
        raise ValueError("compile artifact name is not safe to project")
    if (
        type(artifact.media_type) is not str
        or _SAFE_ARTIFACT_MEDIA_TYPE.fullmatch(artifact.media_type) is None
    ):
        raise ValueError("compile artifact media type is not safe to project")
    if artifact.schema_version is not None and (
        type(artifact.schema_version) is not str
        or _SAFE_ARTIFACT_SCHEMA.fullmatch(artifact.schema_version) is None
    ):
        raise ValueError("compile artifact schema version is not safe to project")
    if (
        type(artifact.bytes) is not int
        or artifact.bytes < 0
        or artifact.bytes > _JAVASCRIPT_SAFE_INTEGER_MAX
    ):
        raise ValueError("compile artifact byte count is invalid")
    if type(artifact.sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", artifact.sha256) is None:
        raise ValueError("compile artifact digest is invalid")
    if artifact.uri != f"artifact://sha256/{artifact.sha256}":
        raise ValueError("compile artifact is not in the application CAS")


def _empty_event_page(page: EventPage) -> bool:
    return not page.events and page.last_event_id == 0 and not page.has_more


def _canonical_model_bytes(model: Any) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

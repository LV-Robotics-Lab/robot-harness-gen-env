"""Run the checked-in replay qualification's fixed case on its bound deployment."""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from self_improving.harness.artifacts import ArtifactResolutionError, LocalArtifactStore
from self_improving.harness.event_journal import EventPage
from self_improving.harness.media_sandbox import SandboxError
from self_improving.harness.qualification import QualificationBundleError
from self_improving.harness.registry import RunPersistenceError
from self_improving.harness.replay_application import (
    ReplayApplicationConfigurationError,
    ReplayApplicationSettings,
    create_replay_application,
)
from self_improving.harness.replay_qualification import load_replay_qualification_bundle
from self_improving.harness.runtime_executor import RuntimeExecutorConfigurationError
from self_improving.harness.schemas import (
    Invocation,
    RunState,
    RunStatus,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)

_CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
_DISTRIBUTION_ROOT = _CHECKOUT_ROOT
_SCENE_GEN_ROOT = _DISTRIBUTION_ROOT / "scene_gen"
_LEDGER_CONTRACT_ROOT = (
    _DISTRIBUTION_ROOT / "self_improving/asset_pipeline/active/asset_reuse/lib"
)
_QUALIFICATION_BUNDLE_ROOT = (
    _DISTRIBUTION_ROOT / "self_improving/harness/qualified_skills/text2env.replay/1.0.0"
)
_RUNTIME_RUNNER = _DISTRIBUTION_ROOT / "script/run_scene_runtime.py"
_RUNTIME_MODULE_ROOT = _DISTRIBUTION_ROOT
_MAX_SETTINGS_BYTES = 64 * 1024
_SETTINGS_SCHEMA_VERSION = "harness.qualified_replay_fixed_case_launch.v1"
_SETTINGS_FIELDS = frozenset(
    {
        "schema_version",
        "state_root",
        "evidence_artifact_root",
        "allowed_asset_roots",
        "interpreter",
        "runtime_capability_path",
        "media_launcher",
        "static_ffmpeg",
        "delegated_cgroup_root",
        "runtime_timeout_seconds",
        "capability_timeout_seconds",
    }
)
_PATH_FIELDS = (
    "state_root",
    "evidence_artifact_root",
    "interpreter",
    "runtime_capability_path",
    "media_launcher",
    "static_ffmpeg",
    "delegated_cgroup_root",
)


@dataclass(frozen=True, slots=True)
class _LaunchSettings:
    state_root: Path
    evidence_artifact_root: Path
    allowed_asset_roots: tuple[Path, ...]
    interpreter: Path
    runtime_capability_path: Path
    media_launcher: Path
    static_ffmpeg: Path
    delegated_cgroup_root: Path
    runtime_timeout_seconds: float
    capability_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class _StateClaim:
    parent_fd: int
    directory_fd: int
    parent_identity: tuple[int, int, int]
    directory_identity: tuple[int, int, int]
    leaf_name: str


class _DurableResultError(RuntimeError):
    pass


class _InputError(ValueError):
    pass


class _StateClaimError(ValueError):
    pass


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(status)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _InputError("invalid command line")

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise _ParserExit(status)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="robot-harness-run-qualified-replay",
        description="Run the fixed qualified replay case on its evidence-bound deployment.",
        allow_abbrev=False,
    )
    parser.add_argument("--settings", action="append", required=True)
    try:
        arguments = parser.parse_args(argv)
        if len(arguments.settings) != 1:
            raise _InputError("invalid command line")
        settings_path = _settings_path(arguments.settings[0])
        settings = _load_settings(settings_path)
        _validate_launch_paths(settings)
    except _InputError as error:
        sys.stderr.write(f"robot-harness-run-qualified-replay: input error: {error}\n")
        return 78
    except _ParserExit as exit_request:
        return exit_request.status
    try:
        state, summary = _run_fixed_qualified_case(settings)
    except _StateClaimError as error:
        sys.stderr.write(f"robot-harness-run-qualified-replay: input error: {error}\n")
        return 78
    except (
        ArtifactResolutionError,
        QualificationBundleError,
        ReplayApplicationConfigurationError,
        RuntimeExecutorConfigurationError,
        SandboxError,
        ValidationError,
    ) as error:
        sys.stderr.write(
            "robot-harness-run-qualified-replay: configuration/trust/qualification error: "
            f"{type(error).__name__}\n"
        )
        return 78
    except RunPersistenceError as error:
        sys.stderr.write(
            f"robot-harness-run-qualified-replay: persistence error: {type(error).__name__}\n"
        )
        return 74
    except Exception as error:
        sys.stderr.write(
            f"robot-harness-run-qualified-replay: durable/internal error: {type(error).__name__}\n"
        )
        return 74
    sys.stdout.write(
        json.dumps(
            summary,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    return {
        RunStatus.SUCCEEDED: 0,
        RunStatus.BLOCKED: 10,
        RunStatus.FAILED: 20,
    }[state.status]


def _settings_path(value: str) -> Path:
    candidate = Path(value)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise _InputError("--settings must be a canonical absolute regular file") from error
    if (
        not candidate.is_absolute()
        or candidate.is_symlink()
        or candidate != resolved
        or not resolved.is_file()
    ):
        raise _InputError("--settings must be a canonical absolute regular file")
    return resolved


def _load_settings(path: Path) -> _LaunchSettings:
    try:
        value = json.loads(
            _read_stable_settings_bytes(path),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except _InputError:
        raise
    except (
        OSError,
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise _InputError(
            "settings must be strict JSON with the exact fixed launch fields"
        ) from error
    if (
        not isinstance(value, dict)
        or set(value) != _SETTINGS_FIELDS
        or value.get("schema_version") != _SETTINGS_SCHEMA_VERSION
    ):
        raise _InputError("settings must be strict JSON with the exact fixed launch fields")
    if any(type(value[name]) is not str or not value[name] for name in _PATH_FIELDS):
        raise _InputError("settings values must use strict path, root, and timeout types")
    roots = value["allowed_asset_roots"]
    if (
        type(roots) is not list
        or not roots
        or any(type(item) is not str or not item for item in roots)
    ):
        raise _InputError("settings values must use strict path, root, and timeout types")
    raw_timeouts = (
        value["runtime_timeout_seconds"],
        value["capability_timeout_seconds"],
    )
    if any(type(item) not in {int, float} for item in raw_timeouts):
        raise _InputError("settings values must use strict path, root, and timeout types")
    try:
        timeout_values = tuple(float(item) for item in raw_timeouts)
    except (OverflowError, ValueError) as error:
        raise _InputError(
            "settings values must use strict path, root, and timeout types"
        ) from error
    if any(not math.isfinite(item) or item <= 0 for item in timeout_values):
        raise _InputError("settings values must use strict path, root, and timeout types")
    return _LaunchSettings(
        state_root=Path(value["state_root"]),
        evidence_artifact_root=Path(value["evidence_artifact_root"]),
        allowed_asset_roots=tuple(Path(item) for item in roots),
        interpreter=Path(value["interpreter"]),
        runtime_capability_path=Path(value["runtime_capability_path"]),
        media_launcher=Path(value["media_launcher"]),
        static_ffmpeg=Path(value["static_ffmpeg"]),
        delegated_cgroup_root=Path(value["delegated_cgroup_root"]),
        runtime_timeout_seconds=timeout_values[0],
        capability_timeout_seconds=timeout_values[1],
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _read_stable_settings_bytes(path: Path) -> bytes:
    descriptor = -1
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        before_path = os.stat(path, follow_symlinks=False)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_SETTINGS_BYTES
            or _inode_identity(before_path) != _inode_identity(before)
        ):
            raise ValueError
        chunks: list[bytes] = []
        remaining = _MAX_SETTINGS_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        after_path = os.stat(path, follow_symlinks=False)
        if (
            len(payload) > _MAX_SETTINGS_BYTES
            or len(payload) != after.st_size
            or _stable_file_identity(before) != _stable_file_identity(after)
            or _inode_identity(after_path) != _inode_identity(after)
        ):
            raise ValueError
        return payload
    except (OSError, ValueError) as error:
        raise _InputError(
            "settings file must be one stable regular file no larger than 65536 bytes"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _inode_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validate_launch_paths(settings: _LaunchSettings) -> None:
    try:
        evidence = _existing_directory(settings.evidence_artifact_root)
        roots = tuple(_existing_directory(path) for path in settings.allowed_asset_roots)
        _ = (
            _existing_file(settings.interpreter),
            _existing_file(settings.runtime_capability_path),
            _existing_file(settings.media_launcher),
            _existing_file(settings.static_ffmpeg),
        )
        delegated = _existing_directory(settings.delegated_cgroup_root)
        state = settings.state_root
        state_parent = _existing_directory(state.parent)
        if (
            not state.is_absolute()
            or state.is_symlink()
            or state.exists()
            or state.parent != state_parent
            or state != state.resolve(strict=False)
            or roots != tuple(sorted(set(roots), key=lambda item: str(item)))
        ):
            raise ValueError
        excluded = (
            _DISTRIBUTION_ROOT,
            _SCENE_GEN_ROOT,
            _LEDGER_CONTRACT_ROOT,
            _QUALIFICATION_BUNDLE_ROOT,
            _RUNTIME_MODULE_ROOT,
            _RUNTIME_RUNNER.parent,
            evidence,
            *roots,
            delegated,
        )
        if any(state.is_relative_to(root) for root in excluded):
            raise ValueError
    except (OSError, RuntimeError, ValueError):
        raise _InputError(
            "settings paths must be canonical, non-overlapping, and state_root must be fresh"
        ) from None


def _existing_file(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not path.is_absolute() or path.is_symlink() or path != resolved or not path.is_file():
        raise ValueError
    return resolved


def _existing_directory(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not path.is_absolute() or path.is_symlink() or path != resolved or not path.is_dir():
        raise ValueError
    return resolved


def _run_fixed_qualified_case(
    settings: _LaunchSettings,
) -> tuple[RunState, dict[str, object]]:
    claim = _claim_state_root(settings.state_root)
    try:
        artifact_store = LocalArtifactStore(settings.evidence_artifact_root)
        loaded = load_replay_qualification_bundle(
            _QUALIFICATION_BUNDLE_ROOT,
            artifact_store=artifact_store,
            implementation_root=_DISTRIBUTION_ROOT,
            scene_gen_root=_SCENE_GEN_ROOT,
            ledger_contract_root=_LEDGER_CONTRACT_ROOT,
        )
        _verify_state_claim(settings.state_root, claim)
        application = create_replay_application(
            ReplayApplicationSettings(
                state_root=settings.state_root,
                qualification_bundle_root=_QUALIFICATION_BUNDLE_ROOT,
                evidence_artifact_root=settings.evidence_artifact_root,
                implementation_root=_DISTRIBUTION_ROOT,
                scene_gen_root=_SCENE_GEN_ROOT,
                ledger_contract_root=_LEDGER_CONTRACT_ROOT,
                allowed_asset_roots=settings.allowed_asset_roots,
                interpreter=settings.interpreter,
                runtime_runner=_RUNTIME_RUNNER,
                runtime_module_root=_RUNTIME_MODULE_ROOT,
                runtime_capability_path=settings.runtime_capability_path,
                media_launcher=settings.media_launcher,
                static_ffmpeg=settings.static_ffmpeg,
                delegated_cgroup_root=settings.delegated_cgroup_root,
                runtime_timeout_seconds=settings.runtime_timeout_seconds,
                capability_timeout_seconds=settings.capability_timeout_seconds,
            )
        )
        if application.skills != (loaded.descriptor,):
            raise _DurableResultError("application descriptor differs from loaded qualification")
        qualified_invocation = loaded.kernel_evaluation.invocation
        fixed_input = Text2EnvReplayInput.model_validate(qualified_invocation.effective_parameters)
        returned = application.replay(fixed_input)
        persisted = application.run_state(returned.run_id)
        invocation = application.invocation(returned.run_id)
        page = application.events(after_event_id=0, run_id=returned.run_id, limit=200)
        persisted_after_events = application.run_state(returned.run_id)
        invocation_after_events = application.invocation(returned.run_id)
        typed_output = _verify_durable_result(
            returned=returned,
            persisted=persisted,
            invocation=invocation,
            page=page,
            persisted_after_events=persisted_after_events,
            invocation_after_events=invocation_after_events,
            fixed_input=fixed_input,
            exact_dependencies=loaded.exact_dependencies.dependencies,
            qualified_invocation_digest=qualified_invocation.invocation_digest,
        )
        _verify_state_claim(settings.state_root, claim)
        blocker_code = persisted.blocker.code if persisted.blocker is not None else None
        summary: dict[str, object] = {
            "schema_version": "harness.qualified_replay_fixed_case_cli_summary.v1",
            "skill_ref": "text2env.replay@1.0.0",
            "case_id": loaded.case_binding.case_id,
            "qualification_report_sha256": loaded.generic.qualification.report_sha256,
            "implementation_sha256": loaded.generic.implementation_sha256,
            "environment_package_id": fixed_input.environment_package.package_id,
            "run_id": str(persisted.run_id),
            "invocation_digest": persisted.invocation_digest,
            "status": persisted.status.value,
            "attempt": persisted.attempt,
            "event_count": len(page.events),
            "artifact_count": len(persisted.artifacts),
            "blocker_code": blocker_code,
            "runtime_evidence_sha256": (
                typed_output.runtime_evidence.sha256 if typed_output is not None else None
            ),
        }
        return persisted, summary
    finally:
        _close_state_claim(claim)


def _verify_durable_result(
    *,
    returned: object,
    persisted: object,
    invocation: object,
    page: object,
    persisted_after_events: object,
    invocation_after_events: object,
    fixed_input: Text2EnvReplayInput,
    exact_dependencies: object,
    qualified_invocation_digest: object,
) -> Text2EnvReplayOutput | None:
    if (
        type(returned) is not RunState
        or returned.status is RunStatus.RUNNING
        or type(persisted) is not RunState
        or persisted != returned
        or persisted.skill_id != "text2env.replay"
        or persisted.skill_version != "1.0.0"
        or type(page) is not EventPage
        or page.has_more
        or len(page.events) != len(persisted.events)
        or type(persisted_after_events) is not RunState
        or persisted_after_events != persisted
    ):
        raise _DurableResultError(
            "durable replay result does not match the returned terminal state"
        )
    if persisted.attempt == 0:
        if (
            persisted.status is RunStatus.SUCCEEDED
            or persisted.max_attempts != 0
            or persisted.invocation_digest is not None
            or invocation is not None
            or invocation_after_events is not None
        ):
            raise _DurableResultError(
                "durable preflight result does not match its unbound terminal state"
            )
    elif (
        persisted.max_attempts != 2
        or type(invocation) is not Invocation
        or invocation.run_id != persisted.run_id
        or invocation.skill_id != "text2env.replay"
        or invocation.skill_version != "1.0.0"
        or invocation.max_attempts != 2
        or invocation.dependencies != exact_dependencies
        or invocation.invocation_digest != persisted.invocation_digest
        or invocation.invocation_digest != qualified_invocation_digest
        or invocation.effective_parameters != fixed_input.model_dump(mode="json")
        or type(invocation_after_events) is not Invocation
        or invocation_after_events != invocation
    ):
        raise _DurableResultError(
            "durable replay invocation does not match the qualified fixed case"
        )
    event_ids = tuple(item.event_id for item in page.events)
    if (
        event_ids != tuple(sorted(set(event_ids)))
        or (event_ids and page.last_event_id != event_ids[-1])
        or (not event_ids and page.last_event_id != 0)
        or any(
            item.envelope.run_id != persisted.run_id
            or item.envelope.skill_id != persisted.skill_id
            or item.envelope.skill_version != persisted.skill_version
            or item.envelope.event != persisted.events[index]
            for index, item in enumerate(page.events)
        )
    ):
        raise _DurableResultError("durable replay events do not match the terminal state")
    if persisted.status is not RunStatus.SUCCEEDED:
        return None
    try:
        return Text2EnvReplayOutput.model_validate(persisted.output)
    except ValidationError as error:
        raise _DurableResultError("succeeded replay output is not strictly typed") from error


def _claim_state_root(path: Path) -> _StateClaim:
    parent_fd = -1
    directory_fd = -1
    try:
        leaf_name = path.name
        if (
            leaf_name in {"", ".", ".."}
            or Path(leaf_name).name != leaf_name
            or path != path.parent / leaf_name
        ):
            raise OSError
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
        parent_fd = os.open(path.parent, flags)
        parent_metadata = os.fstat(parent_fd)
        parent_path_metadata = os.stat(path.parent, follow_symlinks=False)
        parent_identity = _inode_identity(parent_metadata)
        if not stat.S_ISDIR(parent_metadata.st_mode) or parent_identity != _inode_identity(
            parent_path_metadata
        ):
            raise OSError
        os.mkdir(leaf_name, mode=0o700, dir_fd=parent_fd)
        directory_fd = os.open(leaf_name, flags, dir_fd=parent_fd)
        directory_metadata = os.fstat(directory_fd)
        directory_identity = _inode_identity(directory_metadata)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_identity
            != _inode_identity(os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False))
            or directory_identity != _inode_identity(os.stat(path, follow_symlinks=False))
            or parent_identity != _inode_identity(os.stat(path.parent, follow_symlinks=False))
        ):
            raise OSError
        return _StateClaim(
            parent_fd=parent_fd,
            directory_fd=directory_fd,
            parent_identity=parent_identity,
            directory_identity=directory_identity,
            leaf_name=leaf_name,
        )
    except (OSError, ValueError) as error:
        _close_descriptors(directory_fd, parent_fd)
        raise _StateClaimError("state_root could not be claimed as fresh") from error


def _verify_state_claim(path: Path, claim: _StateClaim) -> None:
    try:
        parent_metadata = os.fstat(claim.parent_fd)
        directory_metadata = os.fstat(claim.directory_fd)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or _inode_identity(parent_metadata) != claim.parent_identity
            or _inode_identity(os.stat(path.parent, follow_symlinks=False)) != claim.parent_identity
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or _inode_identity(directory_metadata) != claim.directory_identity
            or _inode_identity(
                os.stat(
                    claim.leaf_name,
                    dir_fd=claim.parent_fd,
                    follow_symlinks=False,
                )
            )
            != claim.directory_identity
            or _inode_identity(os.stat(path, follow_symlinks=False)) != claim.directory_identity
        ):
            raise OSError
    except (OSError, ValueError) as error:
        raise _DurableResultError("state_root claim identity changed") from error


def _close_state_claim(claim: _StateClaim) -> None:
    _close_descriptors(claim.directory_fd, claim.parent_fd)


def _close_descriptors(*descriptors: int) -> None:
    for descriptor in descriptors:
        if descriptor < 0:
            continue
        try:
            os.close(descriptor)
        except OSError:
            pass

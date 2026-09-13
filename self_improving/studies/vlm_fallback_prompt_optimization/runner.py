"""Auditable execution runner for the preregistered VLM fallback study.

The runner owns validation, durable receipts, budgets, resume, and progress.  It
does not grant a VLM authority over deterministic geometry or runtime gates.
Model inference and typed routing live behind injected provider seams.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import selectors
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from scene_gen.rendered_critic import build_critic_prompt
from scene_gen.schema import ResolvedSceneSpec
from self_improving.studies.vlm_fallback_prompt_optimization import (
    amendment_bundle,
    model_content,
    protocol,
)

RUNNER_SOURCE_SCHEMA = "vlm_fallback.runner_source_manifest.v2"
REQUIRED_RUNNER_SOURCES = frozenset(
    {
        "self_improving/studies/vlm_fallback_prompt_optimization/runner.py",
        "self_improving/studies/vlm_fallback_prompt_optimization/protocol.py",
        "self_improving/studies/vlm_fallback_prompt_optimization/amendment.py",
        "self_improving/studies/vlm_fallback_prompt_optimization/amendment_bundle.py",
        "self_improving/studies/vlm_fallback_prompt_optimization/model_content.py",
        "self_improving/studies/vlm_fallback_prompt_optimization/annotations.py",
        "scene_gen/rendered_critic.py",
        "scene_gen/schema.py",
    }
)
ANCHOR_SCHEMA = "vlm_fallback.run_anchor.v1"
FROZEN_SPEC_SHA256 = "ad19d38204f20c42d8070785994fe749b8db41364e002e382e938cb92ae5bf6c"
FROZEN_AMENDMENT_ROOT_SHA256 = "5276894e83f3412f287b7e1b00e39f2eeb71cf51c52d55dafe49d7950a007dff"
FROZEN_EFFECTIVE_SPEC_SHA256 = "d8de1b0aa40f5337e9801f8b34941689fd3267c9bb7277908c83cc899e0dcfda"
FROZEN_PENDING_ANNOTATION_V3_SHA256 = (
    "1826c7633ddeacb5f43cbc4bfae540b409d2b50cf97f06275809b57d43e72bcf"
)
FROZEN_LOG_PREFIX_SHA256 = "141bc995ba391a11cea3461180f51936f5829aca0c13ef44e10b48d3ef27f6a4"
FROZEN_LOG_PREFIX_LINES = 6
AMENDMENT_COMMITMENT_SCHEMA = "vlm_fallback.amendment_commitment.v1"
RUNNER_SOURCE_COMMITMENT_SCHEMA = "vlm_fallback.runner_source_commitment.v2"
FROZEN_AMENDMENT_COMMITMENT_EVENT_SHA256 = (
    "933a504bb63913f4aab8a0badc63f7f2dee43275547a52f7095f93835e9a936b"
)
VISIBLE_SELECTION_SCHEMA = "vlm_fallback.visible_prompt_selection.v2"
VISIBLE_GOLD_SEAL_SCHEMA = "vlm_fallback.visible_gold_seal.v1"
VISIBLE_DEV_GOLD_SCHEMA = "vlm_fallback.visible_dev_gold.v1"
FORMAT_ONLY_REPAIR_TEMPLATE_VERSION = "vlm_fallback.format_only_repair.v1"
_FORMAT_ONLY_REPAIR_INSTRUCTION = (
    "FORMAT ONLY: reproduce exactly the same visible facts from the bound raw response "
    "using the required JSON schema. Do not add, delete, reinterpret, or infer facts."
)
FROZEN_SPEC_PATH = Path(__file__).with_name("experiment_spec.json").resolve()
FROZEN_EFFECTIVE_SPEC_PATH = (
    Path(__file__).with_name("amendments") / "01" / "experiment_spec.v2.json"
).resolve()
FROZEN_REPO_ROOT = Path(__file__).resolve().parents[3]
FROZEN_RUN_LOG_PATH = Path(__file__).with_name("run_log.jsonl").resolve()
FROZEN_RUNTIME_STATE_ROOT = Path(__file__).resolve().parent / "runs" / "runtime_state"
FROZEN_RUN_ANCHOR_PATH = FROZEN_RUNTIME_STATE_ROOT / "run_state.json"
FROZEN_RUN_PENDING_PATH = FROZEN_RUNTIME_STATE_ROOT / "run_pending.json"
FROZEN_RUN_LOCK_PATH = FROZEN_RUNTIME_STATE_ROOT / "run.lock"
FROZEN_RUNNER_SOURCE_MANIFEST_PATH = (
    Path(__file__).with_name("runner_source_manifest.json").resolve()
)
SEALED_TEST_ANNOTATION_MANIFEST_PATH = (
    Path(__file__).with_name("sealed_test_annotation_manifest.json").resolve()
)
# This commitment deliberately lives in reviewed runner source rather than in
# caller configuration.  The checked-in manifest is a public availability
# commitment only; until blinded labels are actually collected it cannot unlock
# sealed-test execution.
SEALED_TEST_ANNOTATION_MANIFEST_SHA256 = (
    "fba3590f5acde490c2ddbe803f785dba54f037a00207f1e8c149166a3ea15114"
)
PRODUCTION_INTENT_VERIFIER_ID = "vlm_fallback.intent_preservation.fixed.v1"
PRODUCTION_INTENT_VERIFIER_IMPLEMENTATION_SHA256 = hashlib.sha256(
    b"vlm_fallback.intent_preservation.fixed.v1.token_multiset_and_directed_relations"
).hexdigest()

_INTENT_RELATION_PATTERNS = tuple(
    sorted(
        (
            (("to", "the", "left", "of"), "left_of", False),
            (("to", "the", "right", "of"), "right_of", False),
            (("in", "front", "of"), "front_of", False),
            (("on", "top", "of"), "on_top_of", False),
            (("left", "of"), "left_of", False),
            (("right", "of"), "right_of", False),
            (("next", "to"), "next_to", True),
            (("inside",), "inside", False),
            (("into",), "inside", False),
            (("within",), "inside", False),
            (("behind",), "behind", False),
            (("under",), "under", False),
            (("below",), "under", False),
            (("above",), "above", False),
            (("near",), "near", True),
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_INTENT_CLAUSE_BOUNDARIES = frozenset({"and", "then", "while", "but"})
_INTENT_ENTITY_NOISE = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "of",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "place",
        "put",
        "set",
        "move",
        "position",
        "locate",
        "open",
        "close",
        "both",
        "object",
        "objects",
    }
)


class RunnerIntegrityError(RuntimeError):
    """Raised when frozen input or durable state no longer matches its receipt."""


class DuplicateInvocationError(RunnerIntegrityError):
    """Raised when a logical invocation key is reused with different inputs."""


class StudyStoppedError(RunnerIntegrityError):
    """Raised after a preregistered stopping rule becomes durable."""

    def __init__(self, reason: str, *, event_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.event_id = event_id


class ProviderContractError(RunnerIntegrityError):
    """Raised internally when a provider returns an unreceiptable shape."""


class ProviderPhysicalClaimError(ProviderContractError):
    """Raised when provider content tries to impersonate a physical gate."""


class ProviderFormatError(ProviderContractError):
    """Raised when a valid provider response needs at most one format-only repair."""


@dataclass(frozen=True)
class RunnerConfig:
    spec_path: Path
    repo_root: Path
    log_path: Path
    anchor_path: Path
    pending_path: Path
    lock_path: Path
    source_manifest_path: Path
    expected_source_manifest_sha256: str
    expected_log_prefix_sha256: str = FROZEN_LOG_PREFIX_SHA256
    expected_log_prefix_lines: int = FROZEN_LOG_PREFIX_LINES
    verify_artifacts: bool = True
    verify_models: bool = True
    allow_test_providers: bool = False
    execution_mode: str = "production"
    routing_provider_mode: str = "sandboxed_subprocess"


@dataclass(frozen=True)
class RunnerIdentity:
    study_id: str
    spec_sha256: str
    source_manifest_sha256: str
    verified_artifact_count: int
    model_identity_matches: int
    execution_mode: str
    verification_profile_sha256: str
    amendment_root_sha256: str
    model_content_manifest_sha256: tuple[tuple[str, str], ...]
    model_roster_sha256: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class _VerifiedModelBinding:
    manifest: dict[str, Any]
    snapshot: model_content.ModelContentSnapshot | None


@dataclass(frozen=True)
class _FrozenInputs:
    spec: dict[str, Any]
    identity: RunnerIdentity
    annotation_manifest: dict[str, Any]
    annotation_manifest_sha256: str
    model_bindings: Mapping[str, _VerifiedModelBinding]


@dataclass(frozen=True)
class ProgressUpdate:
    sequence: int
    timestamp_utc: str
    stage: str
    experiment_id: str | None = None
    case_id: str | None = None
    arm: str | None = None
    invocation_id: str | None = None
    completed: int | None = None
    total: int | None = None
    durable_event_id: str | None = None


ProgressCallback = Callable[[ProgressUpdate], None]


@dataclass(frozen=True)
class ProviderIdentity:
    provider_id: str
    revision: str
    implementation_sha256: str
    kind: str
    production_eligible: bool


@dataclass(frozen=True)
class OracleRoutingCapability:
    study_id: str
    spec_sha256: str
    purpose: str


@dataclass(frozen=True)
class ResourceUsage:
    gpu_time_ms: int = 0
    peak_vram_mib: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    network_calls: int = 0
    remote_paid_calls: int = 0
    compile_attempts: int = 0
    fresh_physical_replays: int = 0
    runtime_steps: int = 0
    contact_window_steps: int = 0
    prompt_rewrites: int = 0
    visible_vlm_invocations: int = 0


@dataclass(frozen=True)
class ProviderOutcome:
    decision: str
    result: Mapping[str, Any]
    raw_response: str | bytes
    parsed_response: Mapping[str, Any] | None
    abstained: bool
    resource: ResourceUsage
    claims_physical_pass: bool = False


@dataclass(frozen=True)
class IntentVerification:
    before_sha256: str
    after_sha256: str
    preserved: bool
    verifier_id: str
    verifier_implementation_sha256: str


class IntentPreservationVerifier(Protocol):
    """Fixed runner-owned verifier contract; arbitrary implementations are not accepted."""

    def verify(self, original_prompt: str, revised_prompt: str) -> IntentVerification: ...


class _ProductionIntentPreservationVerifier:
    """Conservative fixed verifier for rewrites.

    It permits only a rearrangement of the original lexical constraints.  This
    is intentionally narrower than natural-language paraphrase: a rewrite that
    drops, adds, or substitutes a task token is rejected rather than relying on
    a provider assertion about intent.
    """

    @staticmethod
    def _tokens(prompt: str) -> tuple[str, ...]:
        return tuple(re.findall(r"[\w]+", prompt.casefold(), flags=re.UNICODE))

    @staticmethod
    def _entity(tokens: Sequence[str]) -> tuple[str, ...]:
        value = [token for token in tokens if token not in _INTENT_ENTITY_NOISE]
        return tuple(value)

    @classmethod
    def _entity_candidates(
        cls,
        tokens: Sequence[str],
        relation_ranges: Sequence[tuple[int, int]],
    ) -> tuple[tuple[str, ...], ...]:
        separators = set(_INTENT_CLAUSE_BOUNDARIES)
        for start, end in relation_ranges:
            separators.update(tokens[start:end])
        candidates: list[tuple[str, ...]] = []
        chunk: list[str] = []
        for token in tokens:
            if token in separators:
                entity = cls._entity(chunk)
                if entity:
                    candidates.append(entity)
                chunk = []
            else:
                chunk.append(token)
        entity = cls._entity(chunk)
        if entity:
            candidates.append(entity)
        return tuple(candidates)

    @classmethod
    def _canonical_intent(cls, prompt: str) -> dict[str, Any]:
        tokens = cls._tokens(prompt)
        matches: list[tuple[int, int, str, bool]] = []
        index = 0
        while index < len(tokens):
            match = next(
                (
                    (pattern, name, symmetric)
                    for pattern, name, symmetric in _INTENT_RELATION_PATTERNS
                    if tuple(tokens[index : index + len(pattern)]) == pattern
                ),
                None,
            )
            if match is None:
                index += 1
                continue
            pattern, name, symmetric = match
            matches.append((index, index + len(pattern), name, symmetric))
            index += len(pattern)

        ranges = [(start, end) for start, end, _, _ in matches]
        candidates = cls._entity_candidates(tokens, ranges)
        relations: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        for match_index, (start, end, name, symmetric) in enumerate(matches):
            previous_end = matches[match_index - 1][1] if match_index else 0
            next_start = (
                matches[match_index + 1][0] if match_index + 1 < len(matches) else len(tokens)
            )
            left_segment = list(tokens[previous_end:start])
            right_segment = list(tokens[end:next_start])
            for boundary in _INTENT_CLAUSE_BOUNDARIES:
                if boundary in left_segment:
                    left_segment = left_segment[
                        len(left_segment) - left_segment[::-1].index(boundary) :
                    ]
                if boundary in right_segment:
                    right_segment = right_segment[: right_segment.index(boundary)]
            left = cls._entity(left_segment)
            right = cls._entity(right_segment)
            if not left and len(candidates) == 2:
                left = next((candidate for candidate in candidates if candidate != right), ())
            if not right and len(candidates) == 2:
                right = next((candidate for candidate in candidates if candidate != left), ())
            if symmetric and right < left:
                left, right = right, left
            relations.append((name, left, right))
        return {
            "tokens": sorted(tokens),
            "directed_relations": sorted(relations),
        }

    def verify(self, original_prompt: str, revised_prompt: str) -> IntentVerification:
        before_intent = self._canonical_intent(original_prompt)
        after_intent = self._canonical_intent(revised_prompt)
        before = _sha256_bytes(protocol.canonical_json_bytes(before_intent))
        after = _sha256_bytes(protocol.canonical_json_bytes(after_intent))
        return IntentVerification(
            before_sha256=before,
            after_sha256=after,
            preserved=before_intent == after_intent,
            verifier_id=PRODUCTION_INTENT_VERIFIER_ID,
            verifier_implementation_sha256=PRODUCTION_INTENT_VERIFIER_IMPLEMENTATION_SHA256,
        )


def canonical_intent_sha256(prompt: str) -> str:
    """Return the runner-owned intent digest providers must echo for a rewrite."""

    return _sha256_bytes(
        protocol.canonical_json_bytes(
            _ProductionIntentPreservationVerifier._canonical_intent(prompt)
        )
    )


@dataclass(frozen=True)
class ModelBinding:
    role: str
    model_id: str
    revision: str
    snapshot_manifest_sha256: str
    model_content_manifest_sha256: str
    model_roster_sha256: str | None
    local_snapshot_path: Path
    max_new_tokens: int


@dataclass(frozen=True)
class ArtifactBinding:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class VisibleInvocation:
    case_id: str
    arm: str
    prompt: str
    prompt_template_version: str
    processor_config: Mapping[str, Any]
    seed: int
    attempt: int
    resolved_scene_sha256: str | None = None
    resolved_scene_path: Path | None = None
    repair_index: int = 0
    repair_of: str | None = None
    repair_source_prompt: str | None = None
    repair_source_response: str | bytes | None = None
    resource_reservation: ResourceUsage = ResourceUsage(visible_vlm_invocations=1)


@dataclass(frozen=True)
class VisiblePromptFreeze:
    prompt: str
    prompt_template_version: str
    processor_config: Mapping[str, Any]
    selection_evidence_sha256: str
    selection_evidence_path: Path | None = None
    gold_seal_path: Path | None = None
    gold_seal_sha256: str | None = None
    dev_gold_manifest_path: Path | None = None


@dataclass(frozen=True)
class VisibleProviderRequest:
    invocation_id: str
    case_id: str
    arm: str
    task_context: str
    prompt: str
    prompt_template_version: str
    processor_config: Mapping[str, Any]
    seed: int
    attempt: int
    repair_index: int
    repair_of: str | None
    resolved_scene_sha256: str | None
    model: ModelBinding
    image_paths: tuple[Path, ...]
    artifacts: tuple[ArtifactBinding, ...]
    resource_reservation: ResourceUsage


ProviderProgress = Callable[[str], None]


class VisibleCriticProvider(Protocol):
    identity: ProviderIdentity

    def invoke(
        self,
        request: VisibleProviderRequest,
        progress: ProviderProgress,
    ) -> ProviderOutcome: ...


@dataclass(frozen=True)
class RoutingInvocation:
    case_id: str
    arm: str
    original_prompt: str
    routing_instruction: str
    prompt_template_version: str
    typed_failure: Mapping[str, Any]
    trusted_state: Mapping[str, Any]
    asset_availability: Mapping[str, Any]
    visible_report: Mapping[str, Any] | None
    seed: int
    attempt: int
    untyped_failure_summary: str | None = None
    resource_reservation: ResourceUsage = ResourceUsage()


@dataclass(frozen=True)
class RoutingProviderRequest:
    invocation_id: str
    case_id: str
    arm: str
    original_prompt: str
    routing_instruction: str
    prompt_template_version: str
    failure_code: str | None
    untyped_failure_summary: str | None
    typed_failure: Mapping[str, Any] | None
    trusted_state: Mapping[str, Any]
    asset_availability: Mapping[str, Any]
    visible_report: Mapping[str, Any] | None
    seed: int
    attempt: int
    allowed_routes: tuple[str, ...]
    gold_route: str | None
    resource_reservation: ResourceUsage


class TypedRoutingProvider(Protocol):
    identity: ProviderIdentity

    def invoke(
        self,
        request: RoutingProviderRequest,
        progress: ProviderProgress,
    ) -> ProviderOutcome: ...


SANDBOXED_ROUTING_OUTPUT_SCHEMA = "vlm_fallback.sandboxed_routing_output.v1"


@dataclass(frozen=True)
class SandboxedRoutingProvider:
    """Runner-owned, one-shot routing provider executed without repository access.

    The child receives one canonical request on stdin.  Its mount namespace
    contains system runtime files plus explicit read-only inputs, but never the
    repository, its Git history, caller environment, or network namespace.
    """

    identity: ProviderIdentity
    executable: Path
    arguments: tuple[str, ...] = ()
    readable_paths: tuple[Path, ...] = ()
    bubblewrap_path: Path = Path("/usr/bin/bwrap")
    timeout_seconds: int = 600
    output_limit_bytes: int = 4 * 1024 * 1024

    def isolation_profile(self, repo_root: Path) -> tuple[dict[str, Any], str]:
        root = repo_root.expanduser().resolve(strict=True)
        try:
            bubblewrap = self.bubblewrap_path.expanduser().resolve(strict=True)
            executable = self.executable.expanduser().resolve(strict=True)
            readable = tuple(path.expanduser().resolve(strict=True) for path in self.readable_paths)
        except OSError as error:
            raise RunnerIntegrityError(
                "sandbox executable or read-only input is missing"
            ) from error
        if not bubblewrap.is_file() or not executable.is_file():
            raise RunnerIntegrityError("sandbox launcher and provider executable must be files")
        if type(self.timeout_seconds) is not int or self.timeout_seconds < 1:
            raise RunnerIntegrityError("sandbox timeout_seconds must be a positive integer")
        if type(self.output_limit_bytes) is not int or self.output_limit_bytes < 1:
            raise RunnerIntegrityError("sandbox output_limit_bytes must be a positive integer")
        if any(not isinstance(argument, str) or "\x00" in argument for argument in self.arguments):
            raise RunnerIntegrityError("sandbox arguments must be NUL-free strings")
        exposed = (executable, *readable)
        if any(path.is_relative_to(root) or root.is_relative_to(path) for path in exposed):
            raise RunnerIntegrityError(
                "sandbox provider inputs cannot expose the repository or an ancestor"
            )
        profile = {
            "schema_version": "vlm_fallback.routing_sandbox_profile.v1",
            "launcher_path": str(bubblewrap),
            "launcher_sha256": _sha256_file(bubblewrap),
            "executable_path": str(executable),
            "executable_sha256": _sha256_file(executable),
            "arguments": list(self.arguments),
            "readable_paths": [str(path) for path in readable],
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
            "network_namespace": "unshared",
            "environment": "cleared",
            "repository_mounted": False,
        }
        return profile, protocol.canonical_sha256(profile)

    def invoke(
        self,
        request: RoutingProviderRequest,
        progress: ProviderProgress,
    ) -> ProviderOutcome:
        repo_root = FROZEN_SPEC_PATH.parents[3]
        profile, _ = self.isolation_profile(repo_root)
        executable = Path(profile["executable_path"])
        readable = tuple(Path(path) for path in profile["readable_paths"])
        request_payload = {
            "schema_version": "vlm_fallback.sandboxed_routing_request.v1",
            "request": {
                "invocation_id": request.invocation_id,
                "case_id": request.case_id,
                "arm": request.arm,
                "original_prompt": request.original_prompt,
                "routing_instruction": request.routing_instruction,
                "prompt_template_version": request.prompt_template_version,
                "failure_code": request.failure_code,
                "untyped_failure_summary": request.untyped_failure_summary,
                "typed_failure": request.typed_failure,
                "trusted_state": request.trusted_state,
                "asset_availability": request.asset_availability,
                "visible_report": request.visible_report,
                "seed": request.seed,
                "attempt": request.attempt,
                "allowed_routes": list(request.allowed_routes),
                "gold_route": request.gold_route,
                "resource_reservation": asdict(request.resource_reservation),
            },
        }
        command = [
            str(profile["launcher_path"]),
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/provider",
            "--ro-bind",
            str(executable),
            "/provider/executable",
        ]
        for system_path in (Path("/usr"), Path("/lib"), Path("/lib64"), Path("/bin")):
            if system_path.exists():
                command.extend(("--ro-bind", str(system_path), str(system_path)))
        if readable:
            command.extend(("--dir", "/inputs"))
        for index, path in enumerate(readable):
            command.extend(("--ro-bind", str(path), f"/inputs/{index}"))
        command.extend(
            (
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "PYTHONNOUSERSITE",
                "1",
                "--setenv",
                "VLM_SANDBOX_INPUTS_JSON",
                json.dumps(
                    [f"/inputs/{index}" for index in range(len(readable))],
                    separators=(",", ":"),
                ),
                "--chdir",
                "/tmp",
                "--",
                "/provider/executable",
                *self.arguments,
            )
        )
        progress("sandbox_started")
        completed = _run_bounded_provider(
            command,
            input_bytes=protocol.canonical_json_bytes(request_payload) + b"\n",
            timeout_seconds=int(profile["timeout_seconds"]),
            output_limit_bytes=int(profile["output_limit_bytes"]),
        )
        if completed.returncode != 0:
            raise ProviderContractError("sandboxed routing provider exited unsuccessfully")
        try:
            value = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderContractError("sandboxed routing provider output is not JSON") from error
        if protocol.canonical_json_bytes(value) + b"\n" != completed.stdout:
            raise ProviderContractError("sandboxed routing provider output must be canonical JSON")
        required = {
            "schema_version",
            "decision",
            "result",
            "raw_response",
            "parsed_response",
            "abstained",
            "resource",
            "claims_physical_pass",
        }
        if (
            not isinstance(value, dict)
            or set(value) != required
            or value.get("schema_version") != SANDBOXED_ROUTING_OUTPUT_SCHEMA
        ):
            raise ProviderContractError("sandboxed routing provider output contract mismatch")
        resource = value.get("resource")
        if not isinstance(resource, dict) or set(resource) != set(asdict(ResourceUsage())):
            raise ProviderContractError("sandboxed routing provider resource contract mismatch")
        outcome = ProviderOutcome(
            decision=value["decision"],
            result=value["result"],
            raw_response=value["raw_response"],
            parsed_response=value["parsed_response"],
            abstained=value["abstained"],
            resource=ResourceUsage(**resource),
            claims_physical_pass=value["claims_physical_pass"],
        )
        progress("sandbox_completed")
        return outcome


def _run_bounded_provider(
    command: Sequence[str],
    *,
    input_bytes: bytes,
    timeout_seconds: int,
    output_limit_bytes: int,
) -> subprocess.CompletedProcess[bytes]:
    """Run one provider while bounding stdout+stderr before buffering them."""

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as error:
        raise ProviderContractError("sandboxed routing provider could not start") from error
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    input_offset = 0
    deadline = time.monotonic() + timeout_seconds
    streams = (process.stdin, process.stdout, process.stderr)
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderContractError("sandboxed routing provider timed out")
            events = selector.select(timeout=min(remaining, 0.1))
            if not events and process.poll() is not None:
                # The next selector pass observes EOF on both output pipes.
                continue
            for key, _ in events:
                stream = key.fileobj
                if key.data == "stdin":
                    try:
                        written = os.write(stream.fileno(), input_bytes[input_offset:])
                    except BrokenPipeError:
                        written = 0
                    input_offset += written
                    if written == 0 or input_offset == len(input_bytes):
                        selector.unregister(stream)
                        stream.close()
                    continue
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                key.data.extend(chunk)
                if len(stdout) + len(stderr) > output_limit_bytes:
                    raise ProviderContractError(
                        "sandboxed routing provider output exceeds configured limit"
                    )
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        return subprocess.CompletedProcess(command, returncode, bytes(stdout), bytes(stderr))
    except (OSError, subprocess.SubprocessError) as error:
        raise ProviderContractError("sandboxed routing provider could not complete") from error
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in streams:
            if not stream.closed:
                stream.close()


RoutingProviderFactory = Callable[[str], TypedRoutingProvider]


@dataclass(frozen=True)
class RunReceipt:
    invocation_id: str
    event: Mapping[str, Any]
    resumed: bool


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_format_only_repair_prompt(
    source_prompt: str,
    source_response: str | bytes,
) -> str:
    """Build the sole allowed repair prompt from exact parent bytes."""

    if not isinstance(source_prompt, str) or not source_prompt:
        raise ValueError("format repair source_prompt must be non-empty text")
    if not isinstance(source_response, (str, bytes)):
        raise ValueError("format repair source_response must be text or bytes")
    response_bytes = (
        source_response.encode("utf-8") if isinstance(source_response, str) else source_response
    )
    payload = {
        "source_prompt_utf8_base64": base64.b64encode(source_prompt.encode("utf-8")).decode(
            "ascii"
        ),
        "source_response_base64": base64.b64encode(response_bytes).decode("ascii"),
    }
    return f"{_FORMAT_ONLY_REPAIR_INSTRUCTION}\n{protocol.canonical_json_bytes(payload).decode()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_mapping_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(protocol.canonical_json_bytes(value))


def _immutable_json_snapshot(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _immutable_json_snapshot(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_immutable_json_snapshot(item) for item in value)
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    payload = protocol.canonical_json_bytes(value) + b"\n"
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_canonical_bound_json(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
) -> dict[str, Any]:
    try:
        payload = path.expanduser().resolve(strict=True).read_bytes()
    except OSError as error:
        raise RunnerIntegrityError(f"{label} cannot be read") from error
    if _sha256_bytes(payload) != expected_sha256:
        raise RunnerIntegrityError(f"{label} digest mismatch")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunnerIntegrityError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise RunnerIntegrityError(f"{label} must be a JSON object")
    if protocol.canonical_json_bytes(value) + b"\n" != payload:
        raise RunnerIntegrityError(f"{label} must be canonical JSON")
    return value


def _load_sealed_test_annotation_manifest() -> dict[str, Any]:
    manifest = _load_canonical_bound_json(
        SEALED_TEST_ANNOTATION_MANIFEST_PATH,
        expected_sha256=SEALED_TEST_ANNOTATION_MANIFEST_SHA256,
        label="sealed test annotation manifest",
    )
    required = {
        "schema_version",
        "study_id",
        "spec_sha256",
        "state",
        "case_ids",
        "label_contract_sha256",
        "rater_contract_sha256",
        "adjudication_contract_sha256",
        "dev_gold_manifest_sha256",
        "test_annotation_payload_sha256",
    }
    if (
        set(manifest) != required
        or manifest.get("schema_version") != "vlm_fallback.sealed_test_annotation_manifest.v2"
        or manifest.get("study_id") != protocol.STUDY_ID
        or manifest.get("spec_sha256") != FROZEN_SPEC_SHA256
        or manifest.get("state") not in {"pending_blinded_annotation", "sealed"}
        or not isinstance(manifest.get("case_ids"), list)
        or any(not isinstance(case_id, str) or not case_id for case_id in manifest["case_ids"])
        or len(set(manifest["case_ids"])) != len(manifest["case_ids"])
    ):
        raise RunnerIntegrityError("sealed test annotation manifest contract mismatch")
    for field in (
        "label_contract_sha256",
        "rater_contract_sha256",
        "adjudication_contract_sha256",
    ):
        value = manifest.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or value != value.lower()
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise RunnerIntegrityError("sealed test annotation manifest digest is invalid")
    payload_fields = ("dev_gold_manifest_sha256", "test_annotation_payload_sha256")
    if manifest["state"] == "pending_blinded_annotation":
        if any(manifest.get(field) is not None for field in payload_fields):
            raise RunnerIntegrityError("pending annotation manifest cannot claim sealed payloads")
    else:
        for field in payload_fields:
            value = manifest.get(field)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or value != value.lower()
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise RunnerIntegrityError("sealed annotation payload digest is invalid")
    return manifest


def _annotation_contract_digests(spec: Mapping[str, Any]) -> dict[str, str]:
    experiment = spec["experiments"][0]
    gold = experiment["gold_annotation"]
    return {
        "label_contract_sha256": protocol.canonical_sha256(
            {
                "visible_checks": experiment["visible_checks"],
                "statuses": gold["statuses"],
                "insufficient_view_maps_to": gold["insufficient_view_maps_to"],
            }
        ),
        "rater_contract_sha256": protocol.canonical_sha256(
            {"raters": gold["raters"], "blinded_to": gold["blinded_to"]}
        ),
        "adjudication_contract_sha256": protocol.canonical_sha256(
            {
                "adjudication": gold["adjudication"],
                "agreement_report": gold["agreement_report"],
            }
        ),
    }


def _load_source_manifest(
    config: RunnerConfig,
    *,
    amendment_root_sha256: str,
) -> tuple[dict[str, Any], str]:
    manifest_path = (
        FROZEN_RUNNER_SOURCE_MANIFEST_PATH
        if config.execution_mode == "production"
        else config.source_manifest_path
    )
    try:
        raw_manifest = manifest_path.read_bytes()
    except OSError as error:
        raise RunnerIntegrityError("runner source manifest cannot be read") from error
    observed_manifest_sha = _sha256_bytes(raw_manifest)
    if (
        config.execution_mode == "test"
        and observed_manifest_sha != config.expected_source_manifest_sha256
    ):
        raise RunnerIntegrityError(
            "runner source manifest digest mismatch: "
            f"expected {config.expected_source_manifest_sha256}, observed {observed_manifest_sha}"
        )
    try:
        manifest = json.loads(raw_manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunnerIntegrityError("runner source manifest is not valid JSON") from error
    if protocol.canonical_json_bytes(manifest) + b"\n" != raw_manifest:
        raise RunnerIntegrityError("runner source manifest must be canonical JSON")
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "schema_version",
            "study_id",
            "amendment_root_manifest_sha256",
            "entries",
        }
        or manifest.get("schema_version") != RUNNER_SOURCE_SCHEMA
    ):
        raise RunnerIntegrityError(f"runner source manifest must use {RUNNER_SOURCE_SCHEMA}")
    if manifest.get("study_id") != protocol.STUDY_ID:
        raise RunnerIntegrityError("runner source manifest study_id mismatch")
    if manifest.get("amendment_root_manifest_sha256") != amendment_root_sha256:
        raise RunnerIntegrityError("runner source manifest amendment root mismatch")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RunnerIntegrityError("runner source manifest entries must be non-empty")
    root = config.repo_root.expanduser().resolve(strict=True)
    seen: set[str] = set()
    ordered_paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
            raise RunnerIntegrityError("runner source manifest entry must be an object")
        raw_path = entry.get("path")
        expected_sha = entry.get("sha256")
        expected_size = entry.get("size_bytes")
        if (
            not isinstance(raw_path, str)
            or not isinstance(expected_sha, str)
            or type(expected_size) is not int
            or expected_size < 0
        ):
            raise RunnerIntegrityError(
                "runner source manifest entry path/sha256/size must be valid"
            )
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or ".." in relative.parts or raw_path in seen:
            raise RunnerIntegrityError(f"unsafe or duplicate runner source path: {raw_path}")
        seen.add(raw_path)
        ordered_paths.append(raw_path)
        source_path = (root / Path(*relative.parts)).resolve(strict=False)
        if not source_path.is_relative_to(root) or not source_path.is_file():
            raise RunnerIntegrityError(
                f"runner source is missing or escapes repository: {raw_path}"
            )
        observed_sha = _sha256_file(source_path)
        if source_path.stat().st_size != expected_size:
            raise RunnerIntegrityError(f"runner source size mismatch for {raw_path}")
        if observed_sha != expected_sha:
            raise RunnerIntegrityError(
                f"runner source digest mismatch for {raw_path}: "
                f"expected {expected_sha}, observed {observed_sha}"
            )
    if ordered_paths != sorted(ordered_paths, key=lambda value: value.encode("utf-8")):
        raise RunnerIntegrityError("runner source manifest entries must be UTF-8 path sorted")
    if seen != REQUIRED_RUNNER_SOURCES:
        raise RunnerIntegrityError(
            "runner source manifest entries must exactly match required sources: "
            f"{sorted(REQUIRED_RUNNER_SOURCES)}"
        )
    return manifest, observed_manifest_sha


def _verification_profile(config: RunnerConfig) -> tuple[dict[str, Any], str]:
    profile = {
        "schema_version": "vlm_fallback.verification_profile.v1",
        "execution_mode": config.execution_mode,
        "verify_artifacts": config.verify_artifacts,
        "verify_models": config.verify_models,
        "allow_test_providers": config.allow_test_providers,
        "routing_provider_mode": config.routing_provider_mode,
    }
    if config.execution_mode not in {"production", "test"}:
        raise RunnerIntegrityError("execution_mode must be production or test")
    if config.routing_provider_mode not in {"sandboxed_subprocess", "in_process_test"}:
        raise RunnerIntegrityError(
            "routing_provider_mode must be sandboxed_subprocess or in_process_test"
        )
    if any(
        type(value) is not bool
        for value in (
            config.verify_artifacts,
            config.verify_models,
            config.allow_test_providers,
        )
    ):
        raise RunnerIntegrityError("verification profile flags must be booleans")
    if config.execution_mode == "production" and profile != {
        "schema_version": "vlm_fallback.verification_profile.v1",
        "execution_mode": "production",
        "verify_artifacts": True,
        "verify_models": True,
        "allow_test_providers": False,
        "routing_provider_mode": "sandboxed_subprocess",
    }:
        raise RunnerIntegrityError(
            "production mode requires artifact/model verification and forbids test providers"
        )
    if config.execution_mode == "production":
        try:
            repo_root = config.repo_root.expanduser().resolve(strict=True)
            log_path = config.log_path.expanduser().resolve(strict=True)
            source_path = config.source_manifest_path.expanduser().resolve(strict=True)
            # Runtime state may not exist before the first production open, so
            # compare its normalized lexical location without letting a caller
            # relocate authority through an alternate state file.
            anchor_path = Path(os.path.abspath(config.anchor_path.expanduser()))
            pending_path = Path(os.path.abspath(config.pending_path.expanduser()))
            lock_path = Path(os.path.abspath(config.lock_path.expanduser()))
        except OSError as error:
            raise RunnerIntegrityError("production frozen paths cannot be resolved") from error
        if repo_root != FROZEN_REPO_ROOT:
            raise RunnerIntegrityError("production repo_root must be the checked-in repository")
        if log_path != FROZEN_RUN_LOG_PATH:
            raise RunnerIntegrityError("production run log must be the checked-in append-only log")
        if anchor_path != FROZEN_RUN_ANCHOR_PATH:
            raise RunnerIntegrityError("production anchor must use the runner-owned state path")
        if pending_path != FROZEN_RUN_PENDING_PATH:
            raise RunnerIntegrityError("production pending receipt must use the runner-owned path")
        if lock_path != FROZEN_RUN_LOCK_PATH:
            raise RunnerIntegrityError("production lock must use the runner-owned path")
        if source_path != FROZEN_RUNNER_SOURCE_MANIFEST_PATH:
            raise RunnerIntegrityError(
                "production source manifest must be the checked-in fixed manifest"
            )
        if (
            config.expected_log_prefix_sha256 != FROZEN_LOG_PREFIX_SHA256
            or config.expected_log_prefix_lines != FROZEN_LOG_PREFIX_LINES
        ):
            raise RunnerIntegrityError("production run log prefix is runner-pinned")
    return profile, protocol.canonical_sha256(profile)


def _validate_pending_annotation_v3(
    manifest: Mapping[str, Any],
    *,
    spec: Mapping[str, Any],
    spec_sha256: str,
) -> dict[str, Any]:
    required = {
        "schema_version",
        "study_id",
        "amendment_id",
        "state",
        "base_spec_sha256",
        "effective_spec_v2",
        "case_ids",
        "label_contract_sha256",
        "rater_contract_sha256",
        "adjudication_contract_sha256",
        "dev_gold_manifest_sha256",
        "test_annotation_payload_sha256",
        "typed_correction_blind_adjudication_manifest_sha256",
        "typed_correction_adjudication_roster_sha256",
        "execution_authorized",
        "provider_execution_allowed_now",
    }
    expected_cases = [
        sample["case_id"]
        for experiment in spec["experiments"]
        if experiment["experiment_id"] == "A_visible_semantic_correction"
        for sample in experiment["samples"]
        if sample["split"] == "test"
    ]
    if (
        set(manifest) != required
        or manifest.get("schema_version") != "vlm_fallback.pending_annotation_manifest.v3"
        or manifest.get("study_id") != protocol.STUDY_ID
        or manifest.get("amendment_id") != "01"
        or manifest.get("state") != "pending_blinded_annotation"
        or manifest.get("base_spec_sha256") != FROZEN_SPEC_SHA256
        or manifest.get("effective_spec_v2")
        != {"path": "experiment_spec.v2.json", "sha256": spec_sha256}
        or manifest.get("case_ids") != expected_cases
        or manifest.get("execution_authorized") is not False
        or manifest.get("provider_execution_allowed_now") is not False
    ):
        raise RunnerIntegrityError("pending annotation manifest v3 contract mismatch")
    for field, digest in _annotation_contract_digests(spec).items():
        if manifest.get(field) != digest:
            raise RunnerIntegrityError("pending annotation manifest v3 contract digest mismatch")
    for field in (
        "dev_gold_manifest_sha256",
        "test_annotation_payload_sha256",
        "typed_correction_blind_adjudication_manifest_sha256",
        "typed_correction_adjudication_roster_sha256",
    ):
        if manifest.get(field) is not None:
            raise RunnerIntegrityError("pending annotation manifest v3 cannot contain evidence")
    return json.loads(protocol.canonical_json_bytes(manifest))


def _verify_model_content_bindings(
    spec: Mapping[str, Any],
    *,
    verify_content: bool,
    baselines: Mapping[str, _VerifiedModelBinding] | None,
) -> dict[str, _VerifiedModelBinding]:
    models = spec.get("models")
    if not isinstance(models, Sequence):
        raise RunnerIntegrityError("effective spec models are malformed")
    expected_roles = {model.get("role") for model in models if isinstance(model, Mapping)}
    if baselines is not None and set(baselines) != expected_roles:
        raise RunnerIntegrityError("model content baseline roles changed")
    verified: dict[str, _VerifiedModelBinding] = {}
    amendment_root = (amendment_bundle.DEFAULT_STUDY_ROOT / "amendments" / "01").resolve()
    for model in models:
        if not isinstance(model, Mapping):
            raise RunnerIntegrityError("effective spec model is malformed")
        role = model["role"]
        binding = model["content_manifest"]
        manifest_path = (amendment_root / binding["path"]).resolve(strict=False)
        if not manifest_path.is_relative_to(amendment_root):
            raise RunnerIntegrityError("model content manifest escapes amendment root")
        try:
            manifest = model_content.load_model_content_manifest(
                manifest_path,
                binding["canonical_sha256"],
                model_id=model["model_id"],
                revision=model["revision"],
            )
            snapshot: model_content.ModelContentSnapshot | None = None
            if verify_content:
                snapshot_path = Path(model["local_snapshot_path"])
                prior = baselines.get(role) if baselines is not None else None
                if prior is None:
                    snapshot = model_content.verify_model_content(snapshot_path, manifest)
                else:
                    if prior.snapshot is None:
                        raise ValueError("verified model baseline is missing its snapshot")
                    snapshot = model_content.refresh_model_content(
                        snapshot_path,
                        manifest,
                        prior.snapshot,
                    )
        except (OSError, TypeError, ValueError) as error:
            raise RunnerIntegrityError(
                f"model content verification failed for role: {role}"
            ) from error
        verified[role] = _VerifiedModelBinding(
            manifest=json.loads(protocol.canonical_json_bytes(manifest)),
            snapshot=snapshot,
        )
    return verified


def _verify_frozen_inputs(
    config: RunnerConfig,
    *,
    model_baselines: Mapping[str, _VerifiedModelBinding] | None = None,
) -> _FrozenInputs:
    _, verification_profile_sha256 = _verification_profile(config)
    try:
        configured_path = config.spec_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise RunnerIntegrityError("experiment spec cannot be read") from error
    expected_configured_path = (
        FROZEN_EFFECTIVE_SPEC_PATH if config.execution_mode == "production" else FROZEN_SPEC_PATH
    )
    if configured_path != expected_configured_path:
        raise RunnerIntegrityError("experiment spec path must be the checked-in frozen spec")
    try:
        loaded_bundle = amendment_bundle.load_verified_bundle()
        spec = loaded_bundle.parse_spec()
        pending_v3 = loaded_bundle.parse_pending_annotation_manifest()
    except (OSError, ValueError) as error:
        raise RunnerIntegrityError("fixed amendment bundle verification failed") from error
    observed_spec_sha = loaded_bundle.child_sha256["experiment_spec.v2.json"]
    if (
        loaded_bundle.root_sha256 != FROZEN_AMENDMENT_ROOT_SHA256
        or observed_spec_sha != FROZEN_EFFECTIVE_SPEC_SHA256
        or loaded_bundle.child_sha256["pending_annotation_manifest.v3.json"]
        != FROZEN_PENDING_ANNOTATION_V3_SHA256
    ):
        raise RunnerIntegrityError("runner-pinned amendment bundle identity mismatch")
    summary = protocol.validate_effective_spec_v2(
        spec,
        repo_root=config.repo_root,
        verify_files=config.verify_artifacts,
    )
    _, source_manifest_sha = _load_source_manifest(
        config,
        amendment_root_sha256=loaded_bundle.root_sha256,
    )
    pending_v3 = _validate_pending_annotation_v3(
        pending_v3,
        spec=spec,
        spec_sha256=observed_spec_sha,
    )
    if config.execution_mode == "test":
        annotation_manifest = _load_sealed_test_annotation_manifest()
        expected_test_cases = {
            sample["case_id"]
            for experiment in spec["experiments"]
            if experiment["experiment_id"] == "A_visible_semantic_correction"
            for sample in experiment["samples"]
            if sample["split"] == "test"
        }
        if set(annotation_manifest["case_ids"]) != expected_test_cases:
            raise RunnerIntegrityError("sealed test annotation manifest case binding mismatch")
        if any(
            annotation_manifest.get(field) != digest
            for field, digest in _annotation_contract_digests(spec).items()
        ):
            raise RunnerIntegrityError("sealed test annotation contract digest mismatch")
        annotation_manifest_sha256 = SEALED_TEST_ANNOTATION_MANIFEST_SHA256
    else:
        annotation_manifest = pending_v3
        annotation_manifest_sha256 = loaded_bundle.child_sha256[
            "pending_annotation_manifest.v3.json"
        ]
    model_bindings = _verify_model_content_bindings(
        spec,
        verify_content=config.verify_models,
        baselines=model_baselines,
    )
    identity_matches = sum(binding.snapshot is not None for binding in model_bindings.values())
    manifest_receipts = tuple(
        sorted(
            (
                role,
                model_content.manifest_sha256(binding.manifest),
            )
            for role, binding in model_bindings.items()
        )
    )
    roster_receipts = tuple(
        sorted(
            (
                role,
                binding.snapshot.roster_sha256 if binding.snapshot is not None else None,
            )
            for role, binding in model_bindings.items()
        )
    )
    # Reparse a fresh canonical snapshot on every boundary.  No mutable object
    # from the caller or a previous invocation is used for execution.
    return _FrozenInputs(
        spec=json.loads(protocol.canonical_json_bytes(spec)),
        identity=RunnerIdentity(
            study_id=summary["study_id"],
            spec_sha256=observed_spec_sha,
            source_manifest_sha256=source_manifest_sha,
            verified_artifact_count=summary["verified_artifact_count"],
            model_identity_matches=identity_matches,
            execution_mode=config.execution_mode,
            verification_profile_sha256=verification_profile_sha256,
            amendment_root_sha256=loaded_bundle.root_sha256,
            model_content_manifest_sha256=manifest_receipts,
            model_roster_sha256=roster_receipts,
        ),
        annotation_manifest=annotation_manifest,
        annotation_manifest_sha256=annotation_manifest_sha256,
        model_bindings=MappingProxyType(dict(model_bindings)),
    )


class _Journal:
    def __init__(self, config: RunnerConfig, identity: RunnerIdentity) -> None:
        self._config = config
        self._identity = identity
        self.events: list[dict[str, Any]] = []
        with self._lock():
            self._recover_pending()
            self.anchor = self._open_anchor()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._config.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._config.lock_path.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def serialized_operation(self) -> Iterator[None]:
        """Refresh state and serialize provider boundaries across runner processes."""

        operation_lock = self._config.lock_path.with_name(
            f"{self._config.lock_path.name}.operation"
        )
        operation_lock.parent.mkdir(parents=True, exist_ok=True)
        with operation_lock.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                with self._lock():
                    self._recover_pending()
                    self.anchor = self._open_anchor()
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _observed_anchor(self, payload: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not payload.endswith(b"\n"):
            raise RunnerIntegrityError("run log is truncated: final newline is missing")
        lines = payload.splitlines(keepends=True)
        if len(lines) < self._config.expected_log_prefix_lines:
            raise RunnerIntegrityError(
                "run log is truncated before the frozen preregistration prefix"
            )
        prefix = b"".join(lines[: self._config.expected_log_prefix_lines])
        if _sha256_bytes(prefix) != self._config.expected_log_prefix_sha256:
            raise RunnerIntegrityError("run log preregistration prefix digest mismatch")
        previous_sha = self._config.expected_log_prefix_sha256
        parsed_events: list[dict[str, Any]] = []
        event_ids: set[str] = set()
        logical_keys: set[str] = set()
        stopped = False
        stop_reason: str | None = None
        for line_number, line in enumerate(
            lines[self._config.expected_log_prefix_lines :],
            start=self._config.expected_log_prefix_lines + 1,
        ):
            try:
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RunnerIntegrityError(
                    f"run log line {line_number} is not valid JSON"
                ) from error
            if not isinstance(event, dict):
                raise RunnerIntegrityError(f"run log line {line_number} must be an object")
            if protocol.canonical_json_bytes(event) + b"\n" != line:
                raise RunnerIntegrityError(f"run log line {line_number} is not canonical JSON")
            event_sha = event.get("event_sha256")
            unhashed = dict(event)
            unhashed.pop("event_sha256", None)
            if event.get("previous_event_sha256") != previous_sha:
                raise RunnerIntegrityError(f"run log chain mismatch at line {line_number}")
            if event_sha != protocol.canonical_sha256(unhashed):
                raise RunnerIntegrityError(f"run log event digest mismatch at line {line_number}")
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id or event_id in event_ids:
                raise RunnerIntegrityError(f"duplicate or invalid event_id at line {line_number}")
            event_ids.add(event_id)
            logical_key = event.get("input_bindings", {}).get("logical_key")
            if logical_key is not None:
                if not isinstance(logical_key, str) or logical_key in logical_keys:
                    raise RunnerIntegrityError(
                        f"duplicate or invalid invocation logical_key at line {line_number}"
                    )
                logical_keys.add(logical_key)
            stop_violations = event.get("gate_results", {}).get("stop_rule_violations")
            if isinstance(stop_violations, list) and stop_violations:
                stopped = True
                stop_reason = "; ".join(str(item) for item in stop_violations)
            if event.get("gate_results", {}).get("study_stopped") is True:
                stopped = True
                stop_reason = str(
                    event.get("gate_results", {}).get("stop_reason")
                    or "; ".join(str(item) for item in stop_violations or [])
                    or "unspecified"
                )
            if event.get("decision") == "protocol_stopped":
                stopped = True
                reason = event.get("outputs", {}).get("stop_reason")
                stop_reason = str(reason) if reason is not None else "unspecified"
            parsed_events.append(event)
            previous_sha = str(event_sha)
        setup_event_count = self._validate_setup_prelude(parsed_events)
        events = parsed_events[setup_event_count:]
        anchor = {
            "schema_version": ANCHOR_SCHEMA,
            "study_id": self._identity.study_id,
            "spec_sha256": self._identity.spec_sha256,
            "source_manifest_sha256": self._identity.source_manifest_sha256,
            "execution_mode": self._identity.execution_mode,
            "verification_profile_sha256": self._identity.verification_profile_sha256,
            "genesis_blob_sha256": self._config.expected_log_prefix_sha256,
            "genesis_line_count": self._config.expected_log_prefix_lines,
            "setup_event_count": setup_event_count,
            "log_sha256": _sha256_bytes(payload),
            "log_size_bytes": len(payload),
            "line_count": len(lines),
            "last_event_sha256": previous_sha,
            "stopped": stopped,
            "stop_reason": stop_reason,
        }
        return anchor, events

    def _validate_setup_prelude(self, events: Sequence[Mapping[str, Any]]) -> int:
        """Return two only for the exact amendment/source commitment prelude."""

        setup_decisions = {
            "amendment_root_committed",
            "runner_source_manifest_committed",
        }
        if not events:
            if self._identity.execution_mode == "production":
                raise RunnerIntegrityError("run log is missing the two-event setup prelude")
            return 0
        first_decision = events[0].get("decision")
        if first_decision not in setup_decisions:
            if any(event.get("decision") in setup_decisions for event in events):
                raise RunnerIntegrityError(
                    "run log setup events must be the first two suffix lines"
                )
            if self._identity.execution_mode == "production":
                raise RunnerIntegrityError("run log is missing the two-event setup prelude")
            return 0
        if len(events) < 2:
            raise RunnerIntegrityError("run log setup prelude must contain exactly two events")

        line7 = dict(events[0])
        line8 = dict(events[1])
        line7_sha = line7.pop("event_sha256", None)
        line8_sha = line8.pop("event_sha256", None)
        expected_line7 = {
            "schema_version": AMENDMENT_COMMITMENT_SCHEMA,
            "event_id": "amendment-01-root-commitment",
            "study_id": self._identity.study_id,
            "decision": "amendment_root_committed",
            "input_bindings": {
                "amendment_id": "01",
                "amendment_root_manifest_sha256": self._identity.amendment_root_sha256,
                "base_spec_sha256": FROZEN_SPEC_SHA256,
            },
            "previous_event_sha256": self._config.expected_log_prefix_sha256,
        }
        if line7 != expected_line7:
            raise RunnerIntegrityError("run log amendment commitment event mismatch")
        if (
            self._identity.execution_mode == "production"
            and line7_sha != FROZEN_AMENDMENT_COMMITMENT_EVENT_SHA256
        ):
            raise RunnerIntegrityError("run log amendment commitment digest is not runner-pinned")
        expected_line8 = {
            "schema_version": RUNNER_SOURCE_COMMITMENT_SCHEMA,
            "event_id": "runner-source-manifest-v2-commitment",
            "study_id": self._identity.study_id,
            "decision": "runner_source_manifest_committed",
            "input_bindings": {
                "amendment_root_manifest_sha256": self._identity.amendment_root_sha256,
                "runner_source_manifest_sha256": self._identity.source_manifest_sha256,
            },
            "previous_event_sha256": line7_sha,
        }
        if line8 != expected_line8:
            raise RunnerIntegrityError("run log runner source commitment event mismatch")
        if line8_sha != protocol.canonical_sha256(line8):
            # The generic chain parser already verifies this.  Keeping the
            # assertion here makes the two-event contract self-contained.
            raise RunnerIntegrityError("run log runner source commitment digest mismatch")
        if any(event.get("decision") in setup_decisions for event in events[2:]):
            raise RunnerIntegrityError("run log setup prelude must contain exactly two events")
        return 2

    @staticmethod
    def _anchors_match(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> None:
        for field in (
            "schema_version",
            "study_id",
            "spec_sha256",
            "source_manifest_sha256",
            "execution_mode",
            "verification_profile_sha256",
            "genesis_blob_sha256",
            "genesis_line_count",
            "setup_event_count",
            "log_sha256",
            "log_size_bytes",
            "line_count",
            "last_event_sha256",
            "stopped",
            "stop_reason",
        ):
            if expected.get(field) != observed.get(field):
                raise RunnerIntegrityError(f"run anchor mismatch for {field}")

    @staticmethod
    def _anchors_equal(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
        try:
            _Journal._anchors_match(expected, observed)
        except RunnerIntegrityError:
            return False
        return True

    def _recover_pending(self) -> None:
        if not self._config.pending_path.exists():
            return
        if not self._config.anchor_path.is_file():
            raise RunnerIntegrityError("pending receipt exists without a trusted run anchor")
        try:
            pending = json.loads(self._config.pending_path.read_text(encoding="utf-8"))
            anchor = json.loads(self._config.anchor_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RunnerIntegrityError("pending receipt or run anchor is not valid JSON") from error
        if not isinstance(pending, dict) or pending.get("schema_version") != (
            "vlm_fallback.pending_append.v1"
        ):
            raise RunnerIntegrityError("pending receipt schema is invalid")
        if not isinstance(anchor, dict):
            raise RunnerIntegrityError("run anchor must be a JSON object")
        old_sha = pending.get("old_log_sha256")
        old_size = pending.get("old_log_size_bytes")
        event = pending.get("event")
        if not isinstance(old_sha, str) or not isinstance(old_size, int) or old_size < 0:
            raise RunnerIntegrityError("pending receipt old log binding is invalid")
        if not isinstance(event, dict):
            raise RunnerIntegrityError("pending receipt event must be an object")
        event_sha = event.get("event_sha256")
        unhashed = dict(event)
        unhashed.pop("event_sha256", None)
        if not isinstance(event_sha, str) or event_sha != protocol.canonical_sha256(unhashed):
            raise RunnerIntegrityError("pending receipt event digest mismatch")
        encoded = protocol.canonical_json_bytes(event) + b"\n"
        try:
            payload = self._config.log_path.read_bytes()
        except OSError as error:
            raise RunnerIntegrityError("run log cannot be read during pending recovery") from error
        if len(payload) < old_size or _sha256_bytes(payload[:old_size]) != old_sha:
            raise RunnerIntegrityError("pending receipt old log binding mismatch")
        old_payload = payload[:old_size]
        old_observed, _ = self._observed_anchor(old_payload)
        if event.get("previous_event_sha256") != old_observed["last_event_sha256"]:
            raise RunnerIntegrityError("pending receipt does not extend the anchored event chain")
        if payload == old_payload:
            if not self._anchors_equal(anchor, old_observed):
                raise RunnerIntegrityError("pending receipt old run anchor mismatch")
            with self._config.log_path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            payload = old_payload + encoded
        elif payload != old_payload + encoded:
            raise RunnerIntegrityError("pending receipt does not match the run log suffix")
        new_observed, _ = self._observed_anchor(payload)
        if not (
            self._anchors_equal(anchor, old_observed) or self._anchors_equal(anchor, new_observed)
        ):
            raise RunnerIntegrityError(
                "pending receipt run anchor matches neither transaction state"
            )
        _atomic_json_write(self._config.anchor_path, new_observed)
        self._config.pending_path.unlink()
        directory_fd = os.open(
            self._config.pending_path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _open_anchor(self) -> dict[str, Any]:
        try:
            payload = self._config.log_path.read_bytes()
        except OSError as error:
            raise RunnerIntegrityError("run log cannot be read") from error
        observed, events = self._observed_anchor(payload)
        if not self._config.anchor_path.exists():
            if events:
                raise RunnerIntegrityError(
                    "run anchor is missing for a log that already contains execution events"
                )
            _atomic_json_write(self._config.anchor_path, observed)
            self.events = events
            return observed
        try:
            anchor = json.loads(self._config.anchor_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RunnerIntegrityError("run anchor is not valid JSON") from error
        if not isinstance(anchor, dict):
            raise RunnerIntegrityError("run anchor must be a JSON object")
        self._anchors_match(anchor, observed)
        self.events = events
        return anchor

    def append(self, body: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock():
            self._recover_pending()
            self.anchor = self._open_anchor()
            event = dict(body)
            model_receipt = dict(event.get("model_receipt", {}))
            model_receipt.setdefault("model_content_manifest_sha256", None)
            model_receipt.setdefault("model_roster_sha256", None)
            event["model_receipt"] = model_receipt
            event["receipt_promotion_authority"] = False
            event["receipt_scope"] = (
                "test_only_non_promotion"
                if self._identity.execution_mode == "test"
                else "production_preregistration_non_promotion"
            )
            event["previous_event_sha256"] = self.anchor["last_event_sha256"]
            event["event_sha256"] = protocol.canonical_sha256(event)
            encoded = protocol.canonical_json_bytes(event) + b"\n"
            pending = {
                "schema_version": "vlm_fallback.pending_append.v1",
                "old_log_sha256": self.anchor["log_sha256"],
                "old_log_size_bytes": self.anchor["log_size_bytes"],
                "event": event,
            }
            _atomic_json_write(self._config.pending_path, pending)
            with self._config.log_path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            payload = self._config.log_path.read_bytes()
            observed, events = self._observed_anchor(payload)
            _atomic_json_write(self._config.anchor_path, observed)
            self._config.pending_path.unlink()
            directory_fd = os.open(
                self._config.pending_path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self.anchor = observed
            self.events = events
            return event


class ExperimentRunner:
    """Validate the frozen study and execute provider calls behind one audit seam."""

    def __init__(
        self,
        *,
        config: RunnerConfig,
        spec: dict[str, Any],
        identity: RunnerIdentity,
        annotation_manifest: dict[str, Any],
        annotation_manifest_sha256: str,
        model_bindings: Mapping[str, _VerifiedModelBinding],
        journal: _Journal,
        progress: ProgressCallback | None,
        visible_provider: VisibleCriticProvider | None,
        routing_provider: TypedRoutingProvider | None,
        routing_provider_factory: RoutingProviderFactory | None,
        oracle_routing_provider_factory: RoutingProviderFactory | None,
        oracle_capability: OracleRoutingCapability | None,
    ) -> None:
        self.config = config
        self._spec = _immutable_json_snapshot(json.loads(protocol.canonical_json_bytes(spec)))
        self.identity = identity
        self._annotation_manifest = _immutable_json_snapshot(annotation_manifest)
        self._annotation_manifest_sha256 = annotation_manifest_sha256
        self._model_bindings = dict(model_bindings)
        self._journal = journal
        self._progress = progress
        self._visible_provider = visible_provider
        self._routing_provider = routing_provider
        self._routing_provider_factory = routing_provider_factory
        self._oracle_routing_provider_factory = oracle_routing_provider_factory
        self._oracle_capability = oracle_capability
        self._intent_verifier: IntentPreservationVerifier = _ProductionIntentPreservationVerifier()
        self._consumed_routing_provider_instances: list[TypedRoutingProvider] = []
        self._deployable_routing_authorities: set[tuple[str, str, str]] = set()
        self._oracle_routing_authorities: set[tuple[str, str, str]] = set()
        self._restore_routing_authorities()
        self._progress_sequence = 0
        self._progress_error_digests: list[str] = []

    @property
    def spec(self) -> Mapping[str, Any]:
        """The exact validated execution spec as a recursively immutable snapshot."""

        return self._spec

    @classmethod
    def open(
        cls,
        config: RunnerConfig,
        *,
        visible_provider: VisibleCriticProvider | None = None,
        routing_provider: TypedRoutingProvider | None = None,
        routing_provider_factory: RoutingProviderFactory | None = None,
        oracle_routing_provider_factory: RoutingProviderFactory | None = None,
        oracle_capability: OracleRoutingCapability | None = None,
        progress: ProgressCallback | None = None,
    ) -> ExperimentRunner:
        if routing_provider is not None and routing_provider_factory is not None:
            raise ValueError("configure routing_provider or routing_provider_factory, not both")
        if (
            routing_provider_factory is not None
            and routing_provider_factory is oracle_routing_provider_factory
        ):
            raise ValueError("oracle and deployable routing factories must be distinct")
        frozen = _verify_frozen_inputs(config)
        journal = _Journal(config, frozen.identity)
        runner = cls(
            config=config,
            spec=frozen.spec,
            identity=frozen.identity,
            annotation_manifest=frozen.annotation_manifest,
            annotation_manifest_sha256=frozen.annotation_manifest_sha256,
            model_bindings=frozen.model_bindings,
            journal=journal,
            progress=progress,
            visible_provider=visible_provider,
            routing_provider=routing_provider,
            routing_provider_factory=routing_provider_factory,
            oracle_routing_provider_factory=oracle_routing_provider_factory,
            oracle_capability=oracle_capability,
        )
        with journal.serialized_operation():
            runner._recover_interrupted_invocations()
        runner._emit("runner.validated")
        return runner

    def _refresh_execution_snapshot(self) -> None:
        """Use a newly parsed, verified checked-in spec at each call boundary."""

        frozen = _verify_frozen_inputs(
            self.config,
            model_baselines=self._model_bindings,
        )
        static_identity = replace(
            frozen.identity,
            model_roster_sha256=self.identity.model_roster_sha256,
        )
        if static_identity != self.identity:
            raise RunnerIntegrityError("frozen execution identity changed after runner open")
        self.identity = frozen.identity
        self._spec = _immutable_json_snapshot(frozen.spec)
        self._annotation_manifest = _immutable_json_snapshot(frozen.annotation_manifest)
        self._annotation_manifest_sha256 = frozen.annotation_manifest_sha256
        self._model_bindings = dict(frozen.model_bindings)

    def _emit(self, stage: str, **fields: Any) -> None:
        self._progress_sequence += 1
        if self._progress is None:
            return
        update = ProgressUpdate(
            sequence=self._progress_sequence,
            timestamp_utc=_utc_now(),
            stage=stage,
            **fields,
        )
        try:
            self._progress(update)
        except Exception as error:
            self._progress_error_digests.append(
                protocol.canonical_sha256(
                    {"exception_type": type(error).__name__, "message": str(error)}
                )
            )

    @property
    def progress_error_digests(self) -> tuple[str, ...]:
        return tuple(self._progress_error_digests)

    def _recover_interrupted_invocations(self) -> None:
        terminal_reservations = {
            event.get("input_bindings", {}).get("reservation_event_id")
            for event in self._journal.events
            if event.get("decision")
            in {
                "provider_call_completed",
                "provider_call_failed",
                "provider_call_interrupted_unknown",
            }
        }
        reservations = [
            event
            for event in self._journal.events
            if event.get("decision") == "provider_call_reserved"
            and event.get("event_id") not in terminal_reservations
        ]
        for reservation in reservations:
            stop_reason = "provider execution interrupted with unknown resource usage"
            bindings = dict(reservation["input_bindings"])
            logical_key = bindings.pop("reservation_logical_key")
            bindings["logical_key"] = logical_key
            bindings["reservation_event_id"] = reservation["event_id"]
            gate_results = dict(reservation["gate_results"])
            gate_results["execution_completion"] = "unknown_interrupted"
            gate_results["resource_budget"] = "unknown_fail_closed"
            gate_results["stop_rule_violations"] = [stop_reason]
            gate_results["study_stopped"] = True
            gate_results["stop_reason"] = stop_reason
            resource_receipt = dict(reservation["resource_receipt"])
            resource_receipt["usage_known"] = False
            event = self._journal.append(
                {
                    "schema_version": reservation["schema_version"],
                    "event_id": f"interrupted-{bindings['request_sha256']}",
                    "timestamp_utc": _utc_now(),
                    "study_id": reservation["study_id"],
                    "experiment_id": reservation["experiment_id"],
                    "phase": reservation["phase"],
                    "case_id": reservation["case_id"],
                    "arm": reservation["arm"],
                    "attempt": reservation["attempt"],
                    "decision": "provider_call_interrupted_unknown",
                    "input_bindings": bindings,
                    "model_receipt": dict(reservation["model_receipt"]),
                    "resource_receipt": resource_receipt,
                    "outputs": {
                        "decision": "interrupted_unknown",
                        "result": None,
                        "abstain": True,
                        "repair": reservation["outputs"].get("repair"),
                    },
                    "gate_results": gate_results,
                    "error": {
                        "code": "interrupted_unknown",
                        "reservation_event_id": reservation["event_id"],
                    },
                    "expensive_execution_started": True,
                }
            )
            self._emit(
                "invocation.interrupted_recovered",
                experiment_id=event["experiment_id"],
                case_id=event["case_id"],
                arm=event["arm"],
                invocation_id=bindings["request_sha256"],
                durable_event_id=event["event_id"],
            )

    def _reserve_provider_call(
        self,
        *,
        experiment_id: str,
        phase: str,
        case_id: str,
        arm: str,
        attempt: int,
        logical_key: str,
        request_sha: str,
        input_bindings: Mapping[str, Any],
        model_receipt: Mapping[str, Any],
        repair: Mapping[str, Any],
        provider_production_eligible: bool,
    ) -> dict[str, Any]:
        return self._journal.append(
            {
                "schema_version": self.spec["logging_contract"]["schema_version"],
                "event_id": f"reserve-{request_sha}",
                "timestamp_utc": _utc_now(),
                "study_id": self.identity.study_id,
                "experiment_id": experiment_id,
                "phase": phase,
                "case_id": case_id,
                "arm": arm,
                "attempt": attempt,
                "decision": "provider_call_reserved",
                "input_bindings": {
                    "reservation_logical_key": logical_key,
                    "request_sha256": request_sha,
                    "verification_profile_sha256": (self.identity.verification_profile_sha256),
                    **dict(input_bindings),
                },
                "model_receipt": dict(model_receipt),
                "resource_receipt": {"wall_time_ms": 0, **asdict(ResourceUsage())},
                "outputs": {
                    "decision": "provider_call_reserved",
                    "result": None,
                    "abstain": True,
                    "repair": dict(repair),
                },
                "gate_results": {
                    "provider_production_eligible": provider_production_eligible,
                    "physical_gate_authority": self.spec["physical_gate_authority"],
                    "render_used_as_physics_evidence": False,
                    "execution_completion": "reserved",
                    "receipt_hash_bound": True,
                },
                "error": None,
                "expensive_execution_started": False,
            }
        )

    def _routing_provider_for(self, arm: str) -> TypedRoutingProvider:
        is_oracle = arm == "B3_oracle_route_ceiling"
        if is_oracle:
            capability = self._oracle_capability
            if (
                capability is None
                or capability.study_id != self.identity.study_id
                or capability.spec_sha256 != self.identity.spec_sha256
                or capability.purpose != "descriptive_ceiling_only"
            ):
                raise RunnerIntegrityError(
                    "B3 oracle routing requires an exact descriptive-ceiling capability"
                )
            factory = self._oracle_routing_provider_factory
            if factory is None:
                raise RunnerIntegrityError("B3 oracle routing requires a separate provider factory")
            provider = factory(arm)
        elif self._routing_provider_factory is not None:
            provider = self._routing_provider_factory(arm)
        elif self._routing_provider is not None:
            provider = self._routing_provider
        else:
            raise RunnerIntegrityError("no typed routing provider is configured")
        if self.config.routing_provider_mode == "sandboxed_subprocess":
            if type(provider) is not SandboxedRoutingProvider:
                raise RunnerIntegrityError(
                    "sandboxed routing mode requires a runner-owned SandboxedRoutingProvider"
                )
            provider.isolation_profile(self.config.repo_root)
        if (is_oracle or self._routing_provider_factory is not None) and any(
            provider is prior for prior in self._consumed_routing_provider_instances
        ):
            raise RunnerIntegrityError(
                "routing provider factory reused a stateful provider instance"
            )
        identity = self._snapshot_provider_identity(provider)
        authority = (
            identity.provider_id,
            identity.revision,
            identity.implementation_sha256,
        )
        other_authorities = (
            self._deployable_routing_authorities if is_oracle else self._oracle_routing_authorities
        )
        if authority in other_authorities:
            raise RunnerIntegrityError(
                "B2/B3 provider authority must be disjoint across deployable and oracle arms"
            )
        (
            self._oracle_routing_authorities if is_oracle else self._deployable_routing_authorities
        ).add(authority)
        self._consumed_routing_provider_instances.append(provider)
        return provider

    def _restore_routing_authorities(self) -> None:
        for event in self._journal.events:
            if (
                event.get("experiment_id") != "B_typed_failure_prompt_fallback"
                or event.get("decision") != "provider_call_reserved"
            ):
                continue
            model = event.get("model_receipt", {})
            authority = (
                model.get("provider_id"),
                model.get("provider_revision"),
                model.get("provider_implementation_sha256"),
            )
            if not all(isinstance(value, str) and value for value in authority):
                raise RunnerIntegrityError(
                    "durable routing reservation lacks a complete provider authority"
                )
            target = (
                self._oracle_routing_authorities
                if event.get("arm") == "B3_oracle_route_ceiling"
                else self._deployable_routing_authorities
            )
            target.add(authority)  # type: ignore[arg-type]
        overlap = self._oracle_routing_authorities & self._deployable_routing_authorities
        if overlap:
            raise RunnerIntegrityError("durable B2/B3 provider authority history is not disjoint")

    def _visible_context(
        self,
        invocation: VisibleInvocation,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], ModelBinding, list[dict[str, Any]]]:
        experiment = self.spec["experiments"][0]
        arm = next((item for item in experiment["arms"] if item["arm"] == invocation.arm), None)
        if arm is None:
            raise ValueError(f"unknown visible arm: {invocation.arm}")
        sample = next(
            (item for item in experiment["samples"] if item["case_id"] == invocation.case_id),
            None,
        )
        if sample is None:
            raise ValueError(f"unknown visible case: {invocation.case_id}")
        model_value = next(
            (item for item in self.spec["models"] if item["role"] == arm["model_role"]),
            None,
        )
        if model_value is None:
            raise RunnerIntegrityError(f"model role is missing: {arm['model_role']}")
        verified_model = self._model_bindings.get(model_value["role"])
        if verified_model is None:
            raise RunnerIntegrityError(f"model content role is missing: {model_value['role']}")
        content_manifest_sha256 = model_content.manifest_sha256(verified_model.manifest)
        model = ModelBinding(
            role=model_value["role"],
            model_id=model_value["model_id"],
            revision=model_value["revision"],
            snapshot_manifest_sha256=model_value["snapshot_manifest_sha256"],
            model_content_manifest_sha256=content_manifest_sha256,
            model_roster_sha256=(
                verified_model.snapshot.roster_sha256
                if verified_model.snapshot is not None
                else None
            ),
            local_snapshot_path=Path(model_value["local_snapshot_path"]).expanduser().resolve(),
            max_new_tokens=int(model_value["generation"]["max_new_tokens"]),
        )
        artifacts = protocol.build_bundle_manifest(self.config.repo_root, sample["artifacts"])
        observed_bundle = protocol.canonical_sha256(artifacts)
        if observed_bundle != sample["bundle_sha256"]:
            raise RunnerIntegrityError(
                f"artifact bundle drift for {invocation.case_id}: "
                f"expected {sample['bundle_sha256']}, "
                f"observed {observed_bundle}"
            )
        return experiment, arm, sample, model, artifacts

    def _validate_a0_prompt_binding(
        self,
        invocation: VisibleInvocation,
        *,
        sample: Mapping[str, Any],
        artifacts: Sequence[Mapping[str, Any]],
    ) -> None:
        if invocation.arm != "A0_current_critic_3b":
            return
        if invocation.resolved_scene_path is None or invocation.resolved_scene_sha256 is None:
            raise RunnerIntegrityError(
                "A0 requires a source-bound resolved scene for the frozen baseline prompt"
            )
        root = self.config.repo_root.expanduser().resolve(strict=True)
        try:
            resolved_path = invocation.resolved_scene_path.expanduser().resolve(strict=True)
        except OSError as error:
            raise RunnerIntegrityError("A0 resolved scene cannot be read") from error
        if not resolved_path.is_relative_to(root) or not resolved_path.is_file():
            raise RunnerIntegrityError("A0 resolved scene must be a repository file")
        try:
            resolved = ResolvedSceneSpec.model_validate_json(
                resolved_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise RunnerIntegrityError("A0 resolved scene is invalid") from error
        observed_digest = resolved.digest()
        if observed_digest != invocation.resolved_scene_sha256:
            raise RunnerIntegrityError("A0 resolved scene digest mismatch")
        evidence_digests: set[str] = set()
        for artifact in artifacts:
            if Path(str(artifact["path"])).suffix.lower() != ".json":
                continue
            try:
                value = json.loads((root / str(artifact["path"])).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, Mapping) and isinstance(value.get("resolved_scene_sha256"), str):
                evidence_digests.add(value["resolved_scene_sha256"])
        if observed_digest not in evidence_digests or resolved.request != sample["task_context"]:
            raise RunnerIntegrityError("A0 resolved scene is not bound to the frozen sample")
        if invocation.prompt != build_critic_prompt(resolved):
            raise RunnerIntegrityError(
                "A0 prompt must exactly match the frozen build_critic_prompt output"
            )
        if invocation.prompt_template_version != (
            "scene_gen.rendered_critic.build_critic_prompt.v1"
        ):
            raise RunnerIntegrityError("A0 prompt template version is not frozen")

    @staticmethod
    def _validate_provider_identity(identity: ProviderIdentity) -> None:
        if not isinstance(identity, ProviderIdentity):
            raise RunnerIntegrityError("provider identity must use ProviderIdentity")
        if not identity.provider_id or not identity.revision or not identity.kind:
            raise RunnerIntegrityError("provider identity strings must be non-empty")
        if type(identity.production_eligible) is not bool:
            raise RunnerIntegrityError("provider production_eligible must be a boolean")
        try:
            int(identity.implementation_sha256, 16)
        except (TypeError, ValueError) as error:
            raise RunnerIntegrityError(
                "provider implementation_sha256 must be lowercase hex"
            ) from error
        if (
            len(identity.implementation_sha256) != 64
            or identity.implementation_sha256 != identity.implementation_sha256.lower()
        ):
            raise RunnerIntegrityError("provider implementation_sha256 must be lowercase hex")

    @classmethod
    def _snapshot_provider_identity(cls, provider: Any) -> ProviderIdentity:
        try:
            identity = provider.identity
        except Exception as error:
            raise RunnerIntegrityError("provider identity cannot be read") from error
        cls._validate_provider_identity(identity)
        return ProviderIdentity(
            provider_id=identity.provider_id,
            revision=identity.revision,
            implementation_sha256=identity.implementation_sha256,
            kind=identity.kind,
            production_eligible=identity.production_eligible,
        )

    @classmethod
    def _verify_provider_identity_unchanged(
        cls,
        provider: Any,
        expected: ProviderIdentity,
    ) -> None:
        try:
            observed = cls._snapshot_provider_identity(provider)
        except RunnerIntegrityError as error:
            raise ProviderContractError("provider identity changed during invocation") from error
        if observed != expected:
            raise ProviderContractError("provider identity changed during invocation")

    @staticmethod
    def _validate_provider_outcome(outcome: Any) -> ProviderOutcome:
        if not isinstance(outcome, ProviderOutcome):
            raise ProviderContractError("provider must return ProviderOutcome")
        if not isinstance(outcome.decision, str) or not outcome.decision:
            raise ProviderContractError("provider decision must be a non-empty string")
        if not isinstance(outcome.result, Mapping):
            raise ProviderContractError("provider result must be a mapping")
        if not isinstance(outcome.raw_response, (str, bytes)):
            raise ProviderContractError("provider raw_response must be text or bytes")
        if outcome.parsed_response is not None and not isinstance(outcome.parsed_response, Mapping):
            raise ProviderContractError("provider parsed_response must be a mapping or null")
        if type(outcome.abstained) is not bool or type(outcome.claims_physical_pass) is not bool:
            raise ProviderContractError("provider abstain and physical-pass flags must be booleans")
        if not isinstance(outcome.resource, ResourceUsage):
            raise ProviderContractError("provider resource must be ResourceUsage")
        resource = asdict(outcome.resource)
        if any(type(value) is not int or value < 0 for value in resource.values()):
            raise ProviderContractError("provider resource counters must be non-negative integers")
        try:
            protocol.canonical_json_bytes(outcome.result)
            if outcome.parsed_response is not None:
                protocol.canonical_json_bytes(outcome.parsed_response)
        except (TypeError, ValueError) as error:
            raise ProviderContractError(
                "provider outcome must be canonical-JSON serializable"
            ) from error
        return outcome

    @staticmethod
    def _contains_physical_claim(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
                if normalized in {
                    "physical_pass",
                    "physics_pass",
                    "runtime_pass",
                    "authoritative_physical_pass",
                } and not (item is False or item is None or item == "false" or item == "unknown"):
                    return True
                if ExperimentRunner._contains_physical_claim(item):
                    return True
            return False
        if isinstance(value, (list, tuple)):
            return any(ExperimentRunner._contains_physical_claim(item) for item in value)
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in {
                "physical_pass",
                "physics_pass",
                "runtime_pass",
                "authoritative_physical_pass",
            }:
                return True
            # Provider prose is advisory evidence, never a physical decision.
            # Reject common affirmative formulations instead of allowing a
            # natural-language claim to sidestep the structured-key guard.
            compact = re.sub(r"\s+", " ", value.casefold()).strip()
            return any(
                phrase in compact
                for phrase in (
                    "physical gate passed",
                    "physics passed",
                    "physically passed",
                    "runtime validation passed",
                    "authoritative physical pass",
                    "deterministic validator passed",
                    "simulation passed",
                )
            )
        return False

    @staticmethod
    def _validate_visible_arm_outcome(
        outcome: ProviderOutcome,
        *,
        arm: str,
        visible_checks: Sequence[str],
    ) -> None:
        if ExperimentRunner._contains_physical_claim(outcome.result):
            raise ProviderPhysicalClaimError("visible provider output contains a physical pass")
        result = outcome.result
        if arm == "A0_current_critic_3b":
            if (
                set(result) != {"schema_version", "status", "checks"}
                or result.get("schema_version") != "robotwin.rendered_scene_critic.v1"
            ):
                raise ProviderFormatError("A0 result must use the frozen critic schema")
            if result.get("status") not in {"pass", "fail"}:
                raise ProviderFormatError("A0 result status must be pass or fail")
            checks = result.get("checks")
            required = {
                "object_presence",
                "support_relation",
                "penetration_or_floating",
                "articulation_state",
                "overall_prompt_match",
            }
            if (
                not isinstance(checks, list)
                or len(checks) != len(required)
                or {item.get("name") for item in checks if isinstance(item, Mapping)} != required
            ):
                raise ProviderFormatError(
                    "A0 result must contain every frozen critic check exactly once"
                )
            if any(
                not isinstance(item, Mapping)
                or set(item) != {"name", "status", "evidence"}
                or item.get("status") not in {"pass", "fail", "warning", "not_applicable"}
                or not isinstance(item.get("evidence"), str)
                for item in checks
            ):
                raise ProviderFormatError("A0 check schema is invalid")
            expected_status = "fail" if any(item["status"] == "fail" for item in checks) else "pass"
            if result["status"] != expected_status:
                raise ProviderFormatError("A0 overall status is inconsistent with checks")
            if outcome.abstained:
                raise ProviderFormatError("A0 frozen baseline cannot abstain")
            if outcome.parsed_response is None or protocol.canonical_sha256(
                outcome.parsed_response
            ) != protocol.canonical_sha256(result):
                raise ProviderFormatError("A0 parsed response must equal the result")
            return
        required_keys = {"schema_version", "checks", "overall", "corrections"}
        if set(result) != required_keys or result.get("schema_version") != (
            "vlm_fallback.visible_review.v1"
        ):
            raise ProviderFormatError("typed visible result schema is incomplete")
        checks = result.get("checks")
        if not isinstance(checks, Mapping) or set(checks) != set(visible_checks):
            raise ProviderFormatError("typed visible result must contain every frozen check")
        allowed_statuses = {"pass", "fail", "not_applicable", "abstain"}
        if any(status not in allowed_statuses for status in checks.values()):
            raise ProviderFormatError("typed visible check status is invalid")
        overall = result.get("overall")
        if overall not in {"pass", "fail", "review_required"}:
            raise ProviderFormatError("typed visible overall status is invalid")
        corrections = result.get("corrections")
        if not isinstance(corrections, list) or any(
            not isinstance(item, Mapping)
            or set(item) != {"check", "instruction"}
            or item.get("check") not in checks
            or not isinstance(item.get("instruction"), str)
            or not item.get("instruction")
            for item in corrections
        ):
            raise ProviderFormatError("typed visible corrections are invalid")
        requires_abstain = "abstain" in checks.values() or overall == "review_required"
        if outcome.abstained != requires_abstain:
            raise ProviderFormatError("typed visible abstain flag is inconsistent")
        expected_overall = (
            "review_required"
            if "abstain" in checks.values()
            else "fail"
            if "fail" in checks.values()
            else "pass"
        )
        if overall != expected_overall:
            raise ProviderFormatError("typed visible overall status is inconsistent with checks")
        if outcome.parsed_response is None or protocol.canonical_sha256(
            outcome.parsed_response
        ) != protocol.canonical_sha256(result):
            raise ProviderFormatError("typed visible parsed response must equal the result")

    @staticmethod
    def _validate_lower_sha256(value: Any, *, field: str) -> str:
        if not isinstance(value, str) or len(value) != 64 or value != value.lower():
            raise ProviderContractError(f"{field} must be lowercase hex")
        try:
            int(value, 16)
        except ValueError as error:
            raise ProviderContractError(f"{field} must be lowercase hex") from error
        return value

    def _verify_routing_intent(
        self,
        *,
        outcome: ProviderOutcome,
        original_prompt: str,
        revised_prompt: str | None,
    ) -> IntentVerification:
        result = outcome.result
        if set(result) != {
            "route",
            "revised_prompt",
            "intent_sha256_before",
            "intent_sha256_after",
        }:
            raise ProviderContractError("routing result must use the exact frozen schema")
        if self._contains_physical_claim(result):
            raise ProviderPhysicalClaimError("routing provider output contains a physical pass")
        if outcome.parsed_response is None or protocol.canonical_sha256(
            outcome.parsed_response
        ) != protocol.canonical_sha256(result):
            raise ProviderContractError("routing parsed response must equal the result")
        provider_before = self._validate_lower_sha256(
            result.get("intent_sha256_before"),
            field="intent_sha256_before",
        )
        provider_after = self._validate_lower_sha256(
            result.get("intent_sha256_after"),
            field="intent_sha256_after",
        )
        if revised_prompt is None or revised_prompt == original_prompt:
            prompt_sha = _sha256_bytes(original_prompt.encode("utf-8"))
            verification = IntentVerification(
                before_sha256=prompt_sha,
                after_sha256=prompt_sha,
                preserved=True,
                verifier_id="byte_identity",
                verifier_implementation_sha256=_sha256_bytes(b"vlm_fallback.byte_identity.v1"),
            )
        else:
            verification = self._intent_verifier.verify(original_prompt, revised_prompt)
            if not isinstance(verification, IntentVerification):
                raise ProviderContractError("intent verifier returned an invalid result")
        before = self._validate_lower_sha256(
            verification.before_sha256,
            field="verified intent_sha256_before",
        )
        after = self._validate_lower_sha256(
            verification.after_sha256,
            field="verified intent_sha256_after",
        )
        self._validate_lower_sha256(
            verification.verifier_implementation_sha256,
            field="intent verifier implementation_sha256",
        )
        if (
            not verification.verifier_id
            or type(verification.preserved) is not bool
            or verification.preserved != (before == after)
        ):
            raise ProviderContractError("intent verifier result is internally inconsistent")
        if provider_before != before or provider_after != after:
            raise ProviderContractError("provider intent digests do not match trusted verification")
        if not verification.preserved:
            raise ProviderContractError("trusted intent verifier rejected the prompt rewrite")
        return verification

    @staticmethod
    def _validate_visible_invocation(invocation: VisibleInvocation) -> None:
        if invocation.attempt < 1:
            raise ValueError("attempt must be at least one")
        if invocation.repair_index not in {0, 1}:
            raise ValueError("visible repair_index must be zero or one")
        if invocation.repair_index == 0 and invocation.repair_of is not None:
            raise ValueError("base visible invocation cannot declare repair_of")
        if invocation.repair_index == 1 and not invocation.repair_of:
            raise ValueError("format repair must bind repair_of")
        if invocation.repair_index == 0 and (
            invocation.repair_source_prompt is not None
            or invocation.repair_source_response is not None
        ):
            raise ValueError("base visible invocation cannot declare repair source bytes")
        if invocation.repair_index == 1 and (
            not isinstance(invocation.repair_source_prompt, str)
            or not invocation.repair_source_prompt
            or not isinstance(invocation.repair_source_response, (str, bytes))
        ):
            raise ValueError("format repair must bind exact source prompt and response bytes")
        if not invocation.prompt or not invocation.prompt_template_version:
            raise ValueError("prompt and prompt_template_version must be non-empty")
        if not isinstance(invocation.processor_config, Mapping):
            raise ValueError("processor_config must be a mapping")
        if not isinstance(invocation.resource_reservation, ResourceUsage) or any(
            type(value) is not int or value < 0
            for value in asdict(invocation.resource_reservation).values()
        ):
            raise ValueError("resource_reservation must contain non-negative integer counters")
        if invocation.resolved_scene_sha256 is not None:
            value = invocation.resolved_scene_sha256
            try:
                int(value, 16)
            except ValueError as error:
                raise ValueError("resolved_scene_sha256 must be lowercase hex") from error
            if len(value) != 64 or value != value.lower():
                raise ValueError("resolved_scene_sha256 must be lowercase hex")

    def execute(self, invocation: VisibleInvocation | RoutingInvocation) -> RunReceipt:
        with self._journal.serialized_operation():
            self._recover_interrupted_invocations()
            if self._journal.anchor.get("stopped"):
                raise StudyStoppedError(
                    str(self._journal.anchor.get("stop_reason") or "study stopped")
                )
            if not isinstance(invocation, (VisibleInvocation, RoutingInvocation)):
                raise TypeError("invocation must be VisibleInvocation or RoutingInvocation")
            experiment_id = (
                "A_visible_semantic_correction"
                if isinstance(invocation, VisibleInvocation)
                else "B_typed_failure_prompt_fallback"
            )
            try:
                self._refresh_execution_snapshot()
            except RunnerIntegrityError as error:
                self._record_stop(
                    reason=f"frozen input drift: {error}",
                    experiment_id=experiment_id,
                    case_id=invocation.case_id,
                    arm=invocation.arm,
                    attempt=invocation.attempt,
                    triggering_event_id=None,
                )
            annotation_manifest = self._annotation_manifest
            annotation_ready = annotation_manifest["state"] == "sealed"
            if self.config.execution_mode == "production":
                annotation_ready = annotation_ready and (
                    annotation_manifest.get("execution_authorized") is True
                    and annotation_manifest.get("provider_execution_allowed_now") is True
                )
            if not annotation_ready:
                self._record_stop(
                    reason="sealed blinded annotations are required before any arm execution",
                    experiment_id=experiment_id,
                    case_id=invocation.case_id,
                    arm=invocation.arm,
                    attempt=invocation.attempt,
                    triggering_event_id=None,
                    extra_input_bindings={
                        "annotation_manifest_sha256": self._annotation_manifest_sha256,
                        "annotation_state": annotation_manifest["state"],
                    },
                    extra_gate_results={"sealed_annotation_contract": "fail"},
                )
            try:
                if isinstance(invocation, VisibleInvocation):
                    return self._execute_visible(invocation)
                return self._execute_routing(invocation)
            except BaseException:
                # SystemExit/KeyboardInterrupt bypass normal provider exception
                # handling.  Close any durable reservation as unknown and stop;
                # an unknown resource spend can never be reconstructed as zero.
                self._recover_interrupted_invocations()
                raise

    def execute_plan(
        self,
        invocations: Sequence[VisibleInvocation | RoutingInvocation],
    ) -> tuple[RunReceipt, ...]:
        if not invocations:
            raise ValueError("execution plan must contain at least one invocation")
        total = len(invocations)
        self._emit("plan.started", completed=0, total=total)
        receipts: list[RunReceipt] = []
        for index, invocation in enumerate(invocations):
            self._emit("plan.item_started", completed=index, total=total)
            receipts.append(self.execute(invocation))
            self._emit("plan.item_completed", completed=index + 1, total=total)
        self._emit("plan.completed", completed=total, total=total)
        return tuple(receipts)

    def freeze_visible_prompt(self, freeze: VisiblePromptFreeze) -> RunReceipt:
        with self._journal.serialized_operation():
            return self._freeze_visible_prompt(freeze)

    def _validate_prompt_freeze_evidence(
        self,
        freeze: VisiblePromptFreeze,
        *,
        prompt_sha256: str,
        processor_config_sha256: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        assert freeze.selection_evidence_path is not None
        assert freeze.gold_seal_path is not None
        assert freeze.gold_seal_sha256 is not None
        selection = _load_canonical_bound_json(
            freeze.selection_evidence_path,
            expected_sha256=freeze.selection_evidence_sha256,
            label="visible prompt selection evidence",
        )
        gold_seal = _load_canonical_bound_json(
            freeze.gold_seal_path,
            expected_sha256=freeze.gold_seal_sha256,
            label="visible test gold seal",
        )
        required_gold = {
            "schema_version",
            "study_id",
            "spec_sha256",
            "state",
            "test_gold_opened",
            "annotation_manifest_sha256",
        }
        if set(gold_seal) != required_gold or (
            gold_seal.get("schema_version") != VISIBLE_GOLD_SEAL_SCHEMA
            or gold_seal.get("study_id") != self.identity.study_id
            or gold_seal.get("spec_sha256") != self.identity.spec_sha256
            or gold_seal.get("state") != "sealed"
            or gold_seal.get("test_gold_opened") is not False
        ):
            raise RunnerIntegrityError("visible test gold seal contract mismatch")
        annotation_sha = gold_seal.get("annotation_manifest_sha256")
        try:
            int(annotation_sha, 16)
        except (TypeError, ValueError) as error:
            raise RunnerIntegrityError(
                "visible test gold seal annotation digest is invalid"
            ) from error
        if len(annotation_sha) != 64 or annotation_sha != annotation_sha.lower():
            raise RunnerIntegrityError("visible test gold seal annotation digest is invalid")
        annotation_manifest = _load_sealed_test_annotation_manifest()
        if annotation_sha != SEALED_TEST_ANNOTATION_MANIFEST_SHA256:
            raise RunnerIntegrityError("visible test gold seal is not spec-committed")
        if annotation_manifest["state"] != "sealed":
            raise RunnerIntegrityError("sealed test annotations are not available for execution")
        if freeze.dev_gold_manifest_path is None:
            raise RunnerIntegrityError("selected prompt requires a sealed dev gold manifest")
        dev_gold_sha256 = annotation_manifest["dev_gold_manifest_sha256"]
        dev_gold = _load_canonical_bound_json(
            freeze.dev_gold_manifest_path,
            expected_sha256=dev_gold_sha256,
            label="visible dev gold manifest",
        )
        experiment = self.spec["experiments"][0]
        expected_dev_cases = {
            sample["case_id"] for sample in experiment["samples"] if sample["split"] == "dev"
        }
        expected_dev_groups = {
            sample["case_id"]: sample["group_id"]
            for sample in experiment["samples"]
            if sample["split"] == "dev"
        }
        required_dev_gold = {
            "schema_version",
            "study_id",
            "spec_sha256",
            "label_contract_sha256",
            "rater_contract_sha256",
            "adjudication_contract_sha256",
            "rows",
        }
        if set(dev_gold) != required_dev_gold or (
            dev_gold.get("schema_version") != VISIBLE_DEV_GOLD_SCHEMA
            or dev_gold.get("study_id") != self.identity.study_id
            or dev_gold.get("spec_sha256") != self.identity.spec_sha256
            or any(
                dev_gold.get(field) != annotation_manifest[field]
                for field in (
                    "label_contract_sha256",
                    "rater_contract_sha256",
                    "adjudication_contract_sha256",
                )
            )
        ):
            raise RunnerIntegrityError("visible dev gold manifest contract mismatch")
        dev_gold_rows = dev_gold.get("rows")
        if (
            not isinstance(dev_gold_rows, list)
            or len(dev_gold_rows) != len(expected_dev_cases)
            or any(
                not isinstance(row, Mapping)
                or set(row) != {"case_id", "gold"}
                or not isinstance(row.get("gold"), Mapping)
                for row in dev_gold_rows
            )
            or {row["case_id"] for row in dev_gold_rows} != expected_dev_cases
        ):
            raise RunnerIntegrityError("visible dev gold manifest rows are incomplete")
        gold_by_case = {str(row["case_id"]): dict(row["gold"]) for row in dev_gold_rows}
        required_selection = {
            "schema_version",
            "study_id",
            "spec_sha256",
            "selected_prompt_sha256",
            "prompt_template_version",
            "processor_config_sha256",
            "selection_order",
            "candidate_evaluations",
            "train_revision_set_sha256",
            "dev_gold_manifest_sha256",
            "gold_seal_sha256",
            "test_gold_opened",
        }
        arm = next(
            item for item in experiment["arms"] if item["arm"] == "A1_typed_abstaining_critic_3b"
        )
        if set(selection) != required_selection or (
            selection.get("schema_version") != VISIBLE_SELECTION_SCHEMA
            or selection.get("study_id") != self.identity.study_id
            or selection.get("spec_sha256") != self.identity.spec_sha256
            or selection.get("selected_prompt_sha256") != prompt_sha256
            or selection.get("prompt_template_version") != freeze.prompt_template_version
            or selection.get("processor_config_sha256") != processor_config_sha256
            or selection.get("selection_order") != list(arm["selection_order"])
            or selection.get("dev_gold_manifest_sha256") != dev_gold_sha256
            or selection.get("gold_seal_sha256") != freeze.gold_seal_sha256
            or selection.get("test_gold_opened") is not False
        ):
            raise RunnerIntegrityError("visible prompt selection evidence contract mismatch")
        events_by_id = {event["event_id"]: event for event in self._journal.events}
        train_events = [
            event
            for event in self._journal.events
            if event.get("decision") == "provider_call_completed"
            and event.get("phase") == "execution.train"
            and event.get("arm") == "A1_typed_abstaining_critic_3b"
            and event.get("input_bindings", {}).get("repair_index") == 0
            and event.get("gate_results", {}).get("resource_budget") == "pass"
        ]
        observed_train_revisions = {
            str(event.get("model_receipt", {}).get("prompt_sha256")) for event in train_events
        }
        candidates = selection.get("candidate_evaluations")
        if (
            not isinstance(candidates, list)
            or not candidates
            or len(candidates) > int(experiment["budget"]["max_train_prompt_revisions"])
        ):
            raise RunnerIntegrityError("visible prompt selection candidate set is invalid")
        candidate_shas = [candidate.get("prompt_sha256") for candidate in candidates]
        if (
            any(not isinstance(value, str) for value in candidate_shas)
            or len(set(candidate_shas)) != len(candidate_shas)
            or set(candidate_shas) != observed_train_revisions
            or selection.get("train_revision_set_sha256")
            != protocol.canonical_sha256(sorted(candidate_shas))
        ):
            raise RunnerIntegrityError("visible prompt selection train revisions are incomplete")

        scored: list[tuple[tuple[Any, ...], Mapping[str, Any]]] = []
        required_candidate = {
            "prompt_sha256",
            "prompt_template_version",
            "processor_config_sha256",
            "train_event_ids",
            "dev_event_ids",
            "dev_rows",
            "dev_metrics",
            "dev_wall_time_ms",
            "prompt_size_bytes",
        }
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or set(candidate) != required_candidate:
                raise RunnerIntegrityError("visible prompt selection candidate contract mismatch")
            candidate_sha = str(candidate["prompt_sha256"])
            candidate_train_events = [
                event
                for event in train_events
                if event["model_receipt"]["prompt_sha256"] == candidate_sha
            ]
            train_event_ids = candidate.get("train_event_ids")
            if (
                not isinstance(train_event_ids, list)
                or set(train_event_ids) != {event["event_id"] for event in candidate_train_events}
                or not train_event_ids
            ):
                raise RunnerIntegrityError(
                    "visible prompt selection train revisions are incomplete"
                )
            dev_event_ids = candidate.get("dev_event_ids")
            if (
                not isinstance(dev_event_ids, list)
                or len(dev_event_ids) != len(expected_dev_cases)
                or len(set(dev_event_ids)) != len(dev_event_ids)
            ):
                raise RunnerIntegrityError(
                    "visible prompt selection lacks complete frozen dev evidence"
                )
            candidate_events: dict[str, Mapping[str, Any]] = {}
            for event_id in dev_event_ids:
                event = events_by_id.get(event_id)
                if (
                    event is None
                    or event.get("decision") != "provider_call_completed"
                    or event.get("phase") != "execution.dev"
                    or event.get("arm") != "A1_typed_abstaining_critic_3b"
                    or event.get("input_bindings", {}).get("repair_index") != 0
                    or event.get("model_receipt", {}).get("prompt_sha256") != candidate_sha
                    or event.get("model_receipt", {}).get("prompt_template_version")
                    != candidate.get("prompt_template_version")
                    or event.get("model_receipt", {}).get("processor_config_sha256")
                    != candidate.get("processor_config_sha256")
                    or event.get("gate_results", {}).get("resource_budget") != "pass"
                ):
                    raise RunnerIntegrityError(
                        "visible prompt selection lacks complete frozen dev evidence"
                    )
                candidate_events[str(event["case_id"])] = event
            if set(candidate_events) != expected_dev_cases:
                raise RunnerIntegrityError(
                    "visible prompt selection lacks complete frozen dev evidence"
                )
            rows = candidate.get("dev_rows")
            row_case_ids = (
                [row.get("case_id") for row in rows if isinstance(row, Mapping)]
                if isinstance(rows, list)
                else []
            )
            if (
                not isinstance(rows, list)
                or len(rows) != len(expected_dev_cases)
                or len(row_case_ids) != len(rows)
                or any(not isinstance(case_id, str) for case_id in row_case_ids)
                or len(set(row_case_ids)) != len(row_case_ids)
                or set(row_case_ids) != expected_dev_cases
                or any(
                    not isinstance(row, Mapping)
                    or set(row) != {"case_id", "group_id", "gold", "prediction"}
                    or row.get("case_id") not in candidate_events
                    or row.get("group_id") != expected_dev_groups.get(str(row.get("case_id")))
                    or row.get("gold") != gold_by_case.get(str(row.get("case_id")))
                    or row.get("prediction")
                    != candidate_events[str(row.get("case_id"))]
                    .get("outputs", {})
                    .get("result", {})
                    .get("checks")
                    for row in rows
                )
            ):
                raise RunnerIntegrityError(
                    "visible prompt selection dev rows are not receipt-bound"
                )
            observed_metrics = protocol.visible_metrics([dict(row) for row in rows])
            if candidate.get("dev_metrics") != observed_metrics:
                raise RunnerIntegrityError("visible prompt selection dev metrics mismatch")
            if float(observed_metrics["insufficient_view_non_abstain_count"]) != 0.0:
                raise RunnerIntegrityError(
                    "insufficient_view gold requires model abstention before prompt freeze"
                )
            wall_time_ms = sum(
                int(event.get("resource_receipt", {}).get("wall_time_ms", 0))
                for event in candidate_events.values()
            )
            prompt_sizes = {
                event.get("input_bindings", {}).get("prompt_size_bytes")
                for event in (*candidate_train_events, *candidate_events.values())
            }
            if (
                candidate.get("dev_wall_time_ms") != wall_time_ms
                or len(prompt_sizes) != 1
                or candidate.get("prompt_size_bytes") != next(iter(prompt_sizes))
            ):
                raise RunnerIntegrityError("visible prompt selection cost evidence mismatch")
            metrics = observed_metrics
            selective = (
                float(metrics["selective_accuracy"]) if float(metrics["coverage"]) >= 0.80 else -1.0
            )
            score = (
                int(metrics["unsafe_visible_pass_count"] == 0),
                float(metrics["macro_f1_fail"]),
                selective,
                -(wall_time_ms / len(candidate_events)),
                -int(candidate["prompt_size_bytes"]),
            )
            scored.append((score, candidate))
        best_score = max(score for score, _ in scored)
        winners = [candidate for score, candidate in scored if score == best_score]
        if len(winners) != 1 or winners[0].get("prompt_sha256") != prompt_sha256:
            raise RunnerIntegrityError("selected prompt does not satisfy the frozen selection rule")
        selected_candidate = winners[0]
        if (
            selected_candidate.get("prompt_template_version") != freeze.prompt_template_version
            or selected_candidate.get("processor_config_sha256") != processor_config_sha256
        ):
            raise RunnerIntegrityError("selected prompt candidate binding mismatch")
        return selection, gold_seal

    def _freeze_visible_prompt(self, freeze: VisiblePromptFreeze) -> RunReceipt:
        if not freeze.prompt or not freeze.prompt_template_version:
            raise ValueError("selected prompt and template version must be non-empty")
        if not isinstance(freeze.processor_config, Mapping):
            raise ValueError("selected prompt processor_config must be a mapping")
        try:
            int(freeze.selection_evidence_sha256, 16)
        except ValueError as error:
            raise ValueError("selection_evidence_sha256 must be lowercase hex") from error
        if (
            len(freeze.selection_evidence_sha256) != 64
            or freeze.selection_evidence_sha256 != freeze.selection_evidence_sha256.lower()
        ):
            raise ValueError("selection_evidence_sha256 must be lowercase hex")
        try:
            self._refresh_execution_snapshot()
        except RunnerIntegrityError as error:
            self._record_stop(
                reason=f"frozen input drift: {error}",
                experiment_id="A_visible_semantic_correction",
                case_id=None,
                arm="A1_typed_abstaining_critic_3b",
                attempt=0,
                triggering_event_id=None,
            )
        if (
            freeze.selection_evidence_path is None
            or freeze.gold_seal_path is None
            or freeze.gold_seal_sha256 is None
        ):
            raise RunnerIntegrityError(
                "selected prompt requires resolvable selection evidence and gold seal"
            )
        prompt_sha = _sha256_bytes(freeze.prompt.encode("utf-8"))
        processor_sha = protocol.canonical_sha256(freeze.processor_config)
        selection, gold_seal = self._validate_prompt_freeze_evidence(
            freeze,
            prompt_sha256=prompt_sha,
            processor_config_sha256=processor_sha,
        )
        model_value = next(
            item for item in self.spec["models"] if item["role"] == "primary_local_vlm"
        )
        verified_model = self._model_bindings["primary_local_vlm"]
        content_manifest_sha256 = model_content.manifest_sha256(verified_model.manifest)
        roster_sha256 = (
            verified_model.snapshot.roster_sha256 if verified_model.snapshot is not None else None
        )
        logical_key = protocol.canonical_sha256(
            {"kind": "selected_visible_prompt", "arm": "A1_typed_abstaining_critic_3b"}
        )
        request_value = {
            "kind": "selected_visible_prompt",
            "arm": "A1_typed_abstaining_critic_3b",
            "prompt_sha256": prompt_sha,
            "prompt_template_version": freeze.prompt_template_version,
            "processor_config": freeze.processor_config,
            "selection_evidence_sha256": freeze.selection_evidence_sha256,
            "gold_seal_sha256": freeze.gold_seal_sha256,
            "dev_gold_manifest_sha256": selection["dev_gold_manifest_sha256"],
            "train_revision_set_sha256": selection["train_revision_set_sha256"],
            "candidate_evaluations_sha256": protocol.canonical_sha256(
                selection["candidate_evaluations"]
            ),
            "model_revision": model_value["revision"],
            "model_content_manifest_sha256": content_manifest_sha256,
            "model_roster_sha256": roster_sha256,
            "spec_sha256": self.identity.spec_sha256,
            "source_manifest_sha256": self.identity.source_manifest_sha256,
        }
        request_sha = protocol.canonical_sha256(request_value)
        for event in self._journal.events:
            bindings = event.get("input_bindings", {})
            if bindings.get("logical_key") != logical_key:
                continue
            if bindings.get("request_sha256") != request_sha:
                raise DuplicateInvocationError("the selected visible prompt is already frozen")
            self._emit(
                "prompt_freeze.resumed",
                experiment_id="A_visible_semantic_correction",
                arm="A1_typed_abstaining_critic_3b",
                invocation_id=request_sha,
                durable_event_id=event["event_id"],
            )
            return RunReceipt(invocation_id=request_sha, event=event, resumed=True)
        event = self._journal.append(
            {
                "schema_version": self.spec["logging_contract"]["schema_version"],
                "event_id": f"prompt-freeze-{request_sha}",
                "timestamp_utc": _utc_now(),
                "study_id": self.identity.study_id,
                "experiment_id": "A_visible_semantic_correction",
                "phase": "prompt_selection",
                "case_id": None,
                "arm": "A1_typed_abstaining_critic_3b",
                "attempt": 0,
                "decision": "selected_prompt_frozen",
                "input_bindings": {
                    "logical_key": logical_key,
                    "request_sha256": request_sha,
                    "spec_sha256": self.identity.spec_sha256,
                    "source_manifest_sha256": self.identity.source_manifest_sha256,
                    "selection_evidence_sha256": freeze.selection_evidence_sha256,
                    "gold_seal_sha256": freeze.gold_seal_sha256,
                    "dev_gold_manifest_sha256": selection["dev_gold_manifest_sha256"],
                    "train_revision_set_sha256": selection["train_revision_set_sha256"],
                    "candidate_evaluations_sha256": protocol.canonical_sha256(
                        selection["candidate_evaluations"]
                    ),
                },
                "model_receipt": {
                    "prompt_sha256": prompt_sha,
                    "prompt_template_version": freeze.prompt_template_version,
                    "model_id": model_value["model_id"],
                    "model_revision": model_value["revision"],
                    "snapshot_manifest_sha256": model_value["snapshot_manifest_sha256"],
                    "model_content_manifest_sha256": content_manifest_sha256,
                    "model_roster_sha256": roster_sha256,
                    "processor_config_sha256": processor_sha,
                    "input_image_sha256": None,
                    "resolved_scene_sha256": None,
                    "seed": None,
                    "wall_time_ms": 0,
                    "peak_vram_mib": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "raw_response_sha256": None,
                    "parsed_response_sha256": None,
                },
                "resource_receipt": {"wall_time_ms": 0, **asdict(ResourceUsage())},
                "outputs": {
                    "prompt": freeze.prompt,
                    "prompt_sha256": prompt_sha,
                    "selection_evidence_sha256": freeze.selection_evidence_sha256,
                    "gold_seal_sha256": freeze.gold_seal_sha256,
                    "gold_annotation_manifest_sha256": gold_seal["annotation_manifest_sha256"],
                    "dev_gold_manifest_sha256": selection["dev_gold_manifest_sha256"],
                    "train_revision_set_sha256": selection["train_revision_set_sha256"],
                    "candidate_evaluations_sha256": protocol.canonical_sha256(
                        selection["candidate_evaluations"]
                    ),
                    "abstain": False,
                    "repair": None,
                },
                "gate_results": {
                    "test_gold_opened": False,
                    "selection_evidence_resolved": True,
                    "gold_seal_resolved": True,
                    "physical_gate_authority": self.spec["physical_gate_authority"],
                    "render_used_as_physics_evidence": False,
                    "receipt_hash_bound": True,
                },
                "error": None,
                "expensive_execution_started": False,
            }
        )
        self._emit(
            "prompt_freeze.committed",
            experiment_id="A_visible_semantic_correction",
            arm="A1_typed_abstaining_critic_3b",
            invocation_id=request_sha,
            durable_event_id=event["event_id"],
        )
        return RunReceipt(invocation_id=request_sha, event=event, resumed=False)

    def _record_stop(
        self,
        *,
        reason: str,
        experiment_id: str,
        case_id: str | None,
        arm: str | None,
        attempt: int,
        triggering_event_id: str | None,
        extra_input_bindings: Mapping[str, Any] | None = None,
        extra_gate_results: Mapping[str, Any] | None = None,
    ) -> None:
        stop_identity = protocol.canonical_sha256(
            {
                "reason": reason,
                "experiment_id": experiment_id,
                "case_id": case_id,
                "arm": arm,
                "attempt": attempt,
                "triggering_event_id": triggering_event_id,
                "previous_event_sha256": self._journal.anchor["last_event_sha256"],
            }
        )
        empty_model_receipt = {
            "prompt_sha256": None,
            "prompt_template_version": None,
            "model_id": None,
            "model_revision": None,
            "snapshot_manifest_sha256": None,
            "processor_config_sha256": None,
            "input_image_sha256": None,
            "resolved_scene_sha256": None,
            "seed": None,
            "wall_time_ms": 0,
            "peak_vram_mib": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "raw_response_sha256": None,
            "parsed_response_sha256": None,
        }
        event = self._journal.append(
            {
                "schema_version": self.spec["logging_contract"]["schema_version"],
                "event_id": f"stop-{stop_identity}",
                "timestamp_utc": _utc_now(),
                "study_id": self.identity.study_id,
                "experiment_id": experiment_id,
                "phase": "execution.stop",
                "case_id": case_id,
                "arm": arm,
                "attempt": attempt,
                "decision": "protocol_stopped",
                "input_bindings": {
                    "spec_sha256": self.identity.spec_sha256,
                    "source_manifest_sha256": self.identity.source_manifest_sha256,
                    "triggering_event_id": triggering_event_id,
                    **dict(extra_input_bindings or {}),
                },
                "model_receipt": empty_model_receipt,
                "resource_receipt": {"wall_time_ms": 0, **asdict(ResourceUsage())},
                "outputs": {
                    "stop_reason": reason,
                    "triggering_event_id": triggering_event_id,
                    "abstain": True,
                    "repair": None,
                },
                "gate_results": {
                    "preregistered_stop_rule": "fail",
                    "study_stopped": True,
                    "stop_reason": reason,
                    "stop_rule_violations": [reason],
                    "physical_gate_authority": self.spec["physical_gate_authority"],
                    "render_used_as_physics_evidence": False,
                    "receipt_hash_bound": True,
                    **dict(extra_gate_results or {}),
                },
                "error": {"code": "protocol_stopped", "reason": reason},
                "expensive_execution_started": False,
            }
        )
        self._emit(
            "protocol.stopped",
            experiment_id=experiment_id,
            case_id=case_id,
            arm=arm,
            durable_event_id=event["event_id"],
        )
        raise StudyStoppedError(reason, event_id=event["event_id"])

    def _raise_after_atomic_violation_receipt(
        self,
        *,
        event: Mapping[str, Any],
        reason: str,
        progress_fields: Mapping[str, Any],
    ) -> None:
        """Stop from the same durable receipt transaction that recorded the violation."""

        self._emit("protocol.stopped", durable_event_id=str(event["event_id"]), **progress_fields)
        raise StudyStoppedError(reason, event_id=str(event["event_id"]))

    def _visible_outcome_violations(
        self,
        outcome: ProviderOutcome,
        *,
        model: ModelBinding,
        budget: Mapping[str, Any],
        reservation: ResourceUsage,
        enforce_invocation_count: bool = True,
    ) -> list[str]:
        violations: list[str] = []
        if outcome.resource.output_tokens > model.max_new_tokens:
            violations.append(
                "max_new_tokens exceeded: "
                f"{outcome.resource.output_tokens} > {model.max_new_tokens}"
            )
        if outcome.resource.peak_vram_mib > int(budget["max_peak_vram_mib"]):
            violations.append("max_peak_vram_mib exceeded")
        previous_gpu_ms = sum(
            int(event.get("resource_receipt", {}).get("gpu_time_ms", 0))
            for event in self._journal.events
            if event.get("experiment_id") == "A_visible_semantic_correction"
        )
        max_gpu_ms = int(float(budget["max_gpu_hours"]) * 60 * 60 * 1000)
        if previous_gpu_ms + outcome.resource.gpu_time_ms > max_gpu_ms:
            violations.append("max_gpu_hours exceeded")
        if outcome.resource.network_calls:
            violations.append("network access is forbidden during model execution")
        if outcome.resource.remote_paid_calls:
            violations.append("remote_paid_calls must remain zero")
        if enforce_invocation_count:
            if outcome.resource.visible_vlm_invocations != 1:
                violations.append("visible_vlm_invocations must equal one per provider event")
            if outcome.resource.visible_vlm_invocations > reservation.visible_vlm_invocations:
                violations.append("visible_vlm_invocations exceeded the pre-call reservation")
        if outcome.resource.gpu_time_ms > reservation.gpu_time_ms:
            violations.append("gpu_time_ms exceeded the pre-call reservation")
        if outcome.claims_physical_pass:
            violations.append("VLM provider attempted to claim physical pass")
        return violations

    def _visible_reservation_violations(
        self,
        *,
        reservation: ResourceUsage,
        budget: Mapping[str, Any],
    ) -> list[str]:
        prior = [
            event.get("resource_receipt", {})
            for event in self._journal.events
            if event.get("experiment_id") == "A_visible_semantic_correction"
            and event.get("decision") in {"provider_call_completed", "provider_call_failed"}
        ]
        violations: list[str] = []
        if reservation.visible_vlm_invocations != 1:
            violations.append("each provider call must reserve exactly one visible VLM invocation")
        prior_gpu_ms = sum(int(receipt.get("gpu_time_ms", 0)) for receipt in prior)
        max_gpu_ms = int(float(budget["max_gpu_hours"]) * 60 * 60 * 1000)
        if prior_gpu_ms + reservation.gpu_time_ms > max_gpu_ms:
            violations.append("max_gpu_hours exhausted before provider call")
        max_total_invocations = int(budget["max_base_vlm_invocations"]) * (
            1 + int(budget["max_format_only_repairs_per_invocation"])
        )
        prior_invocations = sum(int(receipt.get("visible_vlm_invocations", 0)) for receipt in prior)
        if prior_invocations + reservation.visible_vlm_invocations > max_total_invocations:
            violations.append("visible VLM invocation budget exhausted before provider call")
        if reservation.network_calls:
            violations.append("network access cannot be reserved")
        if reservation.remote_paid_calls:
            violations.append("remote_paid_calls cannot be reserved")
        return violations

    def _routing_outcome_violations(
        self,
        outcome: ProviderOutcome,
        *,
        case_id: str,
        arm: str,
        revised_prompt: str | None,
        original_prompt: str,
        budget: Mapping[str, Any],
    ) -> list[str]:
        violations: list[str] = []
        usage = outcome.resource
        prior_case_arm_rewrites = sum(
            int(event.get("resource_receipt", {}).get("prompt_rewrites", 0))
            for event in self._journal.events
            if event.get("experiment_id") == "B_typed_failure_prompt_fallback"
            and event.get("case_id") == case_id
            and event.get("arm") == arm
            and event.get("decision")
            in {
                "provider_call_completed",
                "provider_call_failed",
                "provider_call_interrupted_unknown",
            }
        )
        if prior_case_arm_rewrites + usage.prompt_rewrites > int(
            budget["max_prompt_rewrites_per_case_arm"]
        ):
            violations.append("max_prompt_rewrites_per_case_arm exceeded")
        actual_rewrite = revised_prompt is not None and revised_prompt != original_prompt
        if usage.prompt_rewrites != int(actual_rewrite):
            violations.append("prompt rewrite resource receipt does not match output")
        if usage.runtime_steps > usage.fresh_physical_replays * int(
            budget["max_runtime_steps_per_replay"]
        ):
            violations.append("max_runtime_steps_per_replay exceeded")
        if usage.contact_window_steps > usage.fresh_physical_replays * int(
            budget["contact_window_steps"]
        ):
            violations.append("contact_window_steps exceeded")
        prior = [
            event.get("resource_receipt", {})
            for event in self._journal.events
            if event.get("experiment_id") == "B_typed_failure_prompt_fallback"
            and event.get("decision") in {"provider_call_completed", "provider_call_failed"}
        ]
        cumulative_limits = (
            ("compile_attempts", "max_total_compile_attempts"),
            ("fresh_physical_replays", "max_fresh_physical_replays"),
            ("visible_vlm_invocations", "max_vlm_invocations_for_visible_failures"),
        )
        for usage_field, budget_field in cumulative_limits:
            cumulative = sum(int(receipt.get(usage_field, 0)) for receipt in prior) + int(
                getattr(usage, usage_field)
            )
            if cumulative > int(budget[budget_field]):
                violations.append(f"{budget_field} exceeded")
        prior_gpu_ms = sum(int(receipt.get("gpu_time_ms", 0)) for receipt in prior)
        max_gpu_ms = int(float(budget["max_gpu_hours"]) * 60 * 60 * 1000)
        if prior_gpu_ms + usage.gpu_time_ms > max_gpu_ms:
            violations.append("max_gpu_hours exceeded")
        if usage.network_calls:
            violations.append("network access is forbidden during routing execution")
        if usage.remote_paid_calls:
            violations.append("remote_paid_calls must remain zero")
        if outcome.claims_physical_pass:
            violations.append("routing provider attempted to claim physical pass")
        return violations

    def _routing_reservation_violations(
        self,
        *,
        reservation: ResourceUsage,
        case_id: str,
        arm: str,
        budget: Mapping[str, Any],
    ) -> list[str]:
        prior = [
            event.get("resource_receipt", {})
            for event in self._journal.events
            if event.get("experiment_id") == "B_typed_failure_prompt_fallback"
            and event.get("decision") in {"provider_call_completed", "provider_call_failed"}
        ]
        violations: list[str] = []
        prior_case_arm_rewrites = sum(
            int(receipt.get("prompt_rewrites", 0))
            for event, receipt in (
                (event, event.get("resource_receipt", {})) for event in self._journal.events
            )
            if event.get("experiment_id") == "B_typed_failure_prompt_fallback"
            and event.get("case_id") == case_id
            and event.get("arm") == arm
            and event.get("decision") in {"provider_call_completed", "provider_call_failed"}
        )
        if prior_case_arm_rewrites + reservation.prompt_rewrites > int(
            budget["max_prompt_rewrites_per_case_arm"]
        ):
            violations.append("max_prompt_rewrites_per_case_arm exhausted before provider call")
        cumulative_limits = (
            ("compile_attempts", "max_total_compile_attempts"),
            ("fresh_physical_replays", "max_fresh_physical_replays"),
            ("visible_vlm_invocations", "max_vlm_invocations_for_visible_failures"),
        )
        for usage_field, budget_field in cumulative_limits:
            reserved_total = sum(int(receipt.get(usage_field, 0)) for receipt in prior) + int(
                getattr(reservation, usage_field)
            )
            if reserved_total > int(budget[budget_field]):
                violations.append(f"{budget_field} exhausted before provider call")
        if reservation.runtime_steps > reservation.fresh_physical_replays * int(
            budget["max_runtime_steps_per_replay"]
        ):
            violations.append("max_runtime_steps_per_replay exhausted before provider call")
        if reservation.contact_window_steps > reservation.fresh_physical_replays * int(
            budget["contact_window_steps"]
        ):
            violations.append("contact_window_steps exhausted before provider call")
        prior_gpu_ms = sum(int(receipt.get("gpu_time_ms", 0)) for receipt in prior)
        max_gpu_ms = int(float(budget["max_gpu_hours"]) * 60 * 60 * 1000)
        if prior_gpu_ms + reservation.gpu_time_ms > max_gpu_ms:
            violations.append("max_gpu_hours exhausted before provider call")
        if reservation.network_calls:
            violations.append("network access cannot be reserved")
        if reservation.remote_paid_calls:
            violations.append("remote_paid_calls cannot be reserved")
        return violations

    @staticmethod
    def _unreserved_routing_usage(
        usage: ResourceUsage,
        reservation: ResourceUsage,
    ) -> list[str]:
        budgeted_fields = (
            "gpu_time_ms",
            "network_calls",
            "remote_paid_calls",
            "compile_attempts",
            "fresh_physical_replays",
            "runtime_steps",
            "contact_window_steps",
            "prompt_rewrites",
            "visible_vlm_invocations",
        )
        return [
            f"{field} exceeded the pre-call reservation"
            for field in budgeted_fields
            if int(getattr(usage, field)) > int(getattr(reservation, field))
        ]

    def _execute_visible(self, invocation: VisibleInvocation) -> RunReceipt:
        self._validate_visible_invocation(invocation)
        try:
            self._refresh_execution_snapshot()
        except RunnerIntegrityError as error:
            self._record_stop(
                reason=f"frozen input drift: {error}",
                experiment_id="A_visible_semantic_correction",
                case_id=invocation.case_id,
                arm=invocation.arm,
                attempt=invocation.attempt,
                triggering_event_id=None,
            )
        _, _, sample, model, artifacts = self._visible_context(invocation)
        self._validate_a0_prompt_binding(
            invocation,
            sample=sample,
            artifacts=artifacts,
        )
        image_artifacts = [
            item
            for item in artifacts
            if Path(item["path"]).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        if not image_artifacts:
            raise RunnerIntegrityError(f"visible case has no frozen images: {invocation.case_id}")
        budget = self.spec["experiments"][0]["budget"]
        if len(image_artifacts) > int(budget["max_input_images_per_case"]):
            raise RunnerIntegrityError(f"visible case exceeds image budget: {invocation.case_id}")
        artifact_bindings = tuple(ArtifactBinding(**item) for item in artifacts)
        prompt_sha = _sha256_bytes(invocation.prompt.encode("utf-8"))
        processor_config = _canonical_mapping_copy(invocation.processor_config)
        processor_sha = protocol.canonical_sha256(processor_config)
        repair_source_prompt_sha: str | None = None
        repair_source_response_sha: str | None = None
        if invocation.repair_index == 1:
            source_prompt = invocation.repair_source_prompt
            source_response = invocation.repair_source_response
            assert isinstance(source_prompt, str)
            assert isinstance(source_response, (str, bytes))
            source_response_bytes = (
                source_response.encode("utf-8")
                if isinstance(source_response, str)
                else source_response
            )
            repair_source_prompt_sha = _sha256_bytes(source_prompt.encode("utf-8"))
            repair_source_response_sha = _sha256_bytes(source_response_bytes)
        selected_prompt_event = next(
            (
                event
                for event in self._journal.events
                if event.get("decision") == "selected_prompt_frozen"
            ),
            None,
        )
        if sample["split"] == "test":
            if selected_prompt_event is None:
                self._record_stop(
                    reason="sealed visible test attempted before selected prompt freeze",
                    experiment_id="A_visible_semantic_correction",
                    case_id=invocation.case_id,
                    arm=invocation.arm,
                    attempt=invocation.attempt,
                    triggering_event_id=None,
                )
            if invocation.arm in {
                "A1_typed_abstaining_critic_3b",
                "A2_typed_abstaining_critic_7b",
            } and (
                selected_prompt_event["model_receipt"]["prompt_sha256"] != prompt_sha
                or selected_prompt_event["model_receipt"]["prompt_template_version"]
                != invocation.prompt_template_version
                or selected_prompt_event["model_receipt"]["processor_config_sha256"]
                != processor_sha
            ):
                self._record_stop(
                    reason="sealed visible test prompt differs from selected prompt freeze",
                    experiment_id="A_visible_semantic_correction",
                    case_id=invocation.case_id,
                    arm=invocation.arm,
                    attempt=invocation.attempt,
                    triggering_event_id=selected_prompt_event["event_id"],
                )
        elif invocation.arm == "A2_typed_abstaining_critic_7b":
            self._record_stop(
                reason="A2 ceiling is restricted to the sealed test split",
                experiment_id="A_visible_semantic_correction",
                case_id=invocation.case_id,
                arm=invocation.arm,
                attempt=invocation.attempt,
                triggering_event_id=None,
            )
        prior_base_events = [
            event
            for event in self._journal.events
            if event.get("experiment_id") == "A_visible_semantic_correction"
            and event.get("decision")
            in {
                "provider_call_completed",
                "provider_call_failed",
                "provider_call_interrupted_unknown",
            }
            and event.get("input_bindings", {}).get("repair_index") == 0
        ]
        if invocation.repair_index == 0 and len(prior_base_events) >= int(
            budget["max_base_vlm_invocations"]
        ):
            self._record_stop(
                reason="max_base_vlm_invocations exhausted before provider call",
                experiment_id="A_visible_semantic_correction",
                case_id=invocation.case_id,
                arm=invocation.arm,
                attempt=invocation.attempt,
                triggering_event_id=None,
            )
        if (
            invocation.repair_index == 0
            and invocation.arm == "A1_typed_abstaining_critic_3b"
            and sample["split"] == "train"
        ):
            prior_train_prompt_shas = {
                event.get("model_receipt", {}).get("prompt_sha256")
                for event in prior_base_events
                if event.get("arm") == invocation.arm and event.get("phase") == "execution.train"
            }
            if prompt_sha not in prior_train_prompt_shas and len(prior_train_prompt_shas) >= int(
                budget["max_train_prompt_revisions"]
            ):
                self._record_stop(
                    reason="max_train_prompt_revisions exhausted before provider call",
                    experiment_id="A_visible_semantic_correction",
                    case_id=invocation.case_id,
                    arm=invocation.arm,
                    attempt=invocation.attempt,
                    triggering_event_id=None,
                )
        logical_value = {
            "kind": "visible",
            "experiment_id": "A_visible_semantic_correction",
            "case_id": invocation.case_id,
            "arm": invocation.arm,
            "attempt": invocation.attempt,
            "repair_index": invocation.repair_index,
        }
        logical_key = protocol.canonical_sha256(logical_value)
        request_value = {
            **logical_value,
            "prompt": invocation.prompt,
            "prompt_template_version": invocation.prompt_template_version,
            "processor_config": processor_config,
            "seed": invocation.seed,
            "resolved_scene_sha256": invocation.resolved_scene_sha256,
            "repair_of": invocation.repair_of,
            "repair_source_prompt_sha256": repair_source_prompt_sha,
            "repair_source_response_sha256": repair_source_response_sha,
            "model": {
                "role": model.role,
                "model_id": model.model_id,
                "revision": model.revision,
                "snapshot_manifest_sha256": model.snapshot_manifest_sha256,
                "model_content_manifest_sha256": model.model_content_manifest_sha256,
                "model_roster_sha256": model.model_roster_sha256,
            },
            "bundle_sha256": sample["bundle_sha256"],
            "source_manifest_sha256": self.identity.source_manifest_sha256,
            "spec_sha256": self.identity.spec_sha256,
            "selected_prompt_event_id": (
                selected_prompt_event["event_id"] if sample["split"] == "test" else None
            ),
            "resource_reservation": asdict(invocation.resource_reservation),
        }
        request_sha = protocol.canonical_sha256(request_value)
        invocation_id = request_sha
        for event in self._journal.events:
            bindings = event.get("input_bindings", {})
            if bindings.get("logical_key") != logical_key:
                continue
            if bindings.get("request_sha256") != request_sha:
                raise DuplicateInvocationError(
                    "logical invocation key already exists with a different request digest"
                )
            self._emit(
                "invocation.resumed",
                experiment_id="A_visible_semantic_correction",
                case_id=invocation.case_id,
                arm=invocation.arm,
                invocation_id=invocation_id,
                durable_event_id=event["event_id"],
            )
            return RunReceipt(invocation_id=invocation_id, event=event, resumed=True)
        if invocation.repair_index == 1:
            parent = next(
                (
                    event
                    for event in self._journal.events
                    if event.get("input_bindings", {}).get("request_sha256") == invocation.repair_of
                    and event.get("decision") == "provider_call_failed"
                ),
                None,
            )
            parent_bindings = parent.get("input_bindings", {}) if parent is not None else {}
            parent_model = parent.get("model_receipt", {}) if parent is not None else {}
            parent_gates = parent.get("gate_results", {}) if parent is not None else {}
            parent_error = parent.get("error", {}) if parent is not None else {}
            source_prompt = invocation.repair_source_prompt
            source_response = invocation.repair_source_response
            assert isinstance(source_prompt, str)
            assert isinstance(source_response, (str, bytes))
            expected_repair_prompt = build_format_only_repair_prompt(
                source_prompt,
                source_response,
            )
            if (
                parent is None
                or parent.get("experiment_id") != "A_visible_semantic_correction"
                or parent.get("case_id") != invocation.case_id
                or parent.get("arm") != invocation.arm
                or parent.get("attempt") != invocation.attempt
                or parent.get("decision") != "provider_call_failed"
                or parent_bindings.get("repair_index") != 0
                or parent_error.get("code") != "provider_contract_error"
                or parent_error.get("exception_type") != "ProviderFormatError"
                or parent_gates.get("format_repair_eligible") is not True
                or repair_source_prompt_sha != parent_model.get("prompt_sha256")
                or repair_source_response_sha != parent_model.get("raw_response_sha256")
                or invocation.prompt != expected_repair_prompt
                or invocation.prompt_template_version != FORMAT_ONLY_REPAIR_TEMPLATE_VERSION
                or processor_sha != parent_model.get("processor_config_sha256")
                or invocation.seed != parent_model.get("seed")
                or invocation.resolved_scene_sha256 != parent_model.get("resolved_scene_sha256")
                or artifacts != parent_bindings.get("artifact_manifest")
                or sample["bundle_sha256"] != parent_bindings.get("sample_bundle_sha256")
            ):
                self._record_stop(
                    reason="format repair is not bound to exact eligible parent bytes",
                    experiment_id="A_visible_semantic_correction",
                    case_id=invocation.case_id,
                    arm=invocation.arm,
                    attempt=invocation.attempt,
                    triggering_event_id=parent.get("event_id") if parent is not None else None,
                )
        reservation_violations = self._visible_reservation_violations(
            reservation=invocation.resource_reservation,
            budget=budget,
        )
        if reservation_violations:
            self._record_stop(
                reason="; ".join(reservation_violations),
                experiment_id="A_visible_semantic_correction",
                case_id=invocation.case_id,
                arm=invocation.arm,
                attempt=invocation.attempt,
                triggering_event_id=None,
                extra_input_bindings={
                    "resource_reservation": asdict(invocation.resource_reservation)
                },
                extra_gate_results={"resource_reservation": "fail"},
            )
        provider = self._visible_provider
        if provider is None:
            raise RunnerIntegrityError("no visible critic provider is configured")
        provider_identity = self._snapshot_provider_identity(provider)
        if not provider_identity.production_eligible and not self.config.allow_test_providers:
            raise RunnerIntegrityError(
                "non-production provider is forbidden by runner configuration"
            )
        if (
            provider_identity.production_eligible
            and provider_identity.kind in {"local_qwen", "local_llm"}
            and invocation.resource_reservation.gpu_time_ms <= 0
        ):
            self._record_stop(
                reason="production visible provider requires a positive GPU reservation",
                experiment_id="A_visible_semantic_correction",
                case_id=invocation.case_id,
                arm=invocation.arm,
                attempt=invocation.attempt,
                triggering_event_id=None,
                extra_input_bindings={
                    "resource_reservation": asdict(invocation.resource_reservation)
                },
                extra_gate_results={"resource_reservation": "fail"},
            )
        image_paths = tuple(self.config.repo_root / item["path"] for item in image_artifacts)
        request = VisibleProviderRequest(
            invocation_id=invocation_id,
            case_id=invocation.case_id,
            arm=invocation.arm,
            task_context=sample["task_context"],
            prompt=invocation.prompt,
            prompt_template_version=invocation.prompt_template_version,
            processor_config=_canonical_mapping_copy(processor_config),
            seed=invocation.seed,
            attempt=invocation.attempt,
            repair_index=invocation.repair_index,
            repair_of=invocation.repair_of,
            resolved_scene_sha256=invocation.resolved_scene_sha256,
            model=model,
            image_paths=image_paths,
            artifacts=artifact_bindings,
            resource_reservation=invocation.resource_reservation,
        )
        progress_fields = {
            "experiment_id": "A_visible_semantic_correction",
            "case_id": invocation.case_id,
            "arm": invocation.arm,
            "invocation_id": invocation_id,
        }
        image_receipts = [
            {"path": item["path"], "sha256": item["sha256"]} for item in image_artifacts
        ]
        reservation = self._reserve_provider_call(
            experiment_id="A_visible_semantic_correction",
            phase=f"execution.{sample['split']}",
            case_id=invocation.case_id,
            arm=invocation.arm,
            attempt=invocation.attempt,
            logical_key=logical_key,
            request_sha=request_sha,
            input_bindings={
                "spec_sha256": self.identity.spec_sha256,
                "source_manifest_sha256": self.identity.source_manifest_sha256,
                "sample_bundle_sha256": sample["bundle_sha256"],
                "artifact_manifest": artifacts,
                "repair_of": invocation.repair_of,
                "repair_index": invocation.repair_index,
                "repair_source_prompt_sha256": repair_source_prompt_sha,
                "repair_source_response_sha256": repair_source_response_sha,
                "prompt_size_bytes": len(invocation.prompt.encode("utf-8")),
                "selected_prompt_event_id": (
                    selected_prompt_event["event_id"] if sample["split"] == "test" else None
                ),
                "resource_reservation": asdict(invocation.resource_reservation),
            },
            model_receipt={
                "prompt_sha256": prompt_sha,
                "prompt_template_version": invocation.prompt_template_version,
                "model_id": model.model_id,
                "model_revision": model.revision,
                "snapshot_manifest_sha256": model.snapshot_manifest_sha256,
                "model_content_manifest_sha256": model.model_content_manifest_sha256,
                "model_roster_sha256": model.model_roster_sha256,
                "processor_config_sha256": processor_sha,
                "input_image_sha256": protocol.canonical_sha256(image_receipts),
                "input_images": image_receipts,
                "resolved_scene_sha256": invocation.resolved_scene_sha256,
                "seed": invocation.seed,
                "wall_time_ms": 0,
                "peak_vram_mib": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "raw_response_sha256": None,
                "parsed_response_sha256": None,
                "provider_id": provider_identity.provider_id,
                "provider_revision": provider_identity.revision,
                "provider_implementation_sha256": provider_identity.implementation_sha256,
                "provider_kind": provider_identity.kind,
            },
            repair={
                "is_format_only_repair": invocation.repair_index == 1,
                "repair_of": invocation.repair_of,
            },
            provider_production_eligible=provider_identity.production_eligible,
        )
        self._emit("provider.started", **progress_fields)
        start_ns = time.monotonic_ns()
        outcome: Any = None
        try:
            outcome = provider.invoke(
                request,
                lambda stage: self._emit(f"provider.{stage}", **progress_fields),
            )
            self._verify_provider_identity_unchanged(provider, provider_identity)
            outcome = self._validate_provider_outcome(outcome)
            self._validate_visible_arm_outcome(
                outcome,
                arm=invocation.arm,
                visible_checks=self.spec["experiments"][0]["visible_checks"],
            )
            resource = asdict(outcome.resource)
        except Exception as caught_error:
            provider_error = caught_error
            try:
                self._verify_provider_identity_unchanged(provider, provider_identity)
            except ProviderContractError as identity_error:
                provider_error = identity_error
            wall_time_ms = max(0, (time.monotonic_ns() - start_ns) // 1_000_000)
            reported_usage_is_valid = (
                isinstance(outcome, ProviderOutcome)
                and isinstance(outcome.resource, ResourceUsage)
                and all(
                    type(value) is int and value >= 0 for value in asdict(outcome.resource).values()
                )
            )
            reported_usage = (
                outcome.resource if reported_usage_is_valid else invocation.resource_reservation
            )
            partial_resource = asdict(reported_usage)
            partial_raw_sha: str | None = None
            if isinstance(outcome, ProviderOutcome) and isinstance(
                outcome.raw_response, (str, bytes)
            ):
                partial_raw = (
                    outcome.raw_response.encode("utf-8")
                    if isinstance(outcome.raw_response, str)
                    else outcome.raw_response
                )
                partial_raw_sha = _sha256_bytes(partial_raw)
            partial_parsed_sha: str | None = None
            if isinstance(outcome, ProviderOutcome) and isinstance(
                outcome.parsed_response, Mapping
            ):
                try:
                    partial_parsed_sha = protocol.canonical_sha256(outcome.parsed_response)
                except (TypeError, ValueError):
                    partial_parsed_sha = None
            partial_result: dict[str, Any] | None = None
            if isinstance(outcome, ProviderOutcome) and isinstance(outcome.result, Mapping):
                try:
                    protocol.canonical_json_bytes(outcome.result)
                except (TypeError, ValueError):
                    partial_result = None
                else:
                    partial_result = dict(outcome.result)
            error_code = (
                "provider_contract_error"
                if isinstance(provider_error, ProviderContractError)
                else "provider_exception"
            )
            format_repair_eligible = (
                reported_usage_is_valid
                and invocation.repair_index == 0
                and isinstance(provider_error, ProviderFormatError)
            )
            provider_claimed_physical_pass = (
                (outcome.claims_physical_pass or self._contains_physical_claim(outcome.result))
                if isinstance(outcome, ProviderOutcome)
                and type(outcome.claims_physical_pass) is bool
                else False
            )
            failure_outcome = ProviderOutcome(
                decision=error_code,
                result={},
                raw_response=b"",
                parsed_response=None,
                abstained=True,
                resource=reported_usage,
                claims_physical_pass=provider_claimed_physical_pass,
            )
            violations = self._visible_outcome_violations(
                failure_outcome,
                model=model,
                budget=budget,
                reservation=invocation.resource_reservation,
                enforce_invocation_count=reported_usage_is_valid,
            )
            if not reported_usage_is_valid:
                violations.append("provider resource usage is unknown after invocation")
            event_body = {
                "schema_version": self.spec["logging_contract"]["schema_version"],
                "event_id": f"call-{invocation_id}",
                "timestamp_utc": _utc_now(),
                "study_id": self.identity.study_id,
                "experiment_id": "A_visible_semantic_correction",
                "phase": f"execution.{sample['split']}",
                "case_id": invocation.case_id,
                "arm": invocation.arm,
                "attempt": invocation.attempt,
                "decision": "provider_call_failed",
                "input_bindings": {
                    "logical_key": logical_key,
                    "request_sha256": request_sha,
                    "spec_sha256": self.identity.spec_sha256,
                    "source_manifest_sha256": self.identity.source_manifest_sha256,
                    "sample_bundle_sha256": sample["bundle_sha256"],
                    "artifact_manifest": artifacts,
                    "repair_of": invocation.repair_of,
                    "repair_index": invocation.repair_index,
                    "repair_source_prompt_sha256": repair_source_prompt_sha,
                    "repair_source_response_sha256": repair_source_response_sha,
                    "prompt_size_bytes": len(invocation.prompt.encode("utf-8")),
                    "selected_prompt_event_id": (
                        selected_prompt_event["event_id"] if sample["split"] == "test" else None
                    ),
                    "reservation_event_id": reservation["event_id"],
                },
                "model_receipt": {
                    "prompt_sha256": prompt_sha,
                    "prompt_template_version": invocation.prompt_template_version,
                    "model_id": model.model_id,
                    "model_revision": model.revision,
                    "snapshot_manifest_sha256": model.snapshot_manifest_sha256,
                    "model_content_manifest_sha256": model.model_content_manifest_sha256,
                    "model_roster_sha256": model.model_roster_sha256,
                    "processor_config_sha256": processor_sha,
                    "input_image_sha256": protocol.canonical_sha256(image_receipts),
                    "input_images": image_receipts,
                    "resolved_scene_sha256": invocation.resolved_scene_sha256,
                    "seed": invocation.seed,
                    "wall_time_ms": wall_time_ms,
                    "peak_vram_mib": partial_resource.get("peak_vram_mib", 0),
                    "input_tokens": partial_resource.get("input_tokens", 0),
                    "output_tokens": partial_resource.get("output_tokens", 0),
                    "raw_response_sha256": partial_raw_sha,
                    "parsed_response_sha256": partial_parsed_sha,
                    "provider_id": provider_identity.provider_id,
                    "provider_revision": provider_identity.revision,
                    "provider_implementation_sha256": provider_identity.implementation_sha256,
                    "provider_kind": provider_identity.kind,
                },
                "resource_receipt": {
                    "wall_time_ms": wall_time_ms,
                    **partial_resource,
                    "usage_known": reported_usage_is_valid,
                },
                "outputs": {
                    "decision": error_code,
                    "result": partial_result,
                    "abstain": True,
                    "repair": {
                        "is_format_only_repair": invocation.repair_index == 1,
                        "repair_of": invocation.repair_of,
                    },
                },
                "gate_results": {
                    "provider_production_eligible": provider_identity.production_eligible,
                    "physical_gate_authority": self.spec["physical_gate_authority"],
                    "render_used_as_physics_evidence": False,
                    "provider_claimed_physical_pass": provider_claimed_physical_pass,
                    "format_repair_eligible": format_repair_eligible,
                    "resource_budget": "fail" if violations else "pass",
                    "stop_rule_violations": violations,
                    "study_stopped": bool(violations),
                    "stop_reason": "; ".join(violations) if violations else None,
                    "receipt_hash_bound": True,
                },
                "error": {
                    "code": error_code,
                    "exception_type": type(provider_error).__name__,
                    "message_sha256": _sha256_bytes(str(provider_error).encode("utf-8")),
                },
                "expensive_execution_started": True,
            }
            event = self._journal.append(event_body)
            self._emit("receipt.fsynced", durable_event_id=event["event_id"], **progress_fields)
            if violations:
                self._raise_after_atomic_violation_receipt(
                    event=event,
                    reason="; ".join(violations),
                    progress_fields=progress_fields,
                )
            self._emit("invocation.failed", durable_event_id=event["event_id"], **progress_fields)
            return RunReceipt(invocation_id=invocation_id, event=event, resumed=False)
        wall_time_ms = max(0, (time.monotonic_ns() - start_ns) // 1_000_000)
        raw_bytes = (
            outcome.raw_response.encode("utf-8")
            if isinstance(outcome.raw_response, str)
            else outcome.raw_response
        )
        parsed_sha = (
            protocol.canonical_sha256(outcome.parsed_response)
            if outcome.parsed_response is not None
            else None
        )
        violations = self._visible_outcome_violations(
            outcome,
            model=model,
            budget=budget,
            reservation=invocation.resource_reservation,
        )
        model_receipt = {
            "prompt_sha256": prompt_sha,
            "prompt_template_version": invocation.prompt_template_version,
            "model_id": model.model_id,
            "model_revision": model.revision,
            "snapshot_manifest_sha256": model.snapshot_manifest_sha256,
            "model_content_manifest_sha256": model.model_content_manifest_sha256,
            "model_roster_sha256": model.model_roster_sha256,
            "processor_config_sha256": processor_sha,
            "input_image_sha256": protocol.canonical_sha256(image_receipts),
            "input_images": image_receipts,
            "resolved_scene_sha256": invocation.resolved_scene_sha256,
            "seed": invocation.seed,
            "wall_time_ms": wall_time_ms,
            "peak_vram_mib": outcome.resource.peak_vram_mib,
            "input_tokens": outcome.resource.input_tokens,
            "output_tokens": outcome.resource.output_tokens,
            "raw_response_sha256": _sha256_bytes(raw_bytes),
            "parsed_response_sha256": parsed_sha,
            "provider_id": provider_identity.provider_id,
            "provider_revision": provider_identity.revision,
            "provider_implementation_sha256": provider_identity.implementation_sha256,
            "provider_kind": provider_identity.kind,
        }
        event_body = {
            "schema_version": self.spec["logging_contract"]["schema_version"],
            "event_id": f"call-{invocation_id}",
            "timestamp_utc": _utc_now(),
            "study_id": self.identity.study_id,
            "experiment_id": "A_visible_semantic_correction",
            "phase": f"execution.{sample['split']}",
            "case_id": invocation.case_id,
            "arm": invocation.arm,
            "attempt": invocation.attempt,
            "decision": "provider_call_completed",
            "input_bindings": {
                "logical_key": logical_key,
                "request_sha256": request_sha,
                "spec_sha256": self.identity.spec_sha256,
                "source_manifest_sha256": self.identity.source_manifest_sha256,
                "sample_bundle_sha256": sample["bundle_sha256"],
                "artifact_manifest": artifacts,
                "repair_of": invocation.repair_of,
                "repair_index": invocation.repair_index,
                "repair_source_prompt_sha256": repair_source_prompt_sha,
                "repair_source_response_sha256": repair_source_response_sha,
                "prompt_size_bytes": len(invocation.prompt.encode("utf-8")),
                "selected_prompt_event_id": (
                    selected_prompt_event["event_id"] if sample["split"] == "test" else None
                ),
                "reservation_event_id": reservation["event_id"],
            },
            "model_receipt": model_receipt,
            "resource_receipt": {"wall_time_ms": wall_time_ms, **resource},
            "outputs": {
                "decision": outcome.decision,
                "result": dict(outcome.result),
                "abstain": outcome.abstained,
                "repair": {
                    "is_format_only_repair": invocation.repair_index == 1,
                    "repair_of": invocation.repair_of,
                },
            },
            "gate_results": {
                "provider_production_eligible": provider_identity.production_eligible,
                "physical_gate_authority": self.spec["physical_gate_authority"],
                "render_used_as_physics_evidence": False,
                "provider_claimed_physical_pass": outcome.claims_physical_pass,
                "format_repair_eligible": False,
                "resource_budget": "fail" if violations else "pass",
                "stop_rule_violations": violations,
                "study_stopped": bool(violations),
                "stop_reason": "; ".join(violations) if violations else None,
                "receipt_hash_bound": True,
            },
            "error": None,
            "expensive_execution_started": bool(
                outcome.resource.gpu_time_ms or provider_identity.kind == "local_qwen"
            ),
        }
        event = self._journal.append(event_body)
        self._emit("receipt.fsynced", durable_event_id=event["event_id"], **progress_fields)
        if violations:
            self._raise_after_atomic_violation_receipt(
                event=event,
                reason="; ".join(violations),
                progress_fields=progress_fields,
            )
        self._emit("invocation.completed", durable_event_id=event["event_id"], **progress_fields)
        return RunReceipt(invocation_id=invocation_id, event=event, resumed=False)

    @staticmethod
    def _contains_gold_key(value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(
                str(key).lower().startswith("gold") or ExperimentRunner._contains_gold_key(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(ExperimentRunner._contains_gold_key(item) for item in value)
        return False

    @staticmethod
    def _contains_blinding_material(
        value: Any,
        *,
        route_vocabulary: Sequence[str] = (),
    ) -> bool:
        """Reject fields that could expose a gold lookup handle to B0--B2.

        B2 may see typed operational state, but it may not receive an opaque
        annotation, answer, oracle, or label identifier that can be joined to
        the sealed test set outside this process.
        """

        forbidden = {
            "gold",
            "annotation",
            "answer",
            "oracle",
            "target_route",
            "expected_route",
            "label",
            "manifest",
            "test_key",
        }
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
                if any(part in forbidden for part in normalized.split("_")):
                    return True
                if ExperimentRunner._contains_blinding_material(
                    item,
                    route_vocabulary=route_vocabulary,
                ):
                    return True
            return False
        if isinstance(value, (list, tuple)):
            return any(
                ExperimentRunner._contains_blinding_material(
                    item,
                    route_vocabulary=route_vocabulary,
                )
                for item in value
            )
        if isinstance(value, str):
            normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
            padded = f"_{normalized}_"
            marker_phrases = (
                "_gold_route_",
                "_oracle_",
                "_oracle_answer_",
                "_expected_route_",
                "_target_route_",
                "_answer_key_",
                "_annotation_manifest_",
                "_adjudicated_label_",
            )
            return any(marker in padded for marker in marker_phrases) or any(
                f"_{route.casefold()}_" in padded for route in route_vocabulary
            )
        return False

    def _execute_routing(self, invocation: RoutingInvocation) -> RunReceipt:
        if invocation.attempt < 1:
            raise ValueError("attempt must be at least one")
        if not invocation.original_prompt or not invocation.routing_instruction:
            raise ValueError("routing prompts must be non-empty")
        if not invocation.prompt_template_version:
            raise ValueError("prompt_template_version must be non-empty")
        if invocation.untyped_failure_summary is not None and not isinstance(
            invocation.untyped_failure_summary, str
        ):
            raise ValueError("untyped_failure_summary must be text or null")
        if not isinstance(invocation.resource_reservation, ResourceUsage) or any(
            type(value) is not int or value < 0
            for value in asdict(invocation.resource_reservation).values()
        ):
            raise ValueError("resource_reservation must contain non-negative integer counters")
        mapping_inputs = (
            invocation.typed_failure,
            invocation.trusted_state,
            invocation.asset_availability,
        )
        if not all(isinstance(value, Mapping) for value in mapping_inputs) or (
            invocation.visible_report is not None
            and not isinstance(invocation.visible_report, Mapping)
        ):
            raise ValueError("routing state inputs must be mappings or null")
        try:
            self._refresh_execution_snapshot()
        except RunnerIntegrityError as error:
            self._record_stop(
                reason=f"frozen input drift: {error}",
                experiment_id="B_typed_failure_prompt_fallback",
                case_id=invocation.case_id,
                arm=invocation.arm,
                attempt=invocation.attempt,
                triggering_event_id=None,
            )
        experiment = self.spec["experiments"][1]
        arm = next((item for item in experiment["arms"] if item["arm"] == invocation.arm), None)
        if arm is None:
            raise ValueError(f"unknown routing arm: {invocation.arm}")
        sample = next(
            (item for item in experiment["samples"] if item["case_id"] == invocation.case_id),
            None,
        )
        if sample is None:
            raise ValueError(f"unknown routing case: {invocation.case_id}")
        if invocation.original_prompt != sample["task_context"]:
            raise ValueError("original_prompt must exactly match the frozen task_context")
        max_attempts = int(experiment["budget"]["max_attempts_per_case_arm"])
        if invocation.attempt > max_attempts:
            raise ValueError(f"routing attempt exceeds frozen maximum {max_attempts}")
        failure_code = invocation.typed_failure.get("code")
        if invocation.arm == "B0_retry_unchanged" and (
            not isinstance(failure_code, str) or not failure_code
        ):
            raise ValueError("B0 requires one non-empty failure code")
        if invocation.arm == "B1_generic_prompt_repair" and not (
            isinstance(invocation.untyped_failure_summary, str)
            and invocation.untyped_failure_summary
        ):
            raise ValueError("B1 requires one non-empty untyped failure summary")
        expose_gold = invocation.arm == "B3_oracle_route_ceiling"
        if not expose_gold and any(
            self._contains_gold_key(value)
            or self._contains_blinding_material(
                value,
                route_vocabulary=tuple(experiment["route_vocabulary"]),
            )
            for value in (
                invocation.routing_instruction,
                invocation.untyped_failure_summary,
                invocation.typed_failure,
                invocation.trusted_state,
                invocation.asset_availability,
                invocation.visible_report,
            )
        ):
            raise ValueError(
                "non-oracle routing inputs cannot contain gold fields or blinded gold material"
            )
        artifacts = protocol.build_bundle_manifest(self.config.repo_root, sample["artifacts"])
        observed_bundle = protocol.canonical_sha256(artifacts)
        if observed_bundle != sample["bundle_sha256"]:
            raise RunnerIntegrityError(
                f"artifact bundle drift for {invocation.case_id}: "
                f"expected {sample['bundle_sha256']}, "
                f"observed {observed_bundle}"
            )
        typed_failure = _canonical_mapping_copy(invocation.typed_failure)
        trusted_state = _canonical_mapping_copy(invocation.trusted_state)
        asset_availability = _canonical_mapping_copy(invocation.asset_availability)
        visible_report = (
            _canonical_mapping_copy(invocation.visible_report)
            if invocation.visible_report is not None
            else None
        )
        typed_failure_sha = protocol.canonical_sha256(typed_failure)
        trusted_state_sha = protocol.canonical_sha256(trusted_state)
        asset_availability_sha = protocol.canonical_sha256(asset_availability)
        visible_report_sha = (
            protocol.canonical_sha256(visible_report) if visible_report is not None else None
        )
        untyped_failure_summary_sha = (
            _sha256_bytes(invocation.untyped_failure_summary.encode("utf-8"))
            if invocation.untyped_failure_summary is not None
            else None
        )
        exposes_typed_context = invocation.arm in {
            "B2_typed_route_and_prompt_repair",
            "B3_oracle_route_ceiling",
        }
        provider_context = {
            "failure_code": failure_code if invocation.arm == "B0_retry_unchanged" else None,
            "untyped_failure_summary": (
                invocation.untyped_failure_summary
                if invocation.arm == "B1_generic_prompt_repair"
                else None
            ),
            "typed_failure": typed_failure if exposes_typed_context else None,
            "trusted_state": trusted_state if exposes_typed_context else {},
            "asset_availability": asset_availability if exposes_typed_context else {},
            "visible_report": visible_report if exposes_typed_context else None,
            "gold_route": sample["gold_route"] if expose_gold else None,
        }
        provider_context_sha = protocol.canonical_sha256(provider_context)
        logical_value = {
            "kind": "routing",
            "experiment_id": experiment["experiment_id"],
            "case_id": invocation.case_id,
            "arm": invocation.arm,
            "attempt": invocation.attempt,
        }
        logical_key = protocol.canonical_sha256(logical_value)
        request_value = {
            **logical_value,
            "original_prompt": invocation.original_prompt,
            "routing_instruction": invocation.routing_instruction,
            "prompt_template_version": invocation.prompt_template_version,
            "typed_failure": typed_failure,
            "trusted_state": trusted_state,
            "asset_availability": asset_availability,
            "visible_report": visible_report,
            "untyped_failure_summary": invocation.untyped_failure_summary,
            "seed": invocation.seed,
            "resource_reservation": asdict(invocation.resource_reservation),
            "bundle_sha256": sample["bundle_sha256"],
            "source_manifest_sha256": self.identity.source_manifest_sha256,
            "spec_sha256": self.identity.spec_sha256,
        }
        request_sha = protocol.canonical_sha256(request_value)
        invocation_id = request_sha
        for event in self._journal.events:
            bindings = event.get("input_bindings", {})
            if bindings.get("logical_key") != logical_key:
                continue
            if bindings.get("request_sha256") != request_sha:
                raise DuplicateInvocationError(
                    "logical invocation key already exists with a different request digest"
                )
            self._emit(
                "invocation.resumed",
                experiment_id=experiment["experiment_id"],
                case_id=invocation.case_id,
                arm=invocation.arm,
                invocation_id=invocation_id,
                durable_event_id=event["event_id"],
            )
            return RunReceipt(invocation_id=invocation_id, event=event, resumed=True)
        reservation_violations = self._routing_reservation_violations(
            reservation=invocation.resource_reservation,
            case_id=invocation.case_id,
            arm=invocation.arm,
            budget=experiment["budget"],
        )
        if reservation_violations:
            self._record_stop(
                reason="; ".join(reservation_violations),
                experiment_id=experiment["experiment_id"],
                case_id=invocation.case_id,
                arm=invocation.arm,
                attempt=invocation.attempt,
                triggering_event_id=None,
            )
        provider = self._routing_provider_for(invocation.arm)
        provider_identity = self._snapshot_provider_identity(provider)
        provider_sandbox_profile_sha256 = None
        if type(provider) is SandboxedRoutingProvider:
            _, provider_sandbox_profile_sha256 = provider.isolation_profile(self.config.repo_root)
        if not provider_identity.production_eligible and not self.config.allow_test_providers:
            raise RunnerIntegrityError(
                "non-production provider is forbidden by runner configuration"
            )
        provider_invocation_id = f"blind-call-{secrets.token_hex(16)}"
        provider_case_id = f"blind-case-{secrets.token_hex(16)}"
        request = RoutingProviderRequest(
            invocation_id=provider_invocation_id,
            case_id=provider_case_id,
            arm=invocation.arm,
            original_prompt=invocation.original_prompt,
            routing_instruction=invocation.routing_instruction,
            prompt_template_version=invocation.prompt_template_version,
            failure_code=provider_context["failure_code"],
            untyped_failure_summary=provider_context["untyped_failure_summary"],
            typed_failure=(
                _canonical_mapping_copy(typed_failure) if exposes_typed_context else None
            ),
            trusted_state=(_canonical_mapping_copy(trusted_state) if exposes_typed_context else {}),
            asset_availability=(
                _canonical_mapping_copy(asset_availability) if exposes_typed_context else {}
            ),
            visible_report=(
                _canonical_mapping_copy(visible_report)
                if exposes_typed_context and visible_report is not None
                else None
            ),
            seed=invocation.seed,
            attempt=invocation.attempt,
            allowed_routes=tuple(experiment["route_vocabulary"]),
            gold_route=sample["gold_route"] if expose_gold else None,
            resource_reservation=invocation.resource_reservation,
        )
        progress_fields = {
            "experiment_id": experiment["experiment_id"],
            "case_id": invocation.case_id,
            "arm": invocation.arm,
            "invocation_id": invocation_id,
        }
        prompt_sha = _sha256_bytes(invocation.routing_instruction.encode("utf-8"))
        context_config = {
            "arm_context": arm["context"],
            "allowed_routes": experiment["route_vocabulary"],
            "provider_context_sha256": provider_context_sha,
        }
        reservation = self._reserve_provider_call(
            experiment_id=experiment["experiment_id"],
            phase=f"execution.{sample['split']}",
            case_id=invocation.case_id,
            arm=invocation.arm,
            attempt=invocation.attempt,
            logical_key=logical_key,
            request_sha=request_sha,
            input_bindings={
                "spec_sha256": self.identity.spec_sha256,
                "source_manifest_sha256": self.identity.source_manifest_sha256,
                "sample_bundle_sha256": sample["bundle_sha256"],
                "artifact_manifest": artifacts,
                "original_prompt_sha256": _sha256_bytes(invocation.original_prompt.encode("utf-8")),
                "typed_failure_sha256": typed_failure_sha,
                "trusted_state_sha256": trusted_state_sha,
                "asset_availability_sha256": asset_availability_sha,
                "visible_report_sha256": visible_report_sha,
                "untyped_failure_summary_sha256": untyped_failure_summary_sha,
                "provider_context_sha256": provider_context_sha,
                "provider_invocation_id": provider_invocation_id,
                "provider_case_id": provider_case_id,
                "resource_reservation": asdict(invocation.resource_reservation),
            },
            model_receipt={
                "prompt_sha256": prompt_sha,
                "prompt_template_version": invocation.prompt_template_version,
                "model_id": provider_identity.provider_id,
                "model_revision": provider_identity.revision,
                "snapshot_manifest_sha256": None,
                "processor_config_sha256": protocol.canonical_sha256(context_config),
                "input_image_sha256": None,
                "resolved_scene_sha256": None,
                "seed": invocation.seed,
                "wall_time_ms": 0,
                "peak_vram_mib": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "raw_response_sha256": None,
                "parsed_response_sha256": None,
                "provider_id": provider_identity.provider_id,
                "provider_revision": provider_identity.revision,
                "provider_implementation_sha256": provider_identity.implementation_sha256,
                "provider_kind": provider_identity.kind,
                "provider_sandbox_profile_sha256": provider_sandbox_profile_sha256,
            },
            repair={"prompt_rewritten": False, "intent_preserved": None},
            provider_production_eligible=provider_identity.production_eligible,
        )
        self._emit("provider.started", **progress_fields)
        start_ns = time.monotonic_ns()
        outcome: Any = None
        try:
            outcome = provider.invoke(
                request,
                lambda stage: self._emit(f"provider.{stage}", **progress_fields),
            )
            self._verify_provider_identity_unchanged(provider, provider_identity)
            if type(provider) is SandboxedRoutingProvider:
                _, observed_sandbox_profile_sha256 = provider.isolation_profile(
                    self.config.repo_root
                )
                if observed_sandbox_profile_sha256 != provider_sandbox_profile_sha256:
                    raise ProviderContractError(
                        "provider sandbox profile changed during invocation"
                    )
            outcome = self._validate_provider_outcome(outcome)
            resource = asdict(outcome.resource)
            result = dict(outcome.result)
            route = result.get("route")
            if route not in experiment["route_vocabulary"]:
                raise ProviderContractError(f"routing provider returned an unknown route: {route}")
            revised_prompt = result.get("revised_prompt")
            if revised_prompt is not None and not isinstance(revised_prompt, str):
                raise ProviderContractError("revised_prompt must be a string or null")
            if invocation.arm == "B0_retry_unchanged" and revised_prompt not in {
                None,
                invocation.original_prompt,
            }:
                raise ProviderContractError("B0 retry must keep the original prompt byte-identical")
            if (
                revised_prompt is not None
                and revised_prompt != invocation.original_prompt
                and route
                not in {
                    "repair_visible_prompt",
                    "canonicalize_language_preserving_intent",
                }
            ):
                raise ProviderContractError("selected route is not allowed to rewrite the prompt")
            intent_verification = self._verify_routing_intent(
                outcome=outcome,
                original_prompt=invocation.original_prompt,
                revised_prompt=revised_prompt,
            )
            intent_preserved = intent_verification.preserved
        except Exception as caught_error:
            provider_error = caught_error
            try:
                self._verify_provider_identity_unchanged(provider, provider_identity)
            except ProviderContractError as identity_error:
                provider_error = identity_error
            if type(provider) is SandboxedRoutingProvider:
                try:
                    _, observed_sandbox_profile_sha256 = provider.isolation_profile(
                        self.config.repo_root
                    )
                    if observed_sandbox_profile_sha256 != provider_sandbox_profile_sha256:
                        raise ProviderContractError(
                            "provider sandbox profile changed during invocation"
                        )
                except (RunnerIntegrityError, ProviderContractError) as sandbox_error:
                    provider_error = ProviderContractError(
                        "provider sandbox profile changed during invocation"
                    )
                    provider_error.__cause__ = sandbox_error
            wall_time_ms = max(0, (time.monotonic_ns() - start_ns) // 1_000_000)
            reported_usage_is_valid = (
                isinstance(outcome, ProviderOutcome)
                and isinstance(outcome.resource, ResourceUsage)
                and all(
                    type(value) is int and value >= 0 for value in asdict(outcome.resource).values()
                )
            )
            reported_usage = (
                outcome.resource if reported_usage_is_valid else invocation.resource_reservation
            )
            partial_resource = asdict(reported_usage)
            partial_raw_sha: str | None = None
            if isinstance(outcome, ProviderOutcome) and isinstance(
                outcome.raw_response, (str, bytes)
            ):
                partial_raw = (
                    outcome.raw_response.encode("utf-8")
                    if isinstance(outcome.raw_response, str)
                    else outcome.raw_response
                )
                partial_raw_sha = _sha256_bytes(partial_raw)
            partial_parsed_sha: str | None = None
            if isinstance(outcome, ProviderOutcome) and isinstance(
                outcome.parsed_response, Mapping
            ):
                try:
                    partial_parsed_sha = protocol.canonical_sha256(outcome.parsed_response)
                except (TypeError, ValueError):
                    partial_parsed_sha = None
            partial_result: dict[str, Any] | None = None
            if isinstance(outcome, ProviderOutcome) and isinstance(outcome.result, Mapping):
                try:
                    protocol.canonical_json_bytes(outcome.result)
                except (TypeError, ValueError):
                    partial_result = None
                else:
                    partial_result = dict(outcome.result)
            error_code = (
                "provider_contract_error"
                if isinstance(provider_error, ProviderContractError)
                else "provider_exception"
            )
            provider_claimed_physical_pass = (
                (outcome.claims_physical_pass or self._contains_physical_claim(outcome.result))
                if isinstance(outcome, ProviderOutcome)
                and type(outcome.claims_physical_pass) is bool
                else False
            )
            failure_outcome = ProviderOutcome(
                decision=error_code,
                result={},
                raw_response=b"",
                parsed_response=None,
                abstained=True,
                resource=reported_usage,
                claims_physical_pass=provider_claimed_physical_pass,
            )
            failure_revised_prompt = (
                partial_result.get("revised_prompt")
                if partial_result is not None
                and isinstance(partial_result.get("revised_prompt"), str)
                else None
            )
            violations = self._routing_outcome_violations(
                failure_outcome,
                case_id=invocation.case_id,
                arm=invocation.arm,
                revised_prompt=failure_revised_prompt,
                original_prompt=invocation.original_prompt,
                budget=experiment["budget"],
            )
            violations.extend(
                self._unreserved_routing_usage(reported_usage, invocation.resource_reservation)
            )
            if not reported_usage_is_valid:
                violations.append("provider resource usage is unknown after invocation")
            event_body = {
                "schema_version": self.spec["logging_contract"]["schema_version"],
                "event_id": f"call-{invocation_id}",
                "timestamp_utc": _utc_now(),
                "study_id": self.identity.study_id,
                "experiment_id": experiment["experiment_id"],
                "phase": f"execution.{sample['split']}",
                "case_id": invocation.case_id,
                "arm": invocation.arm,
                "attempt": invocation.attempt,
                "decision": "provider_call_failed",
                "input_bindings": {
                    "logical_key": logical_key,
                    "request_sha256": request_sha,
                    "spec_sha256": self.identity.spec_sha256,
                    "source_manifest_sha256": self.identity.source_manifest_sha256,
                    "sample_bundle_sha256": sample["bundle_sha256"],
                    "artifact_manifest": artifacts,
                    "original_prompt_sha256": _sha256_bytes(
                        invocation.original_prompt.encode("utf-8")
                    ),
                    "typed_failure_sha256": typed_failure_sha,
                    "trusted_state_sha256": trusted_state_sha,
                    "asset_availability_sha256": asset_availability_sha,
                    "visible_report_sha256": visible_report_sha,
                    "untyped_failure_summary_sha256": untyped_failure_summary_sha,
                    "provider_context_sha256": provider_context_sha,
                    "reservation_event_id": reservation["event_id"],
                },
                "model_receipt": {
                    "prompt_sha256": prompt_sha,
                    "prompt_template_version": invocation.prompt_template_version,
                    "model_id": provider_identity.provider_id,
                    "model_revision": provider_identity.revision,
                    "snapshot_manifest_sha256": None,
                    "processor_config_sha256": protocol.canonical_sha256(context_config),
                    "input_image_sha256": None,
                    "resolved_scene_sha256": None,
                    "seed": invocation.seed,
                    "wall_time_ms": wall_time_ms,
                    "peak_vram_mib": partial_resource.get("peak_vram_mib", 0),
                    "input_tokens": partial_resource.get("input_tokens", 0),
                    "output_tokens": partial_resource.get("output_tokens", 0),
                    "raw_response_sha256": partial_raw_sha,
                    "parsed_response_sha256": partial_parsed_sha,
                    "provider_id": provider_identity.provider_id,
                    "provider_revision": provider_identity.revision,
                    "provider_implementation_sha256": provider_identity.implementation_sha256,
                    "provider_kind": provider_identity.kind,
                    "provider_sandbox_profile_sha256": provider_sandbox_profile_sha256,
                },
                "resource_receipt": {
                    "wall_time_ms": wall_time_ms,
                    **partial_resource,
                    "usage_known": reported_usage_is_valid,
                },
                "outputs": {
                    "decision": error_code,
                    "result": partial_result,
                    "revised_prompt_sha256": None,
                    "abstain": True,
                    "repair": {"prompt_rewritten": False, "intent_preserved": False},
                },
                "gate_results": {
                    "provider_production_eligible": provider_identity.production_eligible,
                    "physical_gate_authority": self.spec["physical_gate_authority"],
                    "render_used_as_physics_evidence": False,
                    "provider_claimed_physical_pass": provider_claimed_physical_pass,
                    "gold_route_exposed": expose_gold,
                    "intent_preserved": False,
                    "unsafe_publication": False,
                    "resource_budget": "fail" if violations else "pass",
                    "stop_rule_violations": violations,
                    "study_stopped": bool(violations),
                    "stop_reason": "; ".join(violations) if violations else None,
                    "receipt_hash_bound": True,
                },
                "error": {
                    "code": error_code,
                    "exception_type": type(provider_error).__name__,
                    "message_sha256": _sha256_bytes(str(provider_error).encode("utf-8")),
                },
                "expensive_execution_started": True,
            }
            event = self._journal.append(event_body)
            self._emit("receipt.fsynced", durable_event_id=event["event_id"], **progress_fields)
            if violations:
                self._raise_after_atomic_violation_receipt(
                    event=event,
                    reason="; ".join(violations),
                    progress_fields=progress_fields,
                )
            self._emit("invocation.failed", durable_event_id=event["event_id"], **progress_fields)
            return RunReceipt(invocation_id=invocation_id, event=event, resumed=False)
        wall_time_ms = max(0, (time.monotonic_ns() - start_ns) // 1_000_000)
        violations = self._routing_outcome_violations(
            outcome,
            case_id=invocation.case_id,
            arm=invocation.arm,
            revised_prompt=revised_prompt,
            original_prompt=invocation.original_prompt,
            budget=experiment["budget"],
        )
        violations.extend(
            self._unreserved_routing_usage(outcome.resource, invocation.resource_reservation)
        )
        raw_bytes = (
            outcome.raw_response.encode("utf-8")
            if isinstance(outcome.raw_response, str)
            else outcome.raw_response
        )
        parsed_sha = (
            protocol.canonical_sha256(outcome.parsed_response)
            if outcome.parsed_response is not None
            else None
        )
        model_receipt = {
            "prompt_sha256": prompt_sha,
            "prompt_template_version": invocation.prompt_template_version,
            "model_id": provider_identity.provider_id,
            "model_revision": provider_identity.revision,
            "snapshot_manifest_sha256": None,
            "processor_config_sha256": protocol.canonical_sha256(context_config),
            "input_image_sha256": None,
            "resolved_scene_sha256": None,
            "seed": invocation.seed,
            "wall_time_ms": wall_time_ms,
            "peak_vram_mib": outcome.resource.peak_vram_mib,
            "input_tokens": outcome.resource.input_tokens,
            "output_tokens": outcome.resource.output_tokens,
            "raw_response_sha256": _sha256_bytes(raw_bytes),
            "parsed_response_sha256": parsed_sha,
            "provider_id": provider_identity.provider_id,
            "provider_revision": provider_identity.revision,
            "provider_implementation_sha256": provider_identity.implementation_sha256,
            "provider_kind": provider_identity.kind,
            "provider_sandbox_profile_sha256": provider_sandbox_profile_sha256,
        }
        event_body = {
            "schema_version": self.spec["logging_contract"]["schema_version"],
            "event_id": f"call-{invocation_id}",
            "timestamp_utc": _utc_now(),
            "study_id": self.identity.study_id,
            "experiment_id": experiment["experiment_id"],
            "phase": f"execution.{sample['split']}",
            "case_id": invocation.case_id,
            "arm": invocation.arm,
            "attempt": invocation.attempt,
            "decision": "provider_call_completed",
            "input_bindings": {
                "logical_key": logical_key,
                "request_sha256": request_sha,
                "spec_sha256": self.identity.spec_sha256,
                "source_manifest_sha256": self.identity.source_manifest_sha256,
                "sample_bundle_sha256": sample["bundle_sha256"],
                "artifact_manifest": artifacts,
                "original_prompt_sha256": _sha256_bytes(invocation.original_prompt.encode("utf-8")),
                "typed_failure_sha256": typed_failure_sha,
                "trusted_state_sha256": trusted_state_sha,
                "asset_availability_sha256": asset_availability_sha,
                "visible_report_sha256": visible_report_sha,
                "untyped_failure_summary_sha256": untyped_failure_summary_sha,
                "provider_context_sha256": provider_context_sha,
                "reservation_event_id": reservation["event_id"],
            },
            "model_receipt": model_receipt,
            "resource_receipt": {"wall_time_ms": wall_time_ms, **resource},
            "outputs": {
                "decision": outcome.decision,
                "result": result,
                "revised_prompt_sha256": (
                    _sha256_bytes(revised_prompt.encode("utf-8"))
                    if revised_prompt is not None
                    else None
                ),
                "abstain": outcome.abstained,
                "repair": {
                    "prompt_rewritten": revised_prompt is not None
                    and revised_prompt != invocation.original_prompt,
                    "intent_preserved": intent_preserved,
                },
            },
            "gate_results": {
                "provider_production_eligible": provider_identity.production_eligible,
                "physical_gate_authority": self.spec["physical_gate_authority"],
                "render_used_as_physics_evidence": False,
                "provider_claimed_physical_pass": outcome.claims_physical_pass,
                "gold_route_exposed": expose_gold,
                "intent_preserved": intent_preserved,
                "unsafe_publication": not intent_preserved,
                "intent_verifier_id": intent_verification.verifier_id,
                "intent_verifier_implementation_sha256": (
                    intent_verification.verifier_implementation_sha256
                ),
                "resource_budget": "fail" if violations else "pass",
                "stop_rule_violations": violations,
                "study_stopped": bool(violations),
                "stop_reason": "; ".join(violations) if violations else None,
                "receipt_hash_bound": True,
            },
            "error": None,
            "expensive_execution_started": bool(
                outcome.resource.gpu_time_ms
                or provider_identity.kind in {"local_qwen", "local_llm"}
            ),
        }
        event = self._journal.append(event_body)
        self._emit("receipt.fsynced", durable_event_id=event["event_id"], **progress_fields)
        if violations:
            self._raise_after_atomic_violation_receipt(
                event=event,
                reason="; ".join(violations),
                progress_fields=progress_fields,
            )
        self._emit("invocation.completed", durable_event_id=event["event_id"], **progress_fields)
        return RunReceipt(invocation_id=invocation_id, event=event, resumed=False)


def summarize_visible(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Analyze visible outcomes with the preregistered implementation."""

    return protocol.visible_metrics(rows)


def summarize_routing(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Analyze routing outcomes with the preregistered implementation."""

    return protocol.routing_metrics(rows)


def summarize_paired_delta(
    rows: Sequence[dict[str, Any]],
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Run the frozen group-cluster bootstrap without altering aggregation."""

    return protocol.paired_cluster_bootstrap_delta(rows, resamples=resamples, seed=seed)


def summarize_route_comparison(
    baseline_correct: Sequence[bool],
    candidate_correct: Sequence[bool],
) -> dict[str, Any]:
    """Run the frozen exact paired route comparison."""

    return protocol.exact_mcnemar(baseline_correct, candidate_correct)


__all__ = [
    "ArtifactBinding",
    "DuplicateInvocationError",
    "ExperimentRunner",
    "IntentPreservationVerifier",
    "IntentVerification",
    "ModelBinding",
    "OracleRoutingCapability",
    "ProgressCallback",
    "ProgressUpdate",
    "ProviderIdentity",
    "ProviderContractError",
    "ProviderOutcome",
    "ResourceUsage",
    "RoutingInvocation",
    "RoutingProviderFactory",
    "RoutingProviderRequest",
    "RunReceipt",
    "RunnerConfig",
    "RunnerIdentity",
    "RunnerIntegrityError",
    "SandboxedRoutingProvider",
    "StudyStoppedError",
    "TypedRoutingProvider",
    "VisibleCriticProvider",
    "VisibleInvocation",
    "VisiblePromptFreeze",
    "VisibleProviderRequest",
    "summarize_paired_delta",
    "summarize_route_comparison",
    "summarize_routing",
    "summarize_visible",
]

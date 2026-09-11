"""Admit exact native terminals after observing their real public deep readers.

The local profiler observes successful CAS resolution returns without replacing any
reader. A durable read index records actual accesses, not a generic JSON graph or an
enumeration of the CAS. Reload proves these exact bytes and the trusted child binding;
it cannot independently turn a caller-created document into live execution authority.
"""

import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .artifacts import LocalArtifactStore, ResolvedArtifact
from .genesis_fresh_capture_application import GenesisFreshCaptureApplication
from .genesis_scene_application_v2 import GenesisSceneApplication
from .genesis_scene_validation_application import GenesisSceneValidationApplication
from .image2env_genesis_compile_application import Image2EnvGenesisCompileApplication
from .run_store import SQLiteRunStore
from .schemas.common import RunStatus
from .schemas.genesis_fresh_capture import GenesisFreshCaptureInputV1, GenesisFreshCaptureReceiptV1
from .schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from .schemas.genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
)
from .schemas.genesis_scene_replay import GenesisSceneReplayV1, GenesisSceneTraceRowV1
from .schemas.image2env_genesis_compile import Image2EnvGenesisCompileRequestV1
from .schemas.native_terminal_admission import (
    NATIVE_READ_CLOSURE_ID,
    NATIVE_TERMINAL_ADMISSION_ID,
    NativeDigestMemberV1,
    NativeReadClosureV1,
    NativeTerminalAdmissionV1,
)
from .typed_qualification_admission import (
    VerifiedTypedQualificationAdmission,
    verify_typed_qualification_admission,
)

_APPS = {
    Image2EnvGenesisCompileApplication: (
        "image2env.compile@2.0.0",
        "harness.image2env_genesis_compile_receipt.v1",
    ),
    GenesisSceneApplication: (
        "image2env.replay@2.0.0",
        "harness.genesis_scene_execution_receipt.v2",
    ),
    GenesisFreshCaptureApplication: (
        "genesis.observe@3.0.0",
        "harness.genesis_fresh_capture_receipt.v1",
    ),
    GenesisSceneValidationApplication: (
        "image2env.validate@2.0.0",
        "harness.genesis_scene_validation.v1",
    ),
}
_TOKEN = object()


def _canonical(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _put(store, value, name, schema, media="application/json"):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_bytes(value if isinstance(value, bytes) else _canonical(value))
        return store.put_file(path, name=name, media_type=media, schema_version=schema)


def _read(store, ref, model):
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError("admission requires exact CAS identities")
    raw = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(raw)
    if (
        ref.schema_version != value.schema_version
        or ref.media_type != "application/json"
        or raw != _canonical(value)
    ):
        raise ValueError("admission requires exact canonical typed JSON")
    return value


@dataclass(frozen=True)
class ObservedNativeReads:
    closure: NativeReadClosureV1
    live_authority: bool = field(default=False, init=False)


def observe_native_reads(*, artifact_store, read):
    """Observe one synchronous current-thread read; this alone grants no admission."""
    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("read observation requires exact LocalArtifactStore")
    artifacts = {}
    digests = {}
    previous = sys.getprofile()
    resolve_code = LocalArtifactStore.resolve.__code__
    digest_code = LocalArtifactStore.resolve_digest.__code__

    def profile(frame, event, result):
        if previous is not None:
            previous(frame, event, result)
        if event == "return" and frame.f_locals.get("self") is artifact_store:
            if frame.f_code is resolve_code:
                if type(result) is not ResolvedArtifact:
                    raise ValueError("CAS resolve did not return a verified artifact")
                ref = result.ref
                if ref.uri != f"artifact://sha256/{ref.sha256}":
                    raise ValueError("deep read used a non-CAS artifact")
                artifacts[_canonical(ref)] = ref
            elif frame.f_code is digest_code:
                if not isinstance(result, Path):
                    raise ValueError("CAS digest resolve did not return a verified path")
                sha = frame.f_locals["sha256"]
                digests[sha] = NativeDigestMemberV1(sha256=sha, bytes=result.stat().st_size)

    try:
        sys.setprofile(profile)
        if sys.getprofile() is not profile:
            raise ValueError("read observation profile was not installed")
        read()
        if sys.getprofile() is not profile:
            raise ValueError("deep reader changed the observation profile")
    finally:
        sys.setprofile(previous)
        if sys.getprofile() is not previous:
            raise ValueError("read observation profile was not restored")
    return ObservedNativeReads(
        NativeReadClosureV1(
            schema_version=NATIVE_READ_CLOSURE_ID,
            artifacts=tuple(artifacts[key] for key in sorted(artifacts)),
            digest_members=tuple(digests[key] for key in sorted(digests)),
            scope="actual_public_deep_read_accesses",
        )
    )


class VerifiedLiveNativeTerminal:
    """Unforgeable-by-document capability: issued after the exact public reader succeeds."""

    __slots__ = ("admission", "admission_ref")

    def __init__(self, admission, admission_ref, *, token):
        if token is not _TOKEN:
            raise TypeError("live admission requires an exact completed public deep read")
        object.__setattr__(self, "admission", admission)
        object.__setattr__(self, "admission_ref", admission_ref)

    def __setattr__(self, name, value):
        raise AttributeError("live terminal admission is immutable")


class VerifiedNativeTerminalReload(VerifiedLiveNativeTerminal):
    """Sealed byte/SQLite reload result; never a newly observed live deep read."""

    __slots__ = ()

    @property
    def live_authority(self):
        return False


def _predecessor(value, skill, run_id):
    if type(value) not in (VerifiedLiveNativeTerminal, VerifiedNativeTerminalReload):
        raise TypeError("fresh admission requires a sealed replay predecessor")
    if value.admission.skill_ref != skill or value.admission.run_id != run_id:
        raise ValueError("fresh admission predecessor belongs to another replay")
    return value.admission_ref


def _read_fresh(application, store, receipt_ref, state, invocation, predecessor):
    receipt = _read(store, receipt_ref, GenesisFreshCaptureReceiptV1)
    parameters = GenesisFreshCaptureInputV1.model_validate(invocation.effective_parameters)
    prior_ref = _predecessor(predecessor, "image2env.replay@2.0.0", parameters.replay_run_id)
    prior = _read(store, prior_ref, NativeTerminalAdmissionV1)
    if prior != predecessor.admission:
        raise ValueError("replay predecessor admission bytes changed")
    replay_receipt = _read(store, prior.native_receipt, GenesisSceneExecutionReceiptV2)
    scene_request = _read(store, replay_receipt.request, GenesisSceneExecutionRequestV2)
    replay = _read(store, replay_receipt.replay, GenesisSceneReplayV1)
    row = GenesisSceneTraceRowV1.model_validate_json(
        store.resolve(replay.half_dt.rows).path.read_bytes().splitlines()[-1]
    )
    request, result = application.producer.verify_result(receipt.request, receipt.capture_result)
    if (
        receipt.model_dump(mode="json") != state.output
        or receipt.replay_run_id != parameters.replay_run_id
        or receipt.ttl_seconds != parameters.ttl_seconds
        or receipt.replay_receipt != prior.native_receipt
        or request.replay_receipt != prior.native_receipt
        or request.replay_run_id != prior.run_id
        or request.scene != scene_request.scene
        or request.half_dt_rows != replay.half_dt.rows
        or request.final_state != row.objects["object"]
        or row.step != 2000
        or row.time_s != 4.0
        or receipt.image != result.image
        or receipt.captured_at != result.captured_at
    ):
        raise ValueError("fresh capture differs from its verified replay and actual capture")


def _fresh_implementation(qualification):
    # The native fresh qualification explicitly enumerates its installed Python
    # readers. This avoids an expensive source replay merely to recheck host code.
    for member in qualification.implementation_members:
        relative = member.artifact.name.replace("__", "/")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("fresh implementation source path is not portable")
        raw = (Path(__file__).parent / path).read_bytes()
        if (
            len(raw) != member.artifact.bytes
            or hashlib.sha256(raw).hexdigest() != member.artifact.sha256
        ):
            raise ValueError("fresh implementation source differs from qualification")


def _subject(child, run_id, qualification):
    lifecycle = child.read_controlled_lifecycle(run_id)
    state, invocation, intent = lifecycle.terminal, lifecycle.invocation, lifecycle.intent
    if (
        state is None
        or invocation is None
        or intent is None
        or state.status is not RunStatus.SUCCEEDED
        or f"{state.skill_id}@{state.skill_version}" != qualification.skill_ref
        or state.invocation_digest != invocation.invocation_digest
        or intent.descriptor_sha256 != qualification.descriptor.sha256
        or intent.implementation_sha256 != qualification.implementation_sha256
        or intent.qualification_sha256 != qualification.qualification.sha256
        or any(event.stage.startswith("qualification_candidate.") for event in state.events)
    ):
        raise ValueError("native admission requires exact registered production child authority")
    return state, invocation, hashlib.sha256(_canonical(intent)).hexdigest()


def admit_native_terminal(
    *,
    application,
    artifact_store,
    run_id,
    qualification_admission,
    child_authority,
    predecessor_terminal_admission=None,
):
    """Deeply reread an already-successful registered child; never run a pending Skill."""
    if (
        type(application) not in _APPS
        or type(artifact_store) is not LocalArtifactStore
        or type(child_authority) is not SQLiteRunStore
        or type(qualification_admission) is not VerifiedTypedQualificationAdmission
    ):
        raise TypeError(
            "terminal admission requires exact application, CAS, SQLite "
            "and qualification capability"
        )
    skill, schema = _APPS[type(application)]
    # Compile has no public store accessor. This one private attribute is read only
    # for exact identity, as in the sealed binding factory; no private reader is invoked.
    actual_store = (
        application._store
        if type(application) is Image2EnvGenesisCompileApplication
        else application.producer.artifact_store
        if type(application) is GenesisSceneApplication
        else application.store
    )
    if artifact_store is not actual_store:
        raise ValueError("native deep reader must use this exact CAS store instance")
    if (
        skill == "genesis.observe@3.0.0"
        and type(predecessor_terminal_admission) is not VerifiedLiveNativeTerminal
    ):
        raise TypeError("fresh live admission requires a live verified replay predecessor")
    qualification = qualification_admission.admission
    if skill != qualification.skill_ref:
        raise ValueError("native application and qualification Skill differ")
    descriptors = application.skills
    if (
        len(descriptors) != 1
        or hashlib.sha256(_canonical(descriptors[0])).hexdigest() != qualification.descriptor.sha256
    ):
        raise ValueError("native admission requires the exact current registered descriptor")
    state, invocation, intent_sha = _subject(child_authority, run_id, qualification)
    if application.run_state(run_id) != state or application.invocation(run_id) != invocation:
        raise ValueError("native app and supplied child authority differ")
    refs = tuple(ref for ref in state.artifacts if ref.schema_version == schema)
    if len(refs) != 1:
        raise ValueError("native terminal requires one exact typed output receipt")
    source_ref = _put(
        artifact_store,
        Path(__file__).read_bytes(),
        "native_terminal_admission.py",
        None,
        "text/x-python",
    )

    def deep_read():
        verify_typed_qualification_admission(artifact_store=artifact_store, admission=qualification)
        artifact_store.resolve(source_ref)
        if type(application) is Image2EnvGenesisCompileApplication:
            parameters = Image2EnvGenesisCompileRequestV1.model_validate(
                invocation.effective_parameters
            )
            result = application.invoke_typed(parameters, run_id=run_id)
            if result != state:
                raise ValueError("compile deep recovery changed terminal")
        elif type(application) is GenesisSceneApplication:
            parameters = GenesisSceneReplayInputV2.model_validate(invocation.effective_parameters)
            result = application.invoke(parameters, run_id=run_id)
            if result != state:
                raise ValueError("replay deep recovery changed terminal")
        elif type(application) is GenesisFreshCaptureApplication:
            _fresh_implementation(qualification)
            _read_fresh(
                application,
                artifact_store,
                refs[0],
                state,
                invocation,
                predecessor_terminal_admission,
            )
        else:
            ref, result = application.read(run_id)
            if ref != refs[0] or result.model_dump(mode="json") != state.output:
                raise ValueError("native public deep read changed terminal output")
        raw = artifact_store.resolve(refs[0]).path.read_bytes()
        if json.loads(raw) != state.output:
            raise ValueError("native output differs from its canonical receipt")

    observed = observe_native_reads(artifact_store=artifact_store, read=deep_read)
    if _subject(child_authority, run_id, qualification) != (state, invocation, intent_sha):
        raise ValueError("native child changed during public deep read")
    index_ref = _put(
        artifact_store, observed.closure, "native-read-closure.json", NATIVE_READ_CLOSURE_ID
    )
    admission = NativeTerminalAdmissionV1(
        schema_version=NATIVE_TERMINAL_ADMISSION_ID,
        skill_ref=skill,
        run_id=run_id,
        invocation=invocation,
        run_state=state,
        controlled_intent_sha256=intent_sha,
        native_receipt=refs[0],
        qualification_admission=qualification_admission.admission_ref,
        application_implementation_sha256=qualification.implementation_sha256,
        admission_implementation=source_ref,
        read_closure=index_ref,
        predecessor_terminal_admission=(
            predecessor_terminal_admission.admission_ref
            if predecessor_terminal_admission is not None
            else None
        ),
        authority="observed_successful_public_deep_read",
        simulator_rerun=False,
    )
    # The admission is the final authoritative publication after every deep read succeeded.
    admission_ref = _put(
        artifact_store, admission, "native-terminal-admission.json", NATIVE_TERMINAL_ADMISSION_ID
    )
    return VerifiedLiveNativeTerminal(admission, admission_ref, token=_TOKEN)


def reload_native_terminal(
    *,
    artifact_store,
    admission_ref,
    child_authority,
    expected_skill_ref,
    expected_run_id,
    qualification_admission,
    predecessor_terminal_admission=None,
):
    """Verify a trusted recorded admission reference in relocated CAS, without live authority."""
    if (
        type(artifact_store) is not LocalArtifactStore
        or type(child_authority) is not SQLiteRunStore
        or type(qualification_admission) is not VerifiedTypedQualificationAdmission
    ):
        raise TypeError("reload requires exact CAS, SQLite and qualification capability")
    admission = _read(artifact_store, admission_ref, NativeTerminalAdmissionV1)
    q = qualification_admission.admission
    verify_typed_qualification_admission(artifact_store=artifact_store, admission=q)
    if (
        admission.skill_ref != expected_skill_ref
        or admission.run_id != expected_run_id
        or admission.qualification_admission != qualification_admission.admission_ref
        or admission.application_implementation_sha256 != q.implementation_sha256
    ):
        raise ValueError("terminal admission belongs to another requested subject or qualification")
    state, invocation, intent_sha = _subject(child_authority, expected_run_id, q)
    if admission.predecessor_terminal_admission is not None:
        parameters = GenesisFreshCaptureInputV1.model_validate(invocation.effective_parameters)
        if admission.predecessor_terminal_admission != _predecessor(
            predecessor_terminal_admission, "image2env.replay@2.0.0", parameters.replay_run_id
        ):
            raise ValueError("reloaded admission has a different replay predecessor")
    if (
        state != admission.run_state
        or invocation != admission.invocation
        or intent_sha != admission.controlled_intent_sha256
    ):
        raise ValueError("terminal admission differs from trusted SQLite lifecycle")
    index = _read(artifact_store, admission.read_closure, NativeReadClosureV1)
    for ref in index.artifacts:
        artifact_store.resolve(ref)
    for member in index.digest_members:
        if artifact_store.resolve_digest(member.sha256).stat().st_size != member.bytes:
            raise ValueError("read digest member size differs")
    if (
        admission.native_receipt not in index.artifacts
        or admission.admission_implementation not in index.artifacts
        or admission.qualification_admission not in index.artifacts
        or json.loads(artifact_store.resolve(admission.native_receipt).path.read_bytes())
        != state.output
    ):
        raise ValueError("admission read closure lacks exact authoritative root artifacts")
    return VerifiedNativeTerminalReload(admission, admission_ref, token=_TOKEN)

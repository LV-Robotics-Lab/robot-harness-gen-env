"""Bind fixed child execution to runtime bytes and independently recheck its closure."""

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .artifacts import LocalArtifactStore
from .genesis_asset_probe import (
    GenesisAssetProbeArtifacts,
    GenesisAssetProbeEvidenceVerifier,
    VerifiedGenesisAssetProbeEvidence,
)
from .genesis_runtime_manifest import read_runtime_manifest
from .schemas.common import ArtifactRef, DependencyRef
from .schemas.genesis_asset_probe import (
    GenesisAssetContactTraceRowV1,
    GenesisAssetContactTraceV1,
    GenesisAssetLoadEvidenceV1,
    GenesisAssetLoadObservationV1,
    GenesisAssetProbeInputV1,
    GenesisAssetProbeReportV1,
)
from .schemas.genesis_runtime_execution import (
    GenesisRuntimeExecutionReceiptV1,
    GenesisRuntimeExecutionRequestV1,
    GenesisRuntimeObservationV1,
)
from .schemas.genesis_runtime_manifest import GenesisRuntimeManifestV1
from .schemas.x2env_compile_evidence import GenesisRuntimeLockV1


def runtime_backend_identity(manifest: GenesisRuntimeManifestV1) -> str:
    """Identity of all backend members, never supplied by the probe caller."""
    payload = [
        member.model_dump(mode="json") for member in manifest.members if member.root == "backend"
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class GenesisRuntimeExecutionError(ValueError):
    """Fixed execution contract or evidence closure is inconsistent."""

    diagnostic_ref: ArtifactRef | None = None


@dataclass(frozen=True)
class VerifiedGenesisRuntimeClosure:
    """CAS consistency result, deliberately not evidence of a live execution."""

    request: GenesisRuntimeExecutionRequestV1
    observation: GenesisRuntimeObservationV1
    probe: VerifiedGenesisAssetProbeEvidence
    execution_authority: bool = field(default=False, init=False)


_LIVE_EXECUTION_TOKEN = object()


@dataclass(frozen=True, init=False)
class LiveGenesisRuntimeExecution:
    """Only the fixed producer can construct this live, nonserializable capability."""

    receipt_ref: ArtifactRef
    closure: VerifiedGenesisRuntimeClosure

    def __init__(self, receipt_ref, closure, *, token=None):
        if token is not _LIVE_EXECUTION_TOKEN:
            raise TypeError("live capability can only originate from the fixed producer")
        object.__setattr__(self, "receipt_ref", receipt_ref)
        object.__setattr__(self, "closure", closure)


def _canonical(model):
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _read(store, ref, model, *, legacy=False):
    if (
        ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.media_type != "application/json"
        or ref.schema_version != model.model_config["json_schema_extra"]["$id"]
    ):
        raise GenesisRuntimeExecutionError("unexpected artifact schema or locator")
    payload = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(payload)
    if payload != _canonical(value) + (b"\n" if legacy else b""):
        raise GenesisRuntimeExecutionError("noncanonical evidence bytes")
    return value


@dataclass(frozen=True)
class GenesisRuntimeExecutionVerifier:
    artifact_store: LocalArtifactStore

    def read_receipt(self, receipt_ref: ArtifactRef) -> VerifiedGenesisRuntimeClosure:
        """Recompute persisted closure, without reconstructing live execution authority."""
        receipt = _read(self.artifact_store, receipt_ref, GenesisRuntimeExecutionReceiptV1)
        result = self.verify_observation(receipt.request, receipt.observation)
        if result.observation.probe_report != receipt.probe_report:
            raise GenesisRuntimeExecutionError("receipt report differs from executed observation")
        return result

    def verify_request(self, request_ref: ArtifactRef):
        """Read a request and require fresh-lock binding to this manifest/backend."""
        request = _read(self.artifact_store, request_ref, GenesisRuntimeExecutionRequestV1)
        manifest = read_runtime_manifest(self.artifact_store, request.manifest)
        runtime = _read(
            self.artifact_store, request.runtime_lock, GenesisRuntimeLockV1, legacy=True
        )
        expected = DependencyRef(
            name="genesis_runtime_manifest",
            version="harness.genesis_runtime_manifest.v1",
            sha256=request.manifest.sha256,
        )
        if expected not in runtime.dependencies:
            raise GenesisRuntimeExecutionError("runtime lock lacks exact manifest dependency")
        if runtime.runtime_implementation_sha256 != runtime_backend_identity(manifest):
            raise GenesisRuntimeExecutionError(
                "runtime implementation is not the full backend root"
            )
        if request.runner not in manifest.members:
            raise GenesisRuntimeExecutionError("fixed runner is absent from runtime manifest")
        return request, manifest, runtime

    def verify_observation(self, request_ref: ArtifactRef, observation_ref: ArtifactRef):
        request, manifest, _ = self.verify_request(request_ref)
        observation = _read(self.artifact_store, observation_ref, GenesisRuntimeObservationV1)
        if observation.request != request_ref:
            raise GenesisRuntimeExecutionError("observation belongs to another request")
        if observation.process_identity != manifest.process_identity:
            raise GenesisRuntimeExecutionError("observed process differs from manifest")
        declared = {(member.root, member.path): member for member in manifest.members}
        for group in (
            observation.modules,
            observation.native_files,
            observation.external_native_files,
        ):
            keys = tuple((member.root, member.path) for member in group)
            if keys != tuple(sorted(set(keys))):
                raise GenesisRuntimeExecutionError("observed members must be sorted and unique")
            if any(declared.get(key) != member for key, member in zip(keys, group, strict=True)):
                raise GenesisRuntimeExecutionError("observed member is outside runtime manifest")
        external = tuple((member.root, member.path) for member in observation.external_native_files)
        if external != (
            ("native", "usr/lib/x86_64-linux-gnu/libGLX.so.0.0.0"),
            ("native", "usr/lib/x86_64-linux-gnu/libOpenGL.so.0.0.0"),
        ):
            raise GenesisRuntimeExecutionError("unexpected external native members")
        child = ("backend", "self_improving/harness/_genesis_cpu_child.py")
        if child not in {(member.root, member.path) for member in observation.modules}:
            raise GenesisRuntimeExecutionError("fixed child module was not observed")
        for stream in (observation.stdout, observation.stderr):
            if (
                stream.uri != f"artifact://sha256/{stream.sha256}"
                or stream.media_type != "text/plain"
                or stream.schema_version is not None
            ):
                raise GenesisRuntimeExecutionError("invalid child stream reference")
            self.artifact_store.resolve(stream)
        artifacts = self._probe(observation)
        probe = GenesisAssetProbeEvidenceVerifier(self.artifact_store).verify(
            artifacts,
            representation_ref=request.representation,
            runtime_lock_ref=request.runtime_lock,
        )
        return VerifiedGenesisRuntimeClosure(request, observation, probe)

    def _probe(self, observation):
        models = (
            ("probe_input", GenesisAssetProbeInputV1),
            ("load_observation", GenesisAssetLoadObservationV1),
            ("load_evidence", GenesisAssetLoadEvidenceV1),
            ("contact_trace", GenesisAssetContactTraceV1),
            ("probe_report", GenesisAssetProbeReportV1),
        )
        fields = {}
        for name, model in models:
            reference = getattr(observation, name)
            fields[name] = _read(self.artifact_store, reference, model, legacy=True)
            fields[name + "_ref"] = reference
        rows = self.artifact_store.resolve(observation.trace_rows).path.read_bytes()
        fields["rows"] = tuple(
            GenesisAssetContactTraceRowV1.model_validate_json(row) for row in rows.splitlines()
        )
        fields["trace_rows_ref"] = observation.trace_rows
        return GenesisAssetProbeArtifacts(**fields)


@dataclass(frozen=True)
class GenesisRuntimeExecutionProducer:
    """Operator-configured runtime approval; requests cannot substitute its runtime."""

    artifact_store: LocalArtifactStore
    approved_manifest_ref: ArtifactRef

    def produce(self, request_ref: ArtifactRef, *, roots) -> LiveGenesisRuntimeExecution:
        verifier = GenesisRuntimeExecutionVerifier(self.artifact_store)
        try:
            request, _, _ = verifier.verify_request(request_ref)
            if request.manifest != self.approved_manifest_ref:
                raise GenesisRuntimeExecutionError("request runtime is not operator-approved")
            from .genesis_runtime_launcher import execute_genesis_runtime_probe

            observation_ref = execute_genesis_runtime_probe(
                store=self.artifact_store,
                request_ref=request_ref,
                roots=roots,
            )
            closure = verifier.verify_observation(request_ref, observation_ref)
            receipt = GenesisRuntimeExecutionReceiptV1(
                schema_version="harness.genesis_runtime_execution_receipt.v1",
                authority="observed_execution_only",
                request=request_ref,
                observation=observation_ref,
                probe_report=closure.observation.probe_report,
            )
            receipt_ref = self._publish(
                _canonical(receipt),
                "genesis-runtime-execution-receipt.json",
                receipt.schema_version,
            )
            return LiveGenesisRuntimeExecution(receipt_ref, closure, token=_LIVE_EXECUTION_TOKEN)
        except Exception as error:
            diagnostic = {
                "status": "failed",
                "request": request_ref.model_dump(mode="json"),
                "error_type": type(error).__name__,
                "message": str(error),
                "failure_artifacts": [
                    reference.model_dump(mode="json")
                    for reference in getattr(error, "failure_artifacts", ())
                ],
            }
            failure_ref = self._publish(
                json.dumps(diagnostic, sort_keys=True, separators=(",", ":")).encode(),
                "genesis-runtime-execution-failure.json",
                None,
            )
            failed = GenesisRuntimeExecutionError(str(error))
            failed.diagnostic_ref = failure_ref
            raise failed from error

    def _publish(self, payload, name, schema):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record"
            path.write_bytes(payload)
            return self.artifact_store.put_file(
                path, name=name, media_type="application/json", schema_version=schema
            )

#!/usr/bin/env python3
"""Build and qualify validate v2 from one completed Golden compile/replay run."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import EventPage, SQLiteEventJournal
from self_improving.harness.qualify_validate import (
    ValidateQualificationSettings,
    generate_validate_qualification,
)
from self_improving.harness.run_receipts import PortableRunReceiptPublisher
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import (
    ArtifactRef,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
)
from self_improving.harness.schemas.text2env import (
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayOutput,
)
from self_improving.harness.schemas.text2env_validate_v2 import Text2EnvValidateV2Input
from self_improving.harness.schemas.workflow_operation import WorkflowOperationReceiptV2

_DESCRIPTOR = TypeAdapter(RegisteredSkillDescriptor)


@dataclass(frozen=True, slots=True)
class _Source:
    artifact_root: Path
    descriptor: RegisteredSkillDescriptor
    journal: SQLiteEventJournal
    store: SQLiteRunStore

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return (self.descriptor,)

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        return self.journal.read(
            after_event_id=after_event_id,
            run_id=run_id,
            limit=limit,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.store.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.store.read_run_state(run_id)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-cas", type=Path, required=True)
    parser.add_argument("--compile-state", type=Path, required=True)
    parser.add_argument("--replay-state", type=Path, required=True)
    parser.add_argument("--compile-descriptor", type=Path, required=True)
    parser.add_argument("--replay-descriptor", type=Path, required=True)
    parser.add_argument("--compile-run-id", type=UUID, required=True)
    parser.add_argument("--replay-run-id", type=UUID, required=True)
    parser.add_argument("--compile-workflow-receipt", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--distribution-root", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if args.evidence_root.exists():
        raise RuntimeError("evidence-root must be new")
    source_store = LocalArtifactStore(args.source_cas.resolve(strict=True))
    destination = LocalArtifactStore(args.evidence_root)
    shutil.copytree(
        source_store.root / "sha256",
        destination.root / "sha256",
        dirs_exist_ok=True,
    )
    compile_source = _source(args, label="compile")
    replay_source = _source(args, label="replay")
    publisher = PortableRunReceiptPublisher(destination)
    compile_portable = publisher.publish(compile_source, args.compile_run_id)
    replay_portable = publisher.publish(replay_source, args.replay_run_id)
    compile_invocation = compile_source.invocation(args.compile_run_id)
    compile_state = compile_source.run_state(args.compile_run_id)
    replay_state = replay_source.run_state(args.replay_run_id)
    if compile_invocation is None or compile_state is None or replay_state is None:
        raise RuntimeError("source child authorities are incomplete")
    compile_output = Text2EnvCompileOutput.model_validate(compile_state.output)
    replay_output = Text2EnvReplayOutput.model_validate(replay_state.output)
    package_ref = _put_json(
        destination,
        args.evidence_root,
        name="environment_package",
        value=compile_output.environment_package,
        schema_version="harness.environment_package.v1",
    )
    by_schema = {ref.schema_version: ref for ref in replay_state.artifacts}
    execution_ref = _one(by_schema, "harness.text2env_replay_receipt.v1")
    runtime_report = _one(by_schema, "robotwin.scene_validation.v1")
    runtime_assets = _one(by_schema, "harness.runtime_asset_snapshot.v1")
    transcript = _one(by_schema, "harness.runtime_event_transcript.v1")
    execution = _load_json(source_store.resolve(execution_ref).path)
    media_ref = _put_json(
        destination,
        args.evidence_root,
        name="media_verification_receipt",
        value={
            "schema_version": "harness.replay_media_verification.v1",
            "verification": execution["media_verification"],
        },
        schema_version="harness.replay_media_verification.v1",
    )
    compile_workflow_ref = _copy_document(
        source_store,
        destination,
        args.compile_workflow_receipt,
        name="compile_workflow_receipt",
        schema_version="harness.workflow_operation_receipt.v2",
    )
    compile_workflow = WorkflowOperationReceiptV2.model_validate_json(
        destination.resolve(compile_workflow_ref).path.read_bytes()
    )
    compile_input = Text2EnvCompileInput.model_validate(compile_invocation.effective_parameters)
    provenance = _put_json(
        destination,
        args.evidence_root,
        name="request_provenance",
        value={
            "schema_version": "harness.text2env_request_provenance.v1",
            "request": compile_input.request,
            "seed": compile_input.seed,
            "compile_skill_ref": "text2env.compile@1.0.0",
            "compile_run_id": str(compile_state.run_id),
            "compile_invocation_digest": compile_invocation.invocation_digest,
            "compile_trusted_receipt": compile_workflow.tool_receipt.model_dump(mode="json"),
            "compile_history_authority": compile_workflow_ref.model_dump(mode="json"),
            "compile_world_state_sha256": compile_workflow.resulting_state_sha256,
            "environment_package": package_ref.model_dump(mode="json"),
        },
        schema_version="harness.text2env_request_provenance.v1",
    )
    fixed = Text2EnvValidateV2Input(
        environment_package=package_ref,
        compile_run_receipt=compile_portable,
        compile_qualification=compile_source.descriptor.qualification_artifact,
        replay_run_receipt=replay_portable,
        replay_qualification=replay_source.descriptor.qualification_artifact,
        replay_execution_receipt=execution_ref,
        runtime_evidence=replay_output.runtime_evidence,
        runtime_validation_report=runtime_report,
        runtime_asset_snapshot_manifest=runtime_assets,
        media_verification_receipt=media_ref,
        request_provenance=provenance,
        runtime_event_transcript=transcript,
    )
    fixed_path = args.evidence_root.parent / "fixed_validate_input.json"
    fixed_path.write_bytes(_canonical_bytes(fixed))
    result = generate_validate_qualification(
        ValidateQualificationSettings(
            bundle_root=args.bundle_root,
            scratch_root=args.scratch_root,
            distribution_root=args.distribution_root,
            scene_gen_root=args.distribution_root / "scene_gen",
            ledger_contract_root=(
                args.distribution_root
                / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
            ),
            evidence_artifact_root=destination.root,
            fixed_input=fixed,
        )
    )
    summary = {
        "bundle_root": str(result.bundle_root),
        "evidence_artifact_root": str(destination.root),
        "fixed_input": str(fixed_path),
        "implementation_sha256": result.manifest.bundle_sha256,
        "qualification_report_sha256": result.qualification.report_sha256,
    }
    (args.evidence_root.parent / "qualification_summary.json").write_bytes(
        _canonical_bytes(summary)
    )


def _source(args: argparse.Namespace, *, label: str) -> _Source:
    descriptor_path = getattr(args, f"{label}_descriptor")
    state_path = getattr(args, f"{label}_state")
    descriptor = _DESCRIPTOR.validate_json(descriptor_path.read_bytes())
    return _Source(
        artifact_root=args.source_cas.resolve(strict=True),
        descriptor=descriptor,
        journal=SQLiteEventJournal(state_path),
        store=SQLiteRunStore(state_path),
    )


def _copy_document(
    source: LocalArtifactStore,
    destination: LocalArtifactStore,
    path: Path,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    payload = path.read_bytes()
    digest = __import__("hashlib").sha256(payload).hexdigest()
    ref = ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=len(payload),
        schema_version=schema_version,
    )
    source.resolve(ref)
    return _put_bytes(destination, path.parent, payload, name=name, schema_version=schema_version)


def _put_json(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    return _put_bytes(
        store,
        root,
        _canonical_bytes(value),
        name=name,
        schema_version=schema_version,
    )


def _put_bytes(
    store: LocalArtifactStore,
    root: Path,
    payload: bytes,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f".{name}.json"
    path.write_bytes(payload)
    try:
        return store.put_file(
            path,
            name=name,
            media_type="application/json",
            schema_version=schema_version,
        )
    finally:
        path.unlink(missing_ok=True)


def _one(values: dict[str | None, ArtifactRef], schema: str) -> ArtifactRef:
    try:
        return values[schema]
    except KeyError as error:
        raise RuntimeError(f"replay output lacks {schema}") from error


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError("expected JSON object")
    return value


def _canonical_bytes(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


if __name__ == "__main__":
    main()

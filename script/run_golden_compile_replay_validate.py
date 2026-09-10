#!/usr/bin/env python3
"""Run one real qualified compile -> replay -> legacy validate Golden workflow."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from self_improving.harness.application import (
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_compile_execution import (
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_execution import create_golden_execution_catalog
from self_improving.harness.golden_replay_execution import (
    create_text2env_replay_execution_binding,
)
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.golden_validate_execution import (
    create_text2env_validate_execution_binding,
)
from self_improving.harness.replay_application import (
    ReplayApplicationSettings,
    create_replay_application,
)
from self_improving.harness.run_receipts import PortableRunReceiptPublisher
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import ArtifactRef, RunStatus
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)
from self_improving.harness.schemas.text2env import (
    CompileConfig,
    RuntimeConfig,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from self_improving.harness.schemas.text2env_validate_v2 import (
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
)
from self_improving.harness.schemas.workflow import (
    RunCommandRequest,
    RunReadRequest,
    RunStartRequest,
)
from self_improving.harness.schemas.workflow_operation import (
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.trusted_state import TrustedWorldState, WorldFactEvidence
from self_improving.harness.validate_application import (
    ValidateApplicationSettings,
    create_validate_application,
)

_WORKFLOW_ID = UUID("91000000-0000-4000-8000-000000000001")
_OPERATION_IDS = (
    UUID("92000000-0000-4000-8000-000000000001"),
    UUID("92000000-0000-4000-8000-000000000002"),
    UUID("92000000-0000-4000-8000-000000000003"),
)
_CHILD_IDS = (
    UUID("93000000-0000-4000-8000-000000000001"),
    UUID("93000000-0000-4000-8000-000000000002"),
    UUID("93000000-0000-4000-8000-000000000003"),
)
_REQUEST = "Place a can on top of a plate."


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--robotwin-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--interpreter", type=Path, required=True)
    parser.add_argument("--runtime-capability", type=Path, required=True)
    parser.add_argument("--media-launcher", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--delegated-cgroup-root", type=Path, required=True)
    parser.add_argument("--replay-qualification-cas", type=Path, required=True)
    parser.add_argument("--validate-qualification-cas", type=Path, required=True)
    return parser.parse_args()


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


def _put_json(
    store: LocalArtifactStore,
    staging: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"{name}.json"
    path.write_bytes(_canonical_bytes(value))
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _load(store: LocalArtifactStore, ref: ArtifactRef, model):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _import_qualification_cas(store: LocalArtifactStore, root: Path) -> int:
    source = root.resolve(strict=True)
    digest_root = source / "sha256"
    if not digest_root.is_dir():
        raise RuntimeError(f"qualification CAS has no sha256 directory: {source}")
    imported = 0
    for path in sorted(digest_root.glob("*/*")):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"qualification CAS contains a non-file member: {path}")
        expected = path.name
        if len(expected) != 64 or path.parent.name != expected[:2]:
            raise RuntimeError(f"qualification CAS member has an invalid digest path: {path}")
        ref = store.put_file(
            path,
            name=f"qualification_{expected}",
            media_type="application/octet-stream",
            schema_version=None,
        )
        if ref.sha256 != expected:
            raise RuntimeError(f"qualification CAS member digest mismatch: {path}")
        imported += 1
    if imported == 0:
        raise RuntimeError(f"qualification CAS is empty: {source}")
    return imported


def _registry(store: LocalArtifactStore, staging: Path, repository: Path, apps) -> ArtifactRef:
    entries = []
    for app in apps:
        descriptor = app.skills[0]
        label = descriptor.skill_id.rsplit(".", maxsplit=1)[-1]
        descriptor_ref = _put_json(
            store,
            staging,
            name=f"{label}-descriptor",
            value=descriptor,
            schema_version=(
                "harness.skill_descriptor.v2"
                if label == "replay"
                else "harness.skill_descriptor.v1"
            ),
        )
        report = store.put_file(
            repository
            / "self_improving/harness/qualified_skills"
            / descriptor.skill_id
            / descriptor.version
            / "report.json",
            name=f"{label}-qualification-report",
            media_type="application/json",
            schema_version="harness.skill_qualification_report.v1",
        )
        entries.append(
            QualifiedSkillSnapshot(
                skill_ref=f"{descriptor.skill_id}@{descriptor.version}",
                mcp_tool_name=descriptor.mcp_tool_name,
                descriptor_ref=descriptor_ref,
                qualification_ref=descriptor.qualification_artifact,
                qualification_report_ref=report,
            )
        )
    return _put_json(
        store,
        staging,
        name="compile-replay-validate-registry",
        value=RegistrySnapshot(
            schema_version="harness.registry_snapshot.v1",
            qualified_skills=tuple(entries),
        ),
        schema_version="harness.registry_snapshot.v1",
    )


def _validate_input(
    *,
    store: LocalArtifactStore,
    staging: Path,
    compile_app,
    replay_app,
    revision_one,
) -> Text2EnvValidateV2Input:
    publisher = PortableRunReceiptPublisher(store)
    compile_portable = publisher.publish(compile_app, _CHILD_IDS[0])
    replay_portable = publisher.publish(replay_app, _CHILD_IDS[1])
    compile_state = compile_app.run_state(_CHILD_IDS[0])
    compile_invocation = compile_app.invocation(_CHILD_IDS[0])
    replay_state = replay_app.run_state(_CHILD_IDS[1])
    if compile_state is None or compile_invocation is None or replay_state is None:
        raise RuntimeError("child durable authorities are incomplete")
    compile_output = Text2EnvCompileOutput.model_validate(compile_state.output)
    replay_output = Text2EnvReplayOutput.model_validate(replay_state.output)
    package = _put_json(
        store,
        staging,
        name="environment-package",
        value=compile_output.environment_package,
        schema_version="harness.environment_package.v1",
    )
    artifacts = {ref.schema_version: ref for ref in replay_state.artifacts}
    execution = artifacts["harness.text2env_replay_receipt.v1"]
    execution_document = json.loads(store.resolve(execution).path.read_bytes())
    media = _put_json(
        store,
        staging,
        name="media-verification-receipt",
        value={
            "schema_version": "harness.replay_media_verification.v1",
            "verification": execution_document["media_verification"],
        },
        schema_version="harness.replay_media_verification.v1",
    )
    compile_history = _load(store, revision_one.receipt_head, WorkflowOperationReceiptV2)
    compile_input = Text2EnvCompileInput.model_validate(compile_invocation.effective_parameters)
    provenance = _put_json(
        store,
        staging,
        name="request-provenance",
        value={
            "schema_version": "harness.text2env_request_provenance.v1",
            "request": compile_input.request,
            "seed": compile_input.seed,
            "compile_skill_ref": "text2env.compile@1.0.0",
            "compile_run_id": str(compile_state.run_id),
            "compile_invocation_digest": compile_invocation.invocation_digest,
            "compile_trusted_receipt": compile_history.tool_receipt.model_dump(mode="json"),
            "compile_history_authority": revision_one.receipt_head.model_dump(mode="json"),
            "compile_world_state_sha256": compile_history.resulting_state_sha256,
            "environment_package": package.model_dump(mode="json"),
        },
        schema_version="harness.text2env_request_provenance.v1",
    )
    return Text2EnvValidateV2Input(
        environment_package=package,
        compile_run_receipt=compile_portable,
        compile_qualification=compile_app.skills[0].qualification_artifact,
        replay_run_receipt=replay_portable,
        replay_qualification=replay_app.skills[0].qualification_artifact,
        replay_execution_receipt=execution,
        runtime_evidence=replay_output.runtime_evidence,
        runtime_validation_report=artifacts["robotwin.scene_validation.v1"],
        runtime_asset_snapshot_manifest=artifacts["harness.runtime_asset_snapshot.v1"],
        media_verification_receipt=media,
        request_provenance=provenance,
        runtime_event_transcript=artifacts["harness.runtime_event_transcript.v1"],
    )


def main() -> None:
    args = _arguments()
    output = args.output_root.expanduser().absolute()
    if output.exists():
        raise RuntimeError("output-root must be new")
    output.mkdir(parents=True)
    repository = args.repository.resolve(strict=True)
    robotwin = args.robotwin_root.resolve(strict=True)
    started = datetime.now(timezone.utc)
    compile_app = create_compile_application(
        CompileApplicationSettings(
            state_root=output / "compile-state",
            external_catalog_roots=(args.catalog.resolve(strict=True).parent,),
            allowed_asset_roots=(robotwin,),
            admission_date=date(2026, 9, 10),
            asset_library_root=output / "asset-library",
        )
    )
    store = LocalArtifactStore(compile_app.artifact_root)
    replay_qualification_objects = _import_qualification_cas(
        store, args.replay_qualification_cas
    )
    validate_qualification_objects = _import_qualification_cas(
        store, args.validate_qualification_cas
    )
    replay_app = create_replay_application(
        ReplayApplicationSettings(
            state_root=output / "replay-state",
            qualification_bundle_root=(
                repository
                / "self_improving/harness/qualified_skills/text2env.replay/1.0.0"
            ),
            evidence_artifact_root=store.root,
            implementation_root=repository,
            scene_gen_root=repository / "scene_gen",
            ledger_contract_root=(
                repository / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
            ),
            allowed_asset_roots=(robotwin,),
            interpreter=args.interpreter.resolve(strict=True),
            runtime_runner=repository / "script/run_scene_runtime.py",
            runtime_module_root=repository,
            runtime_capability_path=args.runtime_capability.resolve(strict=True),
            media_launcher=args.media_launcher.resolve(strict=True),
            static_ffmpeg=args.ffmpeg.resolve(strict=True),
            delegated_cgroup_root=args.delegated_cgroup_root.resolve(strict=True),
            runtime_timeout_seconds=1800,
            capability_timeout_seconds=30,
        )
    )
    validate_app = create_validate_application(
        ValidateApplicationSettings(
            state_root=output / "validate-state",
            evidence_artifact_root=store.root,
        )
    )
    registry = _registry(
        store,
        output / "inputs",
        repository,
        (compile_app, replay_app, validate_app),
    )
    objective = _put_json(
        store,
        output / "inputs",
        name="objective",
        value=WorldFactEvidence(
            source_kind="user_input",
            key="request.objective",
            value=_REQUEST,
            observed_at=started,
            valid_until=None,
        ),
        schema_version="harness.world_fact_evidence.v1",
    )
    child_ids = iter(_CHILD_IDS)
    workflow_store = SQLiteGoldenRunStore(output / "golden.sqlite3")
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                create_text2env_compile_execution_binding(application=compile_app),
                create_text2env_replay_execution_binding(application=replay_app),
                create_text2env_validate_execution_binding(application=validate_app),
            )
        ),
        clock=lambda: datetime.now(timezone.utc),
        workflow_id_factory=lambda: _WORKFLOW_ID,
        child_run_id_factory=lambda: next(child_ids),
    )
    revision_zero = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="evidence/golden-p4",
            idempotency_key="golden-p4-start",
            workspace="ephemeral",
            requested_profile="legacy.robotwin_validation@2",
            initial_fact_evidence=(objective,),
            registry_snapshot=registry,
        )
    )
    compile_operation = harness.submit(
        _command(
            revision_zero,
            index=0,
            skill_ref="text2env.compile@1.0.0",
            parameters=Text2EnvCompileInput(
                request=_REQUEST,
                seed=7,
                asset_catalog=compile_app.snapshot_asset_catalog(args.catalog),
                config=CompileConfig(generate_missing_assets=False),
            ).model_dump(mode="json"),
        )
    )
    revision_one = _read_head(harness, revision_zero)
    state_one = _load(store, revision_one.state_ref, TrustedWorldState)
    package = next(fact.value for fact in state_one.facts if fact.key == "environment.package")
    replay_operation = harness.submit(
        _command(
            revision_one,
            index=1,
            skill_ref="text2env.replay@1.0.0",
            parameters=Text2EnvReplayInput(
                environment_package=package,
                runtime_config=RuntimeConfig(
                    precheck_steps=0,
                    settle_steps=900,
                    contact_window_steps=120,
                    video_frames=120,
                    fps=12,
                ),
            ).model_dump(mode="json"),
        )
    )
    revision_two = _read_head(harness, revision_one)
    validate_input = _validate_input(
        store=store,
        staging=output / "inputs",
        compile_app=compile_app,
        replay_app=replay_app,
        revision_one=revision_one,
    )
    (output / "inputs/validate-input.json").write_bytes(_canonical_bytes(validate_input))
    validate_command = _command(
        revision_two,
        index=2,
        skill_ref="text2env.validate@2.0.0",
        parameters=validate_input.model_dump(mode="json"),
    )
    validate_operation = harness.submit(validate_command)
    revision_three = _read_head(harness, revision_two)
    if any(
        operation.status is not RunStatus.SUCCEEDED
        for operation in (compile_operation, replay_operation, validate_operation)
    ):
        raise RuntimeError("one real Golden operation did not succeed")

    restarted = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                create_text2env_compile_execution_binding(
                    child_authority=SQLiteRunStore(compile_app.journal_path)
                ),
                create_text2env_replay_execution_binding(
                    child_authority=SQLiteRunStore(replay_app.journal_path)
                ),
                create_text2env_validate_execution_binding(
                    child_authority=SQLiteRunStore(validate_app.journal_path),
                    artifact_store=store,
                ),
            )
        ),
        clock=lambda: datetime.now(timezone.utc),
        child_run_id_factory=lambda: (_ for _ in ()).throw(
            RuntimeError("idempotent recovery reserved another child")
        ),
    )
    if restarted.submit(validate_command) != validate_operation:
        raise RuntimeError("restart resubmission did not recover the exact validate operation")
    result = _load(store, validate_operation.tool_result, ToolResultV2)
    validate_output = Text2EnvValidateV2Output.model_validate(result.typed_output)
    replay_state = replay_app.run_state(_CHILD_IDS[1])
    assert replay_state is not None
    replay_output = Text2EnvReplayOutput.model_validate(replay_state.output)
    media = _copy_media(store, replay_output, output / "view")
    receipt = _load(store, revision_three.receipt_head, WorkflowOperationReceiptV2)
    trusted = _load(store, result.trusted_receipt, TrustedToolReceiptV2)
    state_three = _load(store, revision_three.state_ref, TrustedWorldState)
    summary = {
        "schema_version": "golden.p4.compile_replay_validate_evidence.v1",
        "source_commit": _git_head(repository),
        "request": _REQUEST,
        "workflow_run_id": str(revision_three.workflow_run_id),
        "revisions": [0, 1, 2, 3],
        "operations": [
            compile_operation.model_dump(mode="json"),
            replay_operation.model_dump(mode="json"),
            validate_operation.model_dump(mode="json"),
        ],
        "validate_output": validate_output.model_dump(mode="json"),
        "validate_trusted_receipt": trusted.model_dump(mode="json"),
        "validate_workflow_receipt": receipt.model_dump(mode="json"),
        "state_revision_three": revision_three.state_ref.model_dump(mode="json"),
        "promoted_fact_keys": [fact.key for fact in state_three.facts],
        "fresh_observations": [item.model_dump(mode="json") for item in result.fresh_observations],
        "qualification_cas_imports": {
            "replay": replay_qualification_objects,
            "validate": validate_qualification_objects,
        },
        "restart_resubmit_same_operation": True,
        "media": media,
        "limits": {
            "genesis_native_claim": False,
            "promotion_executed": False,
            "robot_policy_claim": False,
        },
    }
    (output / "summary.json").write_bytes(_canonical_bytes(summary))


def _command(snapshot, *, index: int, skill_ref: str, parameters: dict) -> RunCommandRequest:
    return RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=_OPERATION_IDS[index],
        principal_id=snapshot.principal_id,
        workflow_run_id=snapshot.workflow_run_id,
        idempotency_key="golden-p4-qualified-step",
        expected_workflow_revision=snapshot.revision,
        expected_state_sha256=snapshot.state_sha256,
        expected_receipt_head_sha256=snapshot.receipt_head.sha256,
        skill_ref=skill_ref,
        parameters=parameters,
    )


def _read_head(harness: GoldenRunHarness, prior):
    return harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=prior.principal_id,
            workflow_run_id=prior.workflow_run_id,
        )
    )


def _copy_media(
    store: LocalArtifactStore,
    output: Text2EnvReplayOutput,
    root: Path,
) -> list[dict[str, object]]:
    root.mkdir(parents=True)
    records = []
    for ref in output.replay_artifacts:
        suffix = {"video/mp4": ".mp4", "image/png": ".png"}.get(ref.media_type)
        if suffix is None:
            continue
        target = root / f"{ref.name}-{ref.sha256[:12]}{suffix}"
        shutil.copy2(store.resolve(ref).path, target)
        records.append({"path": str(target), "artifact": ref.model_dump(mode="json")})
    return records


def _git_head(repository: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    main()

"""Faulty candidate adapters cannot qualify by returning unrelated real artifacts."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.genesis_asset_qualification_application import (
    GenesisAssetQualificationApplication,
)
from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
from self_improving.harness.image2env_genesis_compile_application import (
    Image2EnvGenesisCompileApplication,
)
from self_improving.harness.registry import (
    HandlerResult,
    QualificationCandidate,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import ArtifactRef, SkillDescriptor
from self_improving.harness.schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
    Image2EnvGenesisCompileRequestV1,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("GOLDEN_NATIVE_COMPILE_TERMINAL_SUMMARY"),
    reason="requires actual qualified native compile terminals",
)


@pytest.mark.parametrize(
    "variation", ["missing_receipt", "different_output", "different_input", "single_image"]
)
def test_candidate_output_must_match_real_receipt_and_durable_input(tmp_path, variation):
    config = json.loads(Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"]).read_text())
    summary_path = Path(os.environ["GOLDEN_NATIVE_COMPILE_TERMINAL_SUMMARY"])
    summary = json.loads(summary_path.read_text())
    store = LocalArtifactStore(Path(config["cas_root"]))
    source = SQLiteRunStore(summary_path.parent / "harness.sqlite3")
    run_id = UUID(summary["production_run_ids"][0])
    state = source.read_run_state(run_id)
    invocation = source.read_invocation(run_id)
    receipt = Image2EnvGenesisCompileReceiptV1.model_validate(state.output)
    receipt_ref = next(
        ref for ref in state.artifacts if ref.schema_version == receipt.schema_version
    )
    parameters = dict(invocation.effective_parameters)
    if variation == "different_input":
        parameters["compile_input"] = {**parameters["compile_input"], "seed": 99}
    output = receipt
    if variation == "different_output":
        output = receipt.model_copy(
            update={"source_object_id": "other", "proposal_object_id": "other"}
        )

    def external_adapter(value, context):
        return HandlerResult(output, () if variation == "missing_receipt" else (receipt_ref,))

    database = tmp_path / "harness.sqlite3"
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver(
            {"image2env.compile@2.0.0": invocation.dependencies}
        ),
        event_sink=SQLiteEventJournal(database),
        clock=lambda: datetime.now(timezone.utc),
        run_id_factory=uuid4,
        run_store=SQLiteRunStore(database),
    )
    candidate = QualificationCandidate(
        skill_id="image2env.compile",
        version="2.0.0",
        input_schema="harness.image2env_genesis_compile_request.v1",
        output_schema="harness.image2env_genesis_compile_receipt.v1",
        max_attempts=1,
    )
    evaluation = registry.evaluate_candidate(candidate, external_adapter, parameters)
    assert evaluation.state.status.value == "succeeded"
    assets = GenesisAssetQualificationApplication(
        Path(config["state_path"]),
        store,
        ArtifactRef.model_validate(config["approved_manifest_ref"]),
    )
    app = Image2EnvGenesisCompileApplication(tmp_path, Image2EnvGenesisCompiler(store, assets))
    message = {
        "missing_receipt": "one compile receipt",
        "different_output": "receipt or invocation",
        "different_input": "durable invocation",
        "single_image": "three distinct",
    }[variation]
    with pytest.raises(ValueError, match=message):
        app.qualify((evaluation.state.run_id,))
    assert app.skills == ()


def test_self_consistent_changed_qualification_report_does_not_replace_journal(tmp_path):
    config = json.loads(Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"]).read_text())
    store = LocalArtifactStore(Path(config["cas_root"]))
    summary_path = Path(os.environ["GOLDEN_NATIVE_COMPILE_TERMINAL_SUMMARY"])
    summary = json.loads(summary_path.read_text())
    qualification = json.loads(
        store.resolve(
            ArtifactRef.model_validate(summary["descriptor"]["qualification_artifact"])
        ).path.read_bytes()
    )
    report = json.loads(store.resolve_digest(qualification["report_sha256"]).read_bytes())
    report["implementation_sha256"] = "0" * 64

    def put(value, name, schema):
        path = tmp_path / name
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
        return store.put_file(path, name=name, media_type="application/json", schema_version=schema)

    report_ref = put(report, "changed-report.json", report["schema_version"])
    qualification["report_sha256"] = report_ref.sha256
    ref = put(qualification, "changed-qualification.json", "harness.skill_qualification.v1")
    assets = GenesisAssetQualificationApplication(
        Path(config["state_path"]),
        store,
        ArtifactRef.model_validate(config["approved_manifest_ref"]),
    )
    with pytest.raises(ValueError, match="does not match durable candidate evidence"):
        Image2EnvGenesisCompileApplication(
            summary_path.parent, Image2EnvGenesisCompiler(store, assets), qualification=ref
        )


@pytest.mark.parametrize("variation", ["missing_receipt", "different_output"])
def test_registered_terminal_from_faulty_adapter_fails_deep_recovery_without_execution(
    tmp_path, variation
):
    config = json.loads(Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"]).read_text())
    store = LocalArtifactStore(Path(config["cas_root"]))
    summary_path = Path(os.environ["GOLDEN_NATIVE_COMPILE_TERMINAL_SUMMARY"])
    summary = json.loads(summary_path.read_text())
    database = tmp_path / "harness.sqlite3"
    with (
        closing(
            sqlite3.connect(f"file:{summary_path.parent / 'harness.sqlite3'}?mode=ro", uri=True)
        ) as reader,
        closing(sqlite3.connect(database)) as writer,
    ):
        reader.backup(writer)
    runs = SQLiteRunStore(database)
    original_id = UUID(summary["production_run_ids"][0])
    invocation = runs.read_invocation(original_id)
    state = runs.read_run_state(original_id)
    output = Image2EnvGenesisCompileReceiptV1.model_validate(state.output)
    receipt_ref = next(
        ref for ref in state.artifacts if ref.schema_version == output.schema_version
    )
    if variation == "different_output":
        output = output.model_copy(
            update={"source_object_id": "other", "proposal_object_id": "other"}
        )
    descriptor = SkillDescriptor.model_validate(summary["descriptor"])
    assets = GenesisAssetQualificationApplication(
        Path(config["state_path"]),
        store,
        ArtifactRef.model_validate(config["approved_manifest_ref"]),
    )
    app = Image2EnvGenesisCompileApplication(
        tmp_path,
        Image2EnvGenesisCompiler(store, assets),
        qualification=descriptor.qualification_artifact,
    )
    calls = []

    def external_adapter(value, context):
        calls.append(context.run_id)
        return HandlerResult(output, () if variation == "missing_receipt" else (receipt_ref,))

    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver(
            {"image2env.compile@2.0.0": invocation.dependencies}
        ),
        event_sink=SQLiteEventJournal(database),
        clock=lambda: datetime.now(timezone.utc),
        run_id_factory=uuid4,
        run_store=runs,
    )
    registry.register(descriptor, external_adapter)
    run_id = uuid4()
    terminal = registry.invoke_with_run_id(
        "image2env.compile", "2.0.0", invocation.effective_parameters, run_id=run_id
    )
    assert terminal.status.value == "succeeded"
    with pytest.raises(ValueError, match="terminal native compile"):
        app.invoke_typed(
            Image2EnvGenesisCompileRequestV1.model_validate(invocation.effective_parameters),
            run_id=run_id,
        )
    assert calls == [run_id]
    assert runs.read_run_state(run_id) == terminal

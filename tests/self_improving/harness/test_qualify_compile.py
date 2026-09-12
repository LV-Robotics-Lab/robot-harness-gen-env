from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import self_improving.harness.handlers.text2env_compile as handler_module
import self_improving.harness.qualify_compile as module
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.qualification import load_qualification_bundle
from self_improving.harness.qualify_compile import (
    CompileQualificationError,
    CompileQualificationSettings,
    generate_compile_qualification,
)
from self_improving.harness.registry import HandlerResult

ROOT = Path(__file__).resolve().parents[3]
SCENE_GEN = ROOT / "scene_gen"
LEDGER = ROOT / "self_improving/asset_pipeline/active/asset_reuse/lib"


def _settings(tmp_path: Path, *, suffix: str = "") -> CompileQualificationSettings:
    return CompileQualificationSettings(
        bundle_root=tmp_path / f"bundle{suffix}",
        scratch_root=tmp_path / f"scratch{suffix}",
        distribution_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
        admission_date=date(2026, 8, 31),
    )


def _execution(tmp_path: Path) -> module._AcceptanceExecution:
    settings = _settings(tmp_path)
    return module._execute_candidate(settings, scratch_root=settings.scratch_root)


def _replace_run(
    execution: module._AcceptanceExecution,
    ordinal: int,
    run: module._CandidateRun,
) -> module._AcceptanceExecution:
    runs = list(execution.runs)
    runs[ordinal - 1] = run
    return replace(execution, runs=tuple(runs))


def _publish_json(
    execution: module._AcceptanceExecution,
    tmp_path: Path,
    payload: object,
    *,
    name: str,
    schema_version: str,
):
    path = tmp_path / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return execution.store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _replace_artifact(
    execution: module._AcceptanceExecution,
    tmp_path: Path,
    *,
    ordinal: int,
    schema_version: str,
    mutate,
) -> module._AcceptanceExecution:
    run = execution.runs[ordinal - 1]
    old = next(item for item in run.result.artifacts if item.schema_version == schema_version)
    payload = json.loads(execution.store.resolve(old).path.read_text(encoding="utf-8"))
    mutate(payload)
    new = _publish_json(
        execution,
        tmp_path,
        payload,
        name=f"attack_{ordinal}_{old.name}",
        schema_version=schema_version,
    )
    artifacts = tuple(new if item == old else item for item in run.result.artifacts)
    result = replace(run.result, artifacts=artifacts)
    output = run.output
    if schema_version == "robotwin.generated_scene_package.v1":
        package = output.environment_package.model_copy(update={"package_manifest": new})
        output = output.model_copy(update={"environment_package": package})
    elif schema_version == "robotwin.scene_validation.v1":
        output = output.model_copy(update={"static_validation": new})
    return _replace_run(
        execution,
        ordinal,
        replace(run, result=result, output=output),
    )


def _admission_payload(
    execution: module._AcceptanceExecution,
    ordinal: int,
) -> tuple[object, dict]:
    ref = next(
        item
        for item in execution.runs[ordinal - 1].result.artifacts
        if item.schema_version == "harness.generated_asset_admission.v1"
    )
    payload = json.loads(execution.store.resolve(ref).path.read_text(encoding="utf-8"))
    return ref, payload


def _replace_admission_payload(
    execution: module._AcceptanceExecution,
    tmp_path: Path,
    ordinal: int,
    payload: dict,
) -> module._AcceptanceExecution:
    run = execution.runs[ordinal - 1]
    old = next(
        item
        for item in run.result.artifacts
        if item.schema_version == "harness.generated_asset_admission.v1"
    )
    new = _publish_json(
        execution,
        tmp_path,
        payload,
        name=f"attack_admission_{ordinal}",
        schema_version="harness.generated_asset_admission.v1",
    )
    result = replace(
        run.result,
        artifacts=tuple(new if item == old else item for item in run.result.artifacts),
    )
    return _replace_run(execution, ordinal, replace(run, result=result))


def _ledger_path(execution: module._AcceptanceExecution) -> Path:
    _, admission = _admission_payload(execution, 1)
    return Path(admission["assets"][0]["ledger_path"])


def _rewrite_ledger_receipts(
    execution: module._AcceptanceExecution,
    tmp_path: Path,
) -> module._AcceptanceExecution:
    digest = hashlib.sha256(_ledger_path(execution).read_bytes()).hexdigest()
    updated = execution
    for ordinal in (1, 2, 3):
        _, admission = _admission_payload(updated, ordinal)
        admission["assets"][0]["ledger_sha256"] = digest
        updated = _replace_admission_payload(updated, tmp_path, ordinal, admission)
    return updated


def test_real_candidate_emits_loadable_three_document_bundle_with_detailed_evidence(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    result = generate_compile_qualification(settings)

    assert {path.name for path in result.bundle_root.iterdir()} == {
        "qualification.json",
        "report.json",
        "manifest.json",
    }
    assert [check.name for check in result.report.checks] == [
        "admission.lifecycle",
        "artifacts.cas_resolution",
        "invocation.stability",
        "ledger.v3_files",
        "package.binding",
        "source.stability",
        "static_validation.boundary",
    ]
    checks = {check.name: check.evidence for check in result.report.checks}
    assert checks["admission.lifecycle"]["observed"] == [
        "admitted",
        "reused",
        "reused",
    ]
    assert (
        checks["invocation.stability"]["first_invocation_sha256"]
        != checks["invocation.stability"]["second_invocation_sha256"]
    )
    assert (
        checks["invocation.stability"]["second_invocation_sha256"]
        == checks["invocation.stability"]["third_invocation_sha256"]
    )
    assert (
        checks["invocation.stability"]["second_output_sha256"]
        == checks["invocation.stability"]["third_output_sha256"]
    )
    assert checks["ledger.v3_files"] == {
        "schema_version": "asset_ledger.v3",
        "sha256": checks["admission.lifecycle"]["reports"][-1]["ledger_sha256"],
        "file_record_count": 3,
        "check_files": True,
        "violation_count": 0,
    }
    assert all(
        run["status"] == "incomplete" and run["fail_count"] == 0 and run["not_run_count"] > 0
        for run in checks["static_validation.boundary"]["runs"]
    )
    assert checks["static_validation.boundary"]["physical_runtime_claimed"] is False
    assert set(item.path for item in result.manifest.files) == set(module._IMPLEMENTATION_PATHS)
    raw_report = (result.bundle_root / "report.json").read_bytes()
    assert result.qualification.report_sha256 == hashlib.sha256(raw_report).hexdigest()

    loaded = load_qualification_bundle(
        result.bundle_root,
        skill_ref="text2env.compile@1.0.0",
        artifact_store=LocalArtifactStore(tmp_path / "qualification-cas"),
        implementation_root=ROOT,
        scene_gen_root=SCENE_GEN,
        ledger_contract_root=LEDGER,
    )
    assert loaded.report == result.report
    assert loaded.implementation_sha256 == result.manifest.bundle_sha256


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("bundle", "bundle_exists"),
        ("scratch", "scratch_exists"),
        ("date", "invalid_admission_date"),
    ],
)
def test_rejects_unsafe_output_preconditions(
    tmp_path: Path,
    kind: str,
    reason: str,
) -> None:
    settings = _settings(tmp_path)
    if kind == "bundle":
        settings.bundle_root.mkdir()
    elif kind == "scratch":
        settings.scratch_root.mkdir()
    else:
        settings = replace(settings, admission_date="2026-08-31")  # type: ignore[arg-type]

    with pytest.raises(CompileQualificationError) as captured:
        generate_compile_qualification(settings)

    assert captured.value.reason == reason


def test_rejects_implementation_path_escape(tmp_path: Path) -> None:
    distribution = tmp_path / "distribution"
    implementation = distribution / module._IMPLEMENTATION_PATHS[0]
    implementation.parent.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("outside = True\n", encoding="utf-8")
    implementation.symlink_to(outside)
    settings = replace(_settings(tmp_path), distribution_root=distribution)

    with pytest.raises(CompileQualificationError) as captured:
        generate_compile_qualification(settings)

    assert captured.value.reason == "implementation_path_invalid"
    assert not settings.bundle_root.exists()


def test_rejects_implementation_symlink_even_when_it_stays_inside_root(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "distribution"
    relative = Path(module._IMPLEMENTATION_PATHS[0])
    implementation = distribution / relative
    implementation.parent.mkdir(parents=True)
    real = implementation.with_name("real_artifacts.py")
    real.write_text("VALUE = 1\n", encoding="utf-8")
    implementation.symlink_to(real.name)
    settings = replace(_settings(tmp_path), distribution_root=distribution)

    with pytest.raises(CompileQualificationError) as captured:
        generate_compile_qualification(settings)

    assert captured.value.reason == "implementation_path_invalid"
    assert not settings.bundle_root.exists()


def test_rejects_implementation_manifest_entry_that_is_not_a_file(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "distribution"
    implementation = distribution / module._IMPLEMENTATION_PATHS[0]
    implementation.mkdir(parents=True)
    settings = replace(_settings(tmp_path), distribution_root=distribution)

    with pytest.raises(CompileQualificationError) as captured:
        generate_compile_qualification(settings)

    assert captured.value.reason == "implementation_path_invalid"
    assert not settings.bundle_root.exists()


def test_candidate_exception_cannot_publish_bundle(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)

    def fail_compile(*args, **kwargs):
        raise RuntimeError("injected candidate failure")

    monkeypatch.setattr(handler_module, "compile_scene", fail_compile)
    with pytest.raises(CompileQualificationError) as captured:
        generate_compile_qualification(settings)

    assert captured.value.reason == "candidate_execution_failed"
    assert not settings.bundle_root.exists()


def test_source_change_during_acceptance_cannot_publish_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    original = module._snapshot_sources
    calls = 0

    def changing_snapshot(value):
        nonlocal calls
        calls += 1
        snapshot = original(value)
        if calls == 2:
            return replace(snapshot, scene_gen_sha256="f" * 64)
        return snapshot

    monkeypatch.setattr(module, "_snapshot_sources", changing_snapshot)
    with pytest.raises(CompileQualificationError) as captured:
        generate_compile_qualification(settings)

    assert captured.value.reason == "source_changed"
    assert not settings.bundle_root.exists()


def test_rejects_wrong_run_count(tmp_path: Path) -> None:
    execution = _execution(tmp_path)

    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(replace(execution, runs=execution.runs[:2]))

    assert captured.value.reason == "run_count"


def test_rejects_wrong_admission_lifecycle(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    _, admission = _admission_payload(execution, 1)
    admission["status"] = "reused"
    admission["assets"][0]["disposition"] = "reused"
    attacked = _replace_admission_payload(execution, tmp_path, 1, admission)

    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(attacked)

    assert captured.value.reason == "admission_lifecycle"


def test_rejects_missing_execution_callbacks_or_overstated_physical_admission(
    tmp_path: Path,
) -> None:
    missing_events = _execution(tmp_path / "events")
    first = missing_events.runs[0]
    first = replace(
        first,
        event_stages=tuple(
            stage for stage in first.event_stages if stage != "compile.asset_admission.completed"
        ),
    )
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(_replace_run(missing_events, 1, first))
    assert captured.value.reason == "execution_events_missing"

    overstated = _execution(tmp_path / "physical")
    _, admission = _admission_payload(overstated, 1)
    admission["physical_qualification"] = "pass"
    overstated = _replace_admission_payload(
        overstated,
        tmp_path / "physical-attack",
        1,
        admission,
    )
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(overstated)
    assert captured.value.reason == "admission_physical_boundary"

    overstated_asset = _execution(tmp_path / "asset-physical")
    _, admission = _admission_payload(overstated_asset, 1)
    admission["assets"][0]["qualification"] = "physical_pass"
    overstated_asset = _replace_admission_payload(
        overstated_asset,
        tmp_path / "asset-physical-attack",
        1,
        admission,
    )
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(overstated_asset)
    assert captured.value.reason == "asset_qualification_boundary"


def test_rejects_asset_identity_and_unexpected_dependency_drift(tmp_path: Path) -> None:
    identity = _execution(tmp_path / "identity")
    _, admission = _admission_payload(identity, 3)
    admission["assets"][0]["asset_id"] = "different_generated_asset"
    identity = _replace_admission_payload(
        identity,
        tmp_path / "identity-attack",
        3,
        admission,
    )
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(identity)
    assert captured.value.reason == "asset_identity_drift"

    dependency = _execution(tmp_path / "dependency")
    first = dependency.runs[0]
    target = next(item for item in first.dependencies if item.name != "asset-library-state")
    changed = target.model_copy(update={"sha256": "f" * 64})
    first = replace(
        first,
        dependencies=tuple(changed if item == target else item for item in first.dependencies),
    )
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(_replace_run(dependency, 1, first))
    assert captured.value.reason == "unexpected_mutable_dependency"


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("output", "output_drift"),
        ("parameter", "parameter_drift"),
        ("dependency", "dependency_drift"),
        ("invocation", "invocation_drift"),
        ("mutable", "mutable_dependency_not_observed"),
    ],
)
def test_rejects_reuse_identity_drift(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    execution = _execution(tmp_path)
    first, second, third = execution.runs
    if attack == "output":
        scene_spec = third.output.scene_spec.model_copy(update={"name": "drifted"})
        third = replace(
            third,
            output=third.output.model_copy(update={"scene_spec": scene_spec}),
        )
    elif attack == "parameter":
        third = replace(
            third,
            parameters=third.parameters.model_copy(
                update={"request": "Place a different generated object on the table."}
            ),
        )
    elif attack == "dependency":
        dependency = third.dependencies[0].model_copy(update={"sha256": "f" * 64})
        third = replace(third, dependencies=(dependency, *third.dependencies[1:]))
    elif attack == "invocation":
        third = replace(third, invocation_sha256="f" * 64)
    else:
        first = replace(first, invocation_sha256=second.invocation_sha256)
    attacked = replace(execution, runs=(first, second, third))

    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(attacked)

    assert captured.value.reason == reason


def test_rejects_ledger_drift_across_reuse(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    _, admission = _admission_payload(execution, 1)
    original = Path(admission["assets"][0]["ledger_path"])
    clone = tmp_path / "alternate-ledger.json"
    clone.write_text(original.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    admission["assets"][0]["ledger_path"] = str(clone)
    admission["assets"][0]["ledger_sha256"] = hashlib.sha256(clone.read_bytes()).hexdigest()
    attacked = _replace_admission_payload(execution, tmp_path, 1, admission)

    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(attacked)

    assert captured.value.reason == "ledger_drift"


def test_rejects_unresolvable_or_empty_artifact_sets(tmp_path: Path) -> None:
    execution = _execution(tmp_path)
    first = execution.runs[0]
    target = first.result.artifacts[0]
    execution.store.resolve(target).path.unlink()

    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(execution)

    assert captured.value.reason == "artifact_unresolved"

    clean = _execution(tmp_path / "empty")
    first = clean.runs[0]
    empty = replace(first, result=HandlerResult(output=first.output, artifacts=()))
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(_replace_run(clean, 1, empty))
    assert captured.value.reason == "missing_artifacts"


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("report_count", "admission_report_count"),
        ("asset_count", "admission_asset_count"),
        ("status", "admission_status_mismatch"),
    ],
)
def test_rejects_ambiguous_admission_evidence(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    execution = _execution(tmp_path)
    run = execution.runs[0]
    if attack == "report_count":
        artifacts = tuple(
            item
            for item in run.result.artifacts
            if item.schema_version != "harness.generated_asset_admission.v1"
        )
        attacked = _replace_run(
            execution,
            1,
            replace(run, result=replace(run.result, artifacts=artifacts)),
        )
    else:
        _, admission = _admission_payload(execution, 1)
        if attack == "asset_count":
            admission["assets"] = []
        else:
            admission["status"] = "reused"
        attacked = _replace_admission_payload(execution, tmp_path, 1, admission)

    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(attacked)

    assert captured.value.reason == reason


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "evidence_unreadable"),
        ("{broken", "evidence_unreadable"),
        ("[]", "evidence_shape"),
    ],
)
def test_rejects_missing_malformed_or_non_object_ledger(
    tmp_path: Path,
    content: str | None,
    reason: str,
) -> None:
    execution = _execution(tmp_path)
    ledger = _ledger_path(execution)
    if content is None:
        ledger.unlink()
    else:
        ledger.write_text(content, encoding="utf-8")

    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(execution)

    assert captured.value.reason == reason


def test_rejects_invalid_ledger_missing_files_and_receipt_drift(tmp_path: Path) -> None:
    invalid = _execution(tmp_path / "invalid")
    ledger = _ledger_path(invalid)
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["models"][0]["physical"]["mesh_bbox_m"] = [0.0, 0.0, 0.0]
    ledger.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(invalid)
    assert captured.value.reason == "ledger_invalid"

    no_files = _execution(tmp_path / "no-files")
    ledger = _ledger_path(no_files)
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    for representation in payload["models"][0]["representations"]:
        representation["files"] = []
    ledger.write_text(json.dumps(payload), encoding="utf-8")
    no_files = _rewrite_ledger_receipts(no_files, tmp_path / "no-files-receipts")
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(no_files)
    assert captured.value.reason == "ledger_invalid"

    mismatch = _execution(tmp_path / "mismatch")
    ledger = _ledger_path(mismatch)
    ledger.write_text(ledger.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(mismatch)
    assert captured.value.reason == "ledger_receipt_mismatch"


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("no_files", "package_files_missing"),
        ("unresolved", "package_artifact_unresolved"),
        ("binding", "package_binding"),
    ],
)
def test_rejects_unbound_package_evidence(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    execution = _execution(tmp_path)

    def mutate(manifest):
        if attack == "no_files":
            manifest["files"] = []
        elif attack == "unresolved":
            manifest["files"][0]["sha256"] = "f" * 64
        else:
            manifest["resolved_scene_sha256"] = "f" * 64

    attacked = _replace_artifact(
        execution,
        tmp_path,
        ordinal=1,
        schema_version="robotwin.generated_scene_package.v1",
        mutate=mutate,
    )
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(attacked)

    assert captured.value.reason == reason


def test_rejects_static_validation_that_claims_physical_completion(tmp_path: Path) -> None:
    execution = _execution(tmp_path)

    def mutate(validation):
        validation["status"] = "pass"
        validation["not_run_count"] = 0

    attacked = _replace_artifact(
        execution,
        tmp_path,
        ordinal=1,
        schema_version="robotwin.scene_validation.v1",
        mutate=mutate,
    )
    with pytest.raises(CompileQualificationError) as captured:
        module._acceptance_checks(attacked)

    assert captured.value.reason == "static_validation_boundary"


def test_atomic_writer_cleans_staging_when_publish_fails(tmp_path: Path, monkeypatch) -> None:
    bundle = tmp_path / "bundle"

    def fail_replace(source, destination):
        raise OSError("injected rename failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="rename failure"):
        module._write_bundle_atomically(
            bundle,
            {
                "manifest.json": b"{}\n",
                "qualification.json": b"{}\n",
                "report.json": b"{}\n",
            },
        )

    assert not bundle.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_writer_never_overwrites_and_removes_uncertain_directory_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    documents = {
        "manifest.json": b"{}\n",
        "qualification.json": b"{}\n",
        "report.json": b"{}\n",
    }
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "owned-by-user"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        module._write_bundle_atomically(existing, documents)
    assert marker.read_text(encoding="utf-8") == "keep"

    bundle = tmp_path / "bundle"
    real_fsync = module.os.fsync
    calls = 0

    def fail_directory_sync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_directory_sync)
    with pytest.raises(OSError, match="directory sync failed"):
        module._write_bundle_atomically(bundle, documents)

    assert not bundle.exists()
    assert not any(path.name.startswith(".bundle.") for path in tmp_path.iterdir())


def test_sequence_clock_and_violation_projection_are_deterministic() -> None:
    clock = module._SequenceClock(1)
    assert clock() == datetime(2026, 8, 31, 1, tzinfo=timezone.utc)
    assert clock() > datetime(2026, 8, 31, 1, tzinfo=timezone.utc)

    class Violation:
        path = "models[0]"
        code = "example"

    assert (
        module._violation_values([Violation()] * 10)
        == [{"path": "models[0]", "code": "example"}] * 8
    )

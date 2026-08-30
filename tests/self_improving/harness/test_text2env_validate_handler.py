from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

import self_improving.harness.handlers.text2env_validate as validate_module
from scene_gen.builder import build_scene_package
from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.handlers.text2env_validate import (
    RequirePromotionEvidence,
    Text2EnvValidateHandler,
    text2env_validate_descriptor,
)
from self_improving.harness.package_store import PackageStore
from self_improving.harness.registry import SkillBlocked
from self_improving.harness.schemas import (
    ArtifactRef,
    Blocker,
    EnvironmentPackage,
    Text2EnvValidateInput,
    Text2EnvValidateOutput,
    ValidationStatus,
)

ROOT = Path(__file__).resolve().parents[3]
CATALOG_FIXTURE = ROOT / "tests/fixtures/asset_catalog.json"
RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")


class RecordingContext:
    run_id = RUN_ID
    attempt = 1

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[ArtifactRef, ...]]] = []

    def emit(
        self,
        stage: str,
        *,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> None:
        self.events.append((stage, artifact_refs))


@dataclass
class RecordingEligibilityVerifier:
    blockers: tuple[Blocker, ...]

    def __post_init__(self) -> None:
        self.calls: list[tuple[EnvironmentPackage, dict, dict]] = []

    def verify(
        self,
        *,
        environment_package: EnvironmentPackage,
        runtime_evidence: dict,
        validation_report: dict,
    ) -> tuple[Blocker, ...]:
        self.calls.append((environment_package, runtime_evidence, validation_report))
        return self.blockers


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _fixture_input(tmp_path: Path) -> tuple[LocalArtifactStore, Text2EnvValidateInput]:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog = load_catalog(CATALOG_FIXTURE)
    scene_spec = parse_rule_based("put a can on the plate", seed=7)
    resolved = solve_scene(scene_spec, catalog)
    package_root = tmp_path / "compiled"
    build_scene_package(scene_spec, resolved, package_root)
    published = PackageStore(store).publish(package_root)

    canonical_catalog = tmp_path / "catalog.canonical.json"
    _write_json(canonical_catalog, catalog.canonical_dict())
    catalog_ref = store.put_file(
        canonical_catalog,
        name="asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    assert catalog_ref.sha256 == catalog.digest()
    environment_package = EnvironmentPackage(
        package_id=resolved.digest(),
        route_id="text2env",
        producer_skill_ref="text2env.compile@1.0.0",
        seed=7,
        scene_spec_sha256=scene_spec.digest(),
        resolved_scene_sha256=resolved.digest(),
        asset_catalog=catalog_ref,
        package_manifest=published.manifest,
    )
    evidence_path = tmp_path / "runtime_evidence.json"
    _write_json(
        evidence_path,
        {
            "schema_version": "robotwin.scene_runtime_evidence.v2",
            "scene_id": resolved.scene_id,
            "resolved_scene_sha256": resolved.digest(),
            "status": "pass",
        },
    )
    evidence_ref = store.put_file(
        evidence_path,
        name="runtime_evidence",
        media_type="application/json",
        schema_version="robotwin.scene_runtime_evidence.v2",
    )
    return store, Text2EnvValidateInput(
        environment_package=environment_package,
        runtime_evidence=evidence_ref,
    )


def _report(value: Text2EnvValidateInput, status: str) -> dict:
    failed = status == "fail"
    incomplete = status == "incomplete"
    return {
        "schema_version": "robotwin.scene_validation.v1",
        "scene_id": "will-be-replaced",
        "resolved_scene_sha256": value.environment_package.resolved_scene_sha256,
        "status": status,
        "fail_count": int(failed),
        "not_run_count": int(incomplete),
        "checks": [
            {
                "name": "physical_gate",
                "status": "fail" if failed else "not_run" if incomplete else "pass",
                "evidence": {"observed": 1},
            }
        ],
    }


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_code"),
    [
        ("fail", ValidationStatus.FAIL, "T2E_VALIDATION_FAILED"),
        ("incomplete", ValidationStatus.INCOMPLETE, "T2E_VALIDATION_INCOMPLETE"),
    ],
)
def test_physical_failures_are_typed_succeeded_outputs_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_status: ValidationStatus,
    expected_code: str,
) -> None:
    store, value = _fixture_input(tmp_path)
    verifier = RecordingEligibilityVerifier(())
    observed: dict[str, object] = {}

    def fake_validate(resolved, **kwargs):
        observed["resolved"] = resolved
        observed.update(kwargs)
        report = _report(value, status)
        report["scene_id"] = resolved.scene_id
        return report

    monkeypatch.setattr(validate_module, "validate_resolved_scene", fake_validate)
    context = RecordingContext()
    handler = Text2EnvValidateHandler(
        artifact_store=store,
        package_store=PackageStore(store),
        work_root=tmp_path / "validate-work",
        eligibility_verifier=verifier,
    )

    result = handler(value, context)
    output = Text2EnvValidateOutput.model_validate(result.output)

    assert output.validation_status == expected_status
    assert output.publishable is False
    assert [blocker.code for blocker in output.blockers] == [expected_code]
    assert output.blockers[0].details["checks"] == ["physical_gate"]
    assert output.blockers[0].artifact_refs == (output.validation_report,)
    assert result.artifacts == (output.validation_report,)
    assert verifier.calls == []
    assert observed["require_runtime"] is True
    assert observed["catalog"] is not None
    assert Path(observed["package_root"]).name == "package"
    assert observed["runtime_evidence"]["status"] == "pass"
    assert [stage for stage, _ in context.events] == [
        "validate.package_materialized",
        "validate.gates.completed",
    ]
    assert context.events[-1][1] == (output.validation_report,)


def test_physical_pass_stays_nonpublishable_without_promotion_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, value = _fixture_input(tmp_path)

    def fake_validate(resolved, **kwargs):
        report = _report(value, "pass")
        report["scene_id"] = resolved.scene_id
        return report

    monkeypatch.setattr(validate_module, "validate_resolved_scene", fake_validate)
    handler = Text2EnvValidateHandler(
        artifact_store=store,
        package_store=PackageStore(store),
        work_root=tmp_path / "work",
        eligibility_verifier=RequirePromotionEvidence(),
    )

    output = Text2EnvValidateOutput.model_validate(handler(value, RecordingContext()).output)

    assert output.validation_status == ValidationStatus.PASS
    assert output.publishable is False
    assert [blocker.code for blocker in output.blockers] == ["T2E_VALIDATION_INCOMPLETE"]
    assert output.blockers[0].stage == "promotion_evidence"
    assert output.blockers[0].details["missing"] == [
        "compile_run_receipt",
        "replay_run_receipt",
        "compile_qualification",
        "replay_qualification",
        "validate_qualification",
        "request_provenance",
    ]
    assert output.blockers[0].artifact_refs == (output.validation_report,)


def test_complete_eligibility_can_mark_a_physical_pass_publishable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, value = _fixture_input(tmp_path)
    verifier = RecordingEligibilityVerifier(())

    def fake_validate(resolved, **kwargs):
        report = _report(value, "pass")
        report["scene_id"] = resolved.scene_id
        return report

    monkeypatch.setattr(validate_module, "validate_resolved_scene", fake_validate)
    handler = Text2EnvValidateHandler(
        artifact_store=store,
        package_store=PackageStore(store),
        work_root=tmp_path / "work",
        eligibility_verifier=verifier,
    )

    output = Text2EnvValidateOutput.model_validate(handler(value, RecordingContext()).output)

    assert output.validation_status == ValidationStatus.PASS
    assert output.publishable is True
    assert output.blockers == ()
    assert len(verifier.calls) == 1
    package, evidence, report = verifier.calls[0]
    assert package == value.environment_package
    assert evidence["resolved_scene_sha256"] == package.resolved_scene_sha256
    assert report["status"] == "pass"
    assert report["harness_binding"] == {
        "environment_package_id": package.package_id,
        "package_manifest_sha256": package.package_manifest.sha256,
        "asset_catalog_sha256": package.asset_catalog.sha256,
        "runtime_evidence_sha256": value.runtime_evidence.sha256,
        "gate_profile": "robotwin.scene_validation.v1",
    }
    persisted = json.loads(store.resolve(output.validation_report).path.read_text(encoding="utf-8"))
    assert persisted["harness_binding"] == report["harness_binding"]


def test_eligibility_blockers_are_preserved_and_bound_to_the_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, value = _fixture_input(tmp_path)
    blocker = Blocker(
        code="T2E_REGRESSION_FAILED",
        message="replay qualification is missing",
        stage="promotion_evidence",
        retryable=False,
        details={"skill_ref": "text2env.replay@1.0.0"},
        unknowns=(),
        artifact_refs=(),
    )
    verifier = RecordingEligibilityVerifier((blocker,))

    def fake_validate(resolved, **kwargs):
        report = _report(value, "pass")
        report["scene_id"] = resolved.scene_id
        return report

    monkeypatch.setattr(validate_module, "validate_resolved_scene", fake_validate)
    handler = Text2EnvValidateHandler(
        artifact_store=store,
        package_store=PackageStore(store),
        work_root=tmp_path / "work",
        eligibility_verifier=verifier,
    )

    output = Text2EnvValidateOutput.model_validate(handler(value, RecordingContext()).output)

    assert output.publishable is False
    assert output.blockers[0].code == "T2E_REGRESSION_FAILED"
    assert output.blockers[0].artifact_refs == (output.validation_report,)


@pytest.mark.parametrize(
    ("mutation", "stage"),
    [
        ("scene_digest", "package_binding"),
        ("catalog_digest", "package_binding"),
        ("manifest_seed", "package_binding"),
        ("malformed_evidence", "runtime_evidence"),
        ("wrong_evidence_schema", "runtime_evidence"),
        ("evidence_array", "runtime_evidence"),
    ],
)
def test_invalid_package_or_evidence_blocks_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    stage: str,
) -> None:
    store, value = _fixture_input(tmp_path)
    if mutation == "scene_digest":
        value = value.model_copy(
            update={
                "environment_package": value.environment_package.model_copy(
                    update={"scene_spec_sha256": "f" * 64}
                )
            }
        )
    elif mutation == "catalog_digest":
        value = value.model_copy(
            update={
                "environment_package": value.environment_package.model_copy(
                    update={
                        "asset_catalog": value.environment_package.asset_catalog.model_copy(
                            update={"sha256": "e" * 64, "uri": "artifact://sha256/" + "e" * 64}
                        )
                    }
                )
            }
        )
    elif mutation == "manifest_seed":
        manifest = store.resolve(value.environment_package.package_manifest).path
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["seed"] = 8
        changed_manifest = tmp_path / "changed_manifest.json"
        _write_json(changed_manifest, payload)
        changed_ref = store.put_file(
            changed_manifest,
            name="package_manifest",
            media_type="application/json",
            schema_version="robotwin.generated_scene_package.v1",
        )
        value = value.model_copy(
            update={
                "environment_package": value.environment_package.model_copy(
                    update={"package_manifest": changed_ref}
                )
            }
        )
    else:
        evidence_path = tmp_path / f"{mutation}.json"
        if mutation == "malformed_evidence":
            evidence_path.write_text("{", encoding="utf-8")
        elif mutation == "wrong_evidence_schema":
            _write_json(
                evidence_path,
                {
                    "schema_version": "robotwin.scene_runtime_evidence.v1",
                    "scene_id": "wrong-schema",
                },
            )
        else:
            _write_json(evidence_path, [])
        changed_evidence = store.put_file(
            evidence_path,
            name="runtime_evidence",
            media_type="application/json",
            schema_version="robotwin.scene_runtime_evidence.v2",
        )
        value = value.model_copy(update={"runtime_evidence": changed_evidence})

    monkeypatch.setattr(
        validate_module,
        "validate_resolved_scene",
        lambda *args, **kwargs: pytest.fail("invalid inputs must not reach validation"),
    )
    handler = Text2EnvValidateHandler(
        artifact_store=store,
        package_store=PackageStore(store),
        work_root=tmp_path / "work",
        eligibility_verifier=RequirePromotionEvidence(),
    )

    with pytest.raises(SkillBlocked) as captured:
        handler(value, RecordingContext())

    assert captured.value.blocker.code == "T2E_PACKAGE_INVALID"
    assert captured.value.blocker.stage == stage
    assert captured.value.blocker.retryable is False


@pytest.mark.parametrize(
    "mutation",
    [
        "non_object",
        "wrong_schema",
        "wrong_scene",
        "wrong_digest",
        "checks_not_list",
        "checks_empty",
        "check_not_object",
        "check_name_not_string",
        "check_status_invalid",
        "fail_count_bool",
        "not_run_count_bool",
        "fail_count_mismatch",
        "not_run_count_mismatch",
        "status_mismatch",
    ],
)
def test_validator_defects_raise_internal_errors_instead_of_typed_gate_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    store, value = _fixture_input(tmp_path)

    def broken_validator(resolved, **kwargs):
        report = _report(value, "pass")
        report["scene_id"] = resolved.scene_id
        if mutation == "non_object":
            return []
        if mutation == "wrong_schema":
            report["schema_version"] = "robotwin.scene_validation.v2"
        elif mutation == "wrong_scene":
            report["scene_id"] = "another-scene"
        elif mutation == "wrong_digest":
            report["resolved_scene_sha256"] = "f" * 64
        elif mutation == "checks_not_list":
            report["checks"] = {}
        elif mutation == "checks_empty":
            report["checks"] = []
        elif mutation == "check_not_object":
            report["checks"] = ["bad"]
        elif mutation == "check_name_not_string":
            report["checks"][0]["name"] = 7
        elif mutation == "check_status_invalid":
            report["checks"][0]["status"] = "skipped"
        elif mutation == "fail_count_bool":
            report["fail_count"] = False
        elif mutation == "not_run_count_bool":
            report["not_run_count"] = False
        elif mutation == "fail_count_mismatch":
            report["fail_count"] = 1
        elif mutation == "not_run_count_mismatch":
            report["not_run_count"] = 1
        else:
            report["status"] = "fail"
        return report

    monkeypatch.setattr(validate_module, "validate_resolved_scene", broken_validator)
    handler = Text2EnvValidateHandler(
        artifact_store=store,
        package_store=PackageStore(store),
        work_root=tmp_path / "work",
        eligibility_verifier=RequirePromotionEvidence(),
    )

    with pytest.raises(RuntimeError, match="validator"):
        handler(value, RecordingContext())


def test_attempt_directory_is_never_reused_or_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, value = _fixture_input(tmp_path)

    def fake_validate(resolved, **kwargs):
        report = _report(value, "pass")
        report["scene_id"] = resolved.scene_id
        return report

    monkeypatch.setattr(validate_module, "validate_resolved_scene", fake_validate)
    handler = Text2EnvValidateHandler(
        artifact_store=store,
        package_store=PackageStore(store),
        work_root=tmp_path / "work",
        eligibility_verifier=RequirePromotionEvidence(),
    )
    context = RecordingContext()
    handler(value, context)

    with pytest.raises(SkillBlocked) as captured:
        handler(value, context)

    assert captured.value.blocker.code == "T2E_PACKAGE_INVALID"
    assert captured.value.blocker.stage == "package_materialization"


def test_descriptor_is_exact_and_nonretrying() -> None:
    qualification = ArtifactRef(
        name="qualification",
        uri="artifact://sha256/" + "a" * 64,
        media_type="application/json",
        sha256="a" * 64,
        bytes=10,
        schema_version="harness.skill_qualification.v1",
    )

    descriptor = text2env_validate_descriptor(
        qualification_artifact=qualification,
        implementation_sha256="b" * 64,
    )

    assert descriptor.skill_id == "text2env.validate"
    assert descriptor.version == "1.0.0"
    assert descriptor.max_attempts == 1
    assert descriptor.input_schema == "harness.text2env_validate_input.v1"
    assert descriptor.output_schema == "harness.text2env_validate_output.v1"

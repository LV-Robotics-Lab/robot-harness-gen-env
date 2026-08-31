"""Deterministic validation Adapter over a package and captured runtime evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.schema import ResolvedSceneSpec, SceneSpec
from scene_gen.validator import validate_resolved_scene

from ..artifacts import ArtifactResolutionError, LocalArtifactStore
from ..package_store import PackageStore, PackageStoreError
from ..registry import HandlerResult, RunContext, SkillBlocked
from ..schemas import (
    ArtifactRef,
    Blocker,
    EnvironmentPackage,
    SkillDescriptor,
    Text2EnvValidateInput,
    Text2EnvValidateOutput,
    ValidationStatus,
)

_RUNTIME_EVIDENCE_SCHEMA = "robotwin.scene_runtime_evidence.v2"
_VALIDATION_REPORT_SCHEMA = "robotwin.scene_validation.v1"


class EligibilityVerifier(Protocol):
    """Contribute promotion blockers after physical gates pass.

    The v1 input cannot bind the receipts needed to authorize publication, so
    an empty policy result is diagnostic only and never grants eligibility.
    """

    def verify(
        self,
        *,
        environment_package: EnvironmentPackage,
        runtime_evidence: dict[str, Any],
        validation_report: dict[str, Any],
    ) -> tuple[Blocker, ...]: ...


@dataclass(frozen=True)
class RequirePromotionEvidence:
    """Fail-closed default until compile/replay receipts are supplied to validation."""

    def verify(
        self,
        *,
        environment_package: EnvironmentPackage,
        runtime_evidence: dict[str, Any],
        validation_report: dict[str, Any],
    ) -> tuple[Blocker, ...]:
        del environment_package, runtime_evidence, validation_report
        return (
            Blocker(
                code="T2E_VALIDATION_INCOMPLETE",
                message="physical gates passed but promotion evidence is incomplete",
                stage="promotion_evidence",
                retryable=False,
                details={
                    "reason": "validate_v1_cannot_bind_promotion_evidence",
                    "missing": [
                        "compile_run_receipt",
                        "replay_run_receipt",
                        "compile_qualification",
                        "replay_qualification",
                        "validate_qualification",
                        "request_provenance",
                    ],
                },
                unknowns=(),
                artifact_refs=(),
            ),
        )


@dataclass(frozen=True)
class _ValidationInputs:
    package_root: Path
    scene_spec: SceneSpec
    resolved_scene: ResolvedSceneSpec
    catalog: AssetCatalog
    runtime_evidence: dict[str, Any]


@dataclass(frozen=True)
class Text2EnvValidateHandler:
    """Recompute a non-publishable v1 validation report without a simulator."""

    artifact_store: LocalArtifactStore
    package_store: PackageStore
    work_root: Path
    eligibility_verifier: EligibilityVerifier

    def __call__(
        self,
        value: Text2EnvValidateInput,
        context: RunContext,
    ) -> HandlerResult:
        inputs = self._materialize_and_bind(value, context)
        report = validate_resolved_scene(
            inputs.resolved_scene,
            catalog=inputs.catalog,
            package_root=inputs.package_root,
            runtime_evidence=inputs.runtime_evidence,
            require_runtime=True,
        )
        _verify_validation_report(report, inputs.resolved_scene)
        report["harness_binding"] = {
            "environment_package_id": value.environment_package.package_id,
            "package_manifest_sha256": value.environment_package.package_manifest.sha256,
            "asset_catalog_sha256": value.environment_package.asset_catalog.sha256,
            "runtime_evidence_sha256": value.runtime_evidence.sha256,
            "gate_profile": value.gate_profile,
        }
        report_path = inputs.package_root.parent / "validation_report.json"
        report_path.write_text(
            json.dumps(
                report,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        report_ref = self.artifact_store.put_file(
            report_path,
            name="validation_report",
            media_type="application/json",
            schema_version=_VALIDATION_REPORT_SCHEMA,
        )
        context.emit("validate.gates.completed", artifact_refs=(report_ref,))

        status = ValidationStatus(report["status"])
        if status == ValidationStatus.PASS:
            policy_blockers = self.eligibility_verifier.verify(
                environment_package=value.environment_package,
                runtime_evidence=inputs.runtime_evidence,
                validation_report=report,
            )
            blockers = tuple(policy_blockers)
            if not any(
                blocker.details.get("reason") == "validate_v1_cannot_bind_promotion_evidence"
                for blocker in blockers
            ):
                blockers = (
                    *blockers,
                    *RequirePromotionEvidence().verify(
                        environment_package=value.environment_package,
                        runtime_evidence=inputs.runtime_evidence,
                        validation_report=report,
                    ),
                )
        else:
            blockers = (_gate_blocker(report, report_ref),)
        bound_blockers = tuple(_bind_report(blocker, report_ref) for blocker in blockers)
        output = Text2EnvValidateOutput(
            validation_report=report_ref,
            validation_status=status,
            publishable=status == ValidationStatus.PASS and not bound_blockers,
            blockers=bound_blockers,
        )
        return HandlerResult(output=output, artifacts=(report_ref,))

    def _materialize_and_bind(
        self,
        value: Text2EnvValidateInput,
        context: RunContext,
    ) -> _ValidationInputs:
        attempt_root = (
            self.work_root.expanduser().resolve()
            / str(context.run_id)
            / f"attempt-{context.attempt}"
        )
        try:
            package_root = self.package_store.materialize(
                value.environment_package.package_manifest,
                attempt_root / "package",
            )
        except PackageStoreError as error:
            raise _package_blocker(
                stage="package_materialization",
                message=str(error),
                details={"reason": error.reason},
            ) from error
        context.emit(
            "validate.package_materialized",
            artifact_refs=(value.environment_package.package_manifest,),
        )
        try:
            scene_spec = SceneSpec.model_validate_json(
                (package_root / "scene_spec.json").read_text(encoding="utf-8")
            )
            resolved_scene = ResolvedSceneSpec.model_validate_json(
                (package_root / "resolved_scene.json").read_text(encoding="utf-8")
            )
            manifest = _load_json(
                package_root / "package_manifest.json",
                label="package manifest",
            )
            catalog_path = self.artifact_store.resolve(value.environment_package.asset_catalog).path
            catalog = load_catalog(catalog_path)
            problems = _binding_problems(
                value.environment_package,
                scene_spec=scene_spec,
                resolved_scene=resolved_scene,
                catalog=catalog,
                manifest=manifest,
            )
        except (
            ArtifactResolutionError,
            OSError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ) as error:
            raise _package_blocker(
                stage="package_binding",
                message="package content could not be reconstructed",
                details={"error_type": type(error).__name__, "error": str(error)},
            ) from error
        if problems:
            raise _package_blocker(
                stage="package_binding",
                message="environment package failed canonical digest binding",
                details={"problems": problems},
            )
        runtime_evidence = self._load_runtime_evidence(value.runtime_evidence)
        return _ValidationInputs(
            package_root=package_root,
            scene_spec=scene_spec,
            resolved_scene=resolved_scene,
            catalog=catalog,
            runtime_evidence=runtime_evidence,
        )

    def _load_runtime_evidence(self, artifact: ArtifactRef) -> dict[str, Any]:
        try:
            path = self.artifact_store.resolve(artifact).path
            value = _load_json(path, label="runtime evidence")
        except (ArtifactResolutionError, OSError, ValueError, json.JSONDecodeError) as error:
            raise _package_blocker(
                stage="runtime_evidence",
                message="runtime evidence could not be reconstructed",
                details={"error_type": type(error).__name__, "error": str(error)},
            ) from error
        if value.get("schema_version") != _RUNTIME_EVIDENCE_SCHEMA:
            raise _package_blocker(
                stage="runtime_evidence",
                message="runtime evidence schema is unsupported",
                details={"schema_version": value.get("schema_version")},
            )
        return value


def text2env_validate_descriptor(
    *,
    qualification_artifact: ArtifactRef,
    implementation_sha256: str,
) -> SkillDescriptor:
    """Build the descriptor for deterministic, non-promotion v1 validation."""

    return SkillDescriptor(
        skill_id="text2env.validate",
        version="1.0.0",
        mcp_tool_name="text2env_validate_v1_0_0",
        input_schema="harness.text2env_validate_input.v1",
        output_schema="harness.text2env_validate_output.v1",
        implementation_name="self_improving.harness.handlers.text2env_validate",
        implementation_version="1",
        implementation_sha256=implementation_sha256,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification_artifact,
    )


def _binding_problems(
    package: EnvironmentPackage,
    *,
    scene_spec: SceneSpec,
    resolved_scene: ResolvedSceneSpec,
    catalog: AssetCatalog,
    manifest: dict[str, Any],
) -> list[str]:
    problems: list[str] = []
    expected = {
        "environment_package.scene_spec_sha256": (
            package.scene_spec_sha256,
            scene_spec.digest(),
        ),
        "environment_package.resolved_scene_sha256": (
            package.resolved_scene_sha256,
            resolved_scene.digest(),
        ),
        "environment_package.package_id": (
            package.package_id,
            resolved_scene.digest(),
        ),
        "environment_package.asset_catalog.sha256": (
            package.asset_catalog.sha256,
            catalog.digest(),
        ),
        "resolved_scene.source_scene_spec_sha256": (
            resolved_scene.source_scene_spec_sha256,
            scene_spec.digest(),
        ),
        "resolved_scene.asset_catalog_sha256": (
            resolved_scene.asset_catalog_sha256,
            catalog.digest(),
        ),
        "manifest.source_scene_spec_sha256": (
            manifest.get("source_scene_spec_sha256"),
            scene_spec.digest(),
        ),
        "manifest.resolved_scene_sha256": (
            manifest.get("resolved_scene_sha256"),
            resolved_scene.digest(),
        ),
        "manifest.asset_catalog_sha256": (
            manifest.get("asset_catalog_sha256"),
            catalog.digest(),
        ),
        "environment_package.seed": (package.seed, scene_spec.seed),
        "resolved_scene.seed": (resolved_scene.seed, scene_spec.seed),
        "manifest.seed": (manifest.get("seed"), scene_spec.seed),
        "resolved_scene.scene_id": (resolved_scene.scene_id, scene_spec.scene_id),
        "manifest.scene_id": (manifest.get("scene_id"), scene_spec.scene_id),
    }
    for name, (actual, wanted) in expected.items():
        if actual != wanted:
            problems.append(name)
    return problems


def _verify_validation_report(
    report: dict[str, Any],
    resolved_scene: ResolvedSceneSpec,
) -> None:
    if not isinstance(report, dict):
        raise RuntimeError("validator returned a non-object report")
    if (
        report.get("schema_version") != _VALIDATION_REPORT_SCHEMA
        or report.get("scene_id") != resolved_scene.scene_id
        or report.get("resolved_scene_sha256") != resolved_scene.digest()
    ):
        raise RuntimeError("validator report identity is inconsistent")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise RuntimeError("validator report checks must be a nonempty list")
    statuses: list[str] = []
    for check in checks:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("name"), str)
            or check.get("status") not in {"pass", "fail", "not_run", "not_applicable"}
        ):
            raise RuntimeError("validator report contains an invalid check")
        statuses.append(check["status"])
    fail_count = report.get("fail_count")
    not_run_count = report.get("not_run_count")
    if (
        type(fail_count) is not int
        or type(not_run_count) is not int
        or fail_count != statuses.count("fail")
        or not_run_count != statuses.count("not_run")
    ):
        raise RuntimeError("validator report counts are inconsistent")
    expected_status = "fail" if fail_count else "incomplete" if not_run_count else "pass"
    if report.get("status") != expected_status:
        raise RuntimeError("validator report status is inconsistent")


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _gate_blocker(report: dict[str, Any], report_ref: ArtifactRef) -> Blocker:
    status = report["status"]
    check_status = "fail" if status == "fail" else "not_run"
    names = [check["name"] for check in report["checks"] if check["status"] == check_status]
    return Blocker(
        code=("T2E_VALIDATION_FAILED" if status == "fail" else "T2E_VALIDATION_INCOMPLETE"),
        message=(
            "one or more required validation gates failed"
            if status == "fail"
            else "one or more required validation gates were not run"
        ),
        stage="validation.gates",
        retryable=False,
        details={"checks": names},
        unknowns=(),
        artifact_refs=(report_ref,),
    )


def _bind_report(blocker: Blocker, report_ref: ArtifactRef) -> Blocker:
    artifacts = list(blocker.artifact_refs)
    identity = (report_ref.media_type, report_ref.schema_version, report_ref.sha256)
    if all(
        (artifact.media_type, artifact.schema_version, artifact.sha256) != identity
        for artifact in artifacts
    ):
        artifacts.append(report_ref)
    return blocker.model_copy(update={"artifact_refs": tuple(artifacts)})


def _package_blocker(
    *,
    stage: str,
    message: str,
    details: dict[str, Any],
) -> SkillBlocked:
    return SkillBlocked(
        Blocker(
            code="T2E_PACKAGE_INVALID",
            message=message,
            stage=stage,
            retryable=False,
            details=json.loads(json.dumps(details, default=str)),
            unknowns=(),
            artifact_refs=(),
        )
    )

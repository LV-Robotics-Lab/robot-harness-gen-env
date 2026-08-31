from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import self_improving.harness.replay_qualification as replay_qualification_module
from self_improving.harness import (
    ArtifactRef,
    ExecutionReproducibility,
    LoadedReplayQualification,
    LocalArtifactStore,
    ReplayQualificationError,
    load_replay_qualification_bundle,
)
from self_improving.harness.replay_qualification import (
    REPLAY_QUALIFICATION_CASE_ID,
    REPLAY_QUALIFICATION_CHECK_NAMES,
)

SKILL_REF = "text2env.replay@1.0.0"
CASE_ID = "can-on-plate-seed-7-900-120-120-12"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
SHA_1 = "1" * 64
SHA_2 = "2" * 64
SHA_3 = "3" * 64
SHA_4 = "4" * 64
SHA_5 = "5" * 64
SHA_6 = "6" * 64
SHA_7 = "7" * 64
SHA_8 = "8" * 64
CHECK_NAMES = (
    "01.case_binding",
    "02.exact_dependencies",
    "03.event_lifecycle",
    "04.runtime_asset_snapshot",
    "05.media_decode",
    "06.physics_validation",
    "07.source_stability",
    "08.candidate_kernel_executions",
)
EVENT_KINDS = (
    "preflight.completed",
    "scene.loaded",
    "simulation.started",
    *("simulation.checkpoint",) * 7,
    "simulation.completed",
    "media.completed",
    "evidence.completed",
    "worker.completed",
)
CHECKPOINTS = (120, 240, 360, 480, 600, 720, 840)


def test_recursive_evidence_reference_discovery_rejects_artifact_shaped_supersets() -> None:
    valid = ArtifactRef.model_validate(
        _ref(SHA_A, name="evidence", schema_version="test.evidence.v1")
    )
    assert replay_qualification_module._artifact_refs_in_json(
        {"nested": [valid.model_dump(mode="json")]}
    ) == (valid,)

    attacked = {**valid.model_dump(mode="json"), "ignored_extra": True}
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._artifact_refs_in_json(attacked)

    assert captured.value.reason == "invalid_replay_evidence"


@pytest.mark.parametrize(
    "value",
    ["asset//mesh.glb", "./mesh.glb", "asset/./mesh.glb", "bad\x00name"],
)
def test_runtime_asset_paths_must_be_canonical_posix(value: str) -> None:
    assert not replay_qualification_module._safe_runtime_asset_path(value, allow_dot=False)


def _ref(
    sha256: str,
    *,
    name: str,
    schema_version: str,
    bytes_count: int = 1,
) -> dict[str, object]:
    return {
        "name": name,
        "uri": f"artifact://sha256/{sha256}",
        "media_type": "application/json",
        "sha256": sha256,
        "bytes": bytes_count,
        "schema_version": schema_version,
    }


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha(value: object) -> str:
    return _sha(_canonical_bytes(value))


def _tree_sha(root: Path) -> str:
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_file():
            payload = path.read_bytes()
            files.append(
                {
                    "path": relative.as_posix(),
                    "bytes": len(payload),
                    "sha256": _sha(payload),
                }
            )
    return _canonical_sha({"exists": True, "files": files})


def _lifecycle(transcript_sha256: str) -> dict[str, object]:
    return {
        "transcript_sha256": transcript_sha256,
        "event_count": 14,
        "event_kinds": list(EVENT_KINDS),
        "checkpoint_completed_steps": list(CHECKPOINTS),
        "simulation_completed_steps": 900,
        "transcript_complete": True,
        "live_observer_matched": True,
        "streams_complete": True,
    }


def _media(video_sha256: str, unique_frames: int) -> dict[str, object]:
    return {
        "video_sha256": video_sha256,
        "fully_decoded": True,
        "frame_count": 120,
        "source_unique_frame_count": unique_frames,
        "decoded_unique_frame_count": unique_frames,
        "fps_numerator": 12,
        "fps_denominator": 1,
        "width": 320,
        "height": 240,
        "format_name": "iso-bmff/mp4",
        "codec_name": "h264",
        "pixel_format": "8bit-420",
        "sample_aspect_ratio": "0/1",
        "square_sample_aspect_ratio_defaulted": True,
        "decoded_png_count": 7,
        "all_pngs_decoded": True,
    }


def _physics(
    evidence_sha256: str,
    report_sha256: str,
    unique_frames: int,
) -> dict[str, object]:
    return {
        "runtime_evidence_sha256": evidence_sha256,
        "validation_report_sha256": report_sha256,
        "status": "pass",
        "fail_count": 0,
        "not_run_count": 0,
        "resolved_scene_sha256": SHA_B,
        "settle_steps": 900,
        "contact_window_steps": 120,
        "video_frame_count": 120,
        "unique_video_frame_count": unique_frames,
    }


def _execution(
    *,
    mode: str,
    evidence_sha256: str,
    receipt_sha256: str,
    output_sha256: str,
    validation_report_sha256: str,
    event_transcript_sha256: str,
    video_sha256: str,
    dependencies: list[dict[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {
        "mode": mode,
        "status": "succeeded",
        "attempt_count": 1,
        "handler_call_count": 1,
        "execution_receipt_sha256": receipt_sha256,
        "output_sha256": output_sha256,
        "runtime_evidence_sha256": evidence_sha256,
        "validation_report_sha256": validation_report_sha256,
        "event_transcript_sha256": event_transcript_sha256,
        "runtime_asset_manifest_sha256": SHA_E,
        "video_sha256": video_sha256,
        "environment_package_id": SHA_B,
        "dependencies": dependencies,
        "cas_reread_verified": True,
        "all_artifacts_verified": True,
        "run_id": (
            "12345678-1234-4234-9234-123456789abc"
            if mode == "candidate_direct"
            else "22345678-1234-4234-9234-123456789abc"
        ),
        "invocation_digest": SHA_A,
    }
    if mode == "candidate_direct":
        value["supervisor_receipt"] = _ref(
            SHA_6,
            name="direct_supervisor_receipt",
            schema_version="harness.replay_qualification.direct_supervisor_receipt.v1",
        )
    else:
        value["evaluation_receipt"] = _ref(
            SHA_7,
            name="kernel_evaluation_receipt",
            schema_version="harness.replay_qualification.kernel_evaluation_receipt.v1",
        )
    return value


def _valid_checks(
    *,
    implementation_sha256: str,
    scene_gen_sha256: str,
    ledger_sha256: str,
) -> list[dict[str, object]]:
    dependencies = [
        {"name": "text2env.replay.capability", "version": "1", "sha256": SHA_A},
        {"name": "text2env.replay.executor", "version": "1", "sha256": SHA_C},
        {"name": "text2env.replay.handler_config", "version": "1", "sha256": SHA_D},
        {"name": "text2env.replay.media_verifier", "version": "1", "sha256": SHA_F},
        {"name": "text2env.replay.runtime_assets", "version": "1", "sha256": SHA_E},
    ]
    return [
        {
            "name": CHECK_NAMES[0],
            "status": "pass",
            "evidence": {
                "schema_version": "harness.replay_qualification.case_binding.v1",
                "case_id": CASE_ID,
                "skill_ref": SKILL_REF,
                "request": "Place a can on top of a plate.",
                "seed": 7,
                "runtime_config": {
                    "precheck_steps": 0,
                    "settle_steps": 900,
                    "contact_window_steps": 120,
                    "video_frames": 120,
                    "fps": 12,
                },
                "scene_spec_sha256": SHA_A,
                "resolved_scene_sha256": SHA_B,
                "environment_package_id": SHA_B,
                "asset_catalog_sha256": SHA_C,
                "package_manifest_sha256": SHA_D,
                "operator_before_manifest": _ref(
                    SHA_1,
                    name="operator_before",
                    schema_version="harness.replay_qualification.operator_inputs.v1",
                ),
                "operator_after_manifest": _ref(
                    SHA_2,
                    name="operator_after",
                    schema_version="harness.replay_qualification.operator_inputs.v1",
                ),
            },
        },
        {
            "name": CHECK_NAMES[1],
            "status": "pass",
            "evidence": {
                "schema_version": "harness.replay_qualification.exact_dependencies.v1",
                "dependencies": dependencies,
            },
        },
        {
            "name": CHECK_NAMES[2],
            "status": "pass",
            "evidence": {
                "schema_version": "harness.replay_qualification.event_lifecycle.v1",
                "checkpoint_steps": 120,
                "candidate_direct": _lifecycle(SHA_1),
                "production_kernel_candidate": _lifecycle(SHA_2),
            },
        },
        {
            "name": CHECK_NAMES[3],
            "status": "pass",
            "evidence": {
                "schema_version": "harness.replay_qualification.runtime_asset_snapshot.v1",
                "manifest_sha256": SHA_E,
                "member_count": 4,
                "all_members_verified": True,
                "self_contained": True,
                "candidate_direct_manifest_sha256": SHA_E,
                "production_kernel_candidate_manifest_sha256": SHA_E,
            },
        },
        {
            "name": CHECK_NAMES[4],
            "status": "pass",
            "evidence": {
                "schema_version": "harness.replay_qualification.media_decode.v1",
                "verifier_identity_sha256": SHA_F,
                "ffmpeg_sha256": SHA_3,
                "launcher_sha256": SHA_4,
                "candidate_direct": _media(SHA_5, 114),
                "production_kernel_candidate": _media(SHA_6, 113),
            },
        },
        {
            "name": CHECK_NAMES[5],
            "status": "pass",
            "evidence": {
                "schema_version": "harness.replay_qualification.physics_validation.v1",
                "candidate_direct": _physics(SHA_7, SHA_1, 114),
                "production_kernel_candidate": _physics(SHA_8, SHA_2, 113),
            },
        },
        {
            "name": CHECK_NAMES[6],
            "status": "pass",
            "evidence": {
                "schema_version": "harness.replay_qualification.source_stability.v1",
                "changed_during_qualification": False,
                "implementation_sha256": implementation_sha256,
                "implementation_file_count": 1,
                "scene_gen_tree_sha256": scene_gen_sha256,
                "ledger_contract_tree_sha256": ledger_sha256,
                "harness_tree_sha256": SHA_A,
                "before_manifest": _ref(
                    SHA_3,
                    name="source_before",
                    schema_version=("harness.replay_qualification.harness_source_manifest.v1"),
                ),
                "after_execution_manifest": _ref(
                    SHA_4,
                    name="source_after_execution",
                    schema_version=("harness.replay_qualification.harness_source_manifest.v1"),
                ),
                "before_publish_manifest": _ref(
                    SHA_5,
                    name="source_before_publish",
                    schema_version=("harness.replay_qualification.harness_source_manifest.v1"),
                ),
            },
        },
        {
            "name": CHECK_NAMES[7],
            "status": "pass",
            "evidence": {
                "schema_version": ("harness.replay_qualification.candidate_kernel_executions.v1"),
                "candidate_direct": _execution(
                    mode="candidate_direct",
                    evidence_sha256=SHA_7,
                    receipt_sha256=SHA_3,
                    output_sha256=SHA_5,
                    validation_report_sha256=SHA_1,
                    event_transcript_sha256=SHA_1,
                    video_sha256=SHA_5,
                    dependencies=dependencies,
                ),
                "production_kernel_candidate": {
                    **_execution(
                        mode="qualification_candidate",
                        evidence_sha256=SHA_8,
                        receipt_sha256=SHA_4,
                        output_sha256=SHA_6,
                        validation_report_sha256=SHA_2,
                        event_transcript_sha256=SHA_2,
                        video_sha256=SHA_6,
                        dependencies=dependencies,
                    ),
                    "run_id": "12345678-1234-4234-9234-123456789abc",
                    "invocation_digest": SHA_A,
                },
                "evidence_closure_manifest": _ref(
                    SHA_8,
                    name="evidence_closure",
                    schema_version="harness.replay_qualification.evidence_closure.v1",
                ),
            },
        },
    ]


def _write_bundle(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    implementation = tmp_path / "implementation"
    implementation.mkdir()
    implementation_file = implementation / "handler.py"
    implementation_file.write_text("HANDLER = 1\n", encoding="utf-8")
    scene_gen = tmp_path / "scene_gen"
    scene_gen.mkdir()
    (scene_gen / "solver.py").write_text("SOLVER = 1\n", encoding="utf-8")
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "contract.py").write_text("LEDGER = 3\n", encoding="utf-8")

    implementation_payload = implementation_file.read_bytes()
    scene_gen_sha256 = _tree_sha(scene_gen)
    ledger_sha256 = _tree_sha(ledger)
    manifest = {
        "schema_version": "harness.skill_implementation_manifest.v1",
        "skill_ref": SKILL_REF,
        "files": [
            {
                "path": "handler.py",
                "bytes": len(implementation_payload),
                "sha256": _sha(implementation_payload),
            }
        ],
        "bundle_sha256": "0" * 64,
        "scene_gen_tree_sha256": scene_gen_sha256,
        "ledger_contract_tree_sha256": ledger_sha256,
    }
    implementation_sha256 = _canonical_sha(
        {
            "schema_version": manifest["schema_version"],
            "skill_ref": manifest["skill_ref"],
            "files": manifest["files"],
            "scene_gen_tree_sha256": scene_gen_sha256,
            "ledger_contract_tree_sha256": ledger_sha256,
        }
    )
    manifest["bundle_sha256"] = implementation_sha256
    report = {
        "schema_version": "harness.skill_qualification_report.v1",
        "skill_ref": SKILL_REF,
        "status": "pass",
        "deterministic_case_id": CASE_ID,
        "regression_command": "pytest -q tests/self_improving/harness/test_replay_qualification.py",
        "implementation_sha256": implementation_sha256,
        "scene_gen_tree_sha256": scene_gen_sha256,
        "ledger_contract_tree_sha256": ledger_sha256,
        "checks": _valid_checks(
            implementation_sha256=implementation_sha256,
            scene_gen_sha256=scene_gen_sha256,
            ledger_sha256=ledger_sha256,
        ),
    }
    report_bytes = _canonical_bytes(report)
    qualification = {
        "skill_ref": SKILL_REF,
        "status": "pass",
        "deterministic_case_id": CASE_ID,
        "regression_command": report["regression_command"],
        "report_sha256": _sha(report_bytes),
    }
    (bundle / "report.json").write_bytes(report_bytes)
    (bundle / "qualification.json").write_bytes(_canonical_bytes(qualification))
    (bundle / "manifest.json").write_bytes(_canonical_bytes(manifest))
    return bundle, implementation, scene_gen, ledger


def _set_path(value: object, path: tuple[str | int, ...], replacement: object) -> None:
    target = value
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = replacement  # type: ignore[index]


def _rewrite_report(bundle: Path, report: dict[str, object]) -> None:
    report_bytes = _canonical_bytes(report)
    (bundle / "report.json").write_bytes(report_bytes)
    qualification = json.loads((bundle / "qualification.json").read_bytes())
    qualification["report_sha256"] = _sha(report_bytes)
    (bundle / "qualification.json").write_bytes(_canonical_bytes(qualification))


def _mutate_report(
    bundle: Path,
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    report = json.loads((bundle / "report.json").read_bytes())
    _set_path(report, path, replacement)
    _rewrite_report(bundle, report)


def _load(
    tmp_path: Path,
    bundle: Path,
    implementation: Path,
    scene_gen: Path,
    ledger: Path,
) -> LoadedReplayQualification:
    return load_replay_qualification_bundle(
        bundle,
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        implementation_root=implementation,
        scene_gen_root=scene_gen,
        ledger_contract_root=ledger,
    )


def test_claim_only_documents_cannot_load_without_complete_evidence(tmp_path: Path) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)

    with pytest.raises(ReplayQualificationError) as captured:
        load_replay_qualification_bundle(
            bundle,
            artifact_store=LocalArtifactStore(tmp_path / "cas"),
            implementation_root=implementation,
            scene_gen_root=scene_gen,
            ledger_contract_root=ledger,
        )

    assert captured.value.reason == "replay_evidence_unavailable"
    assert REPLAY_QUALIFICATION_CASE_ID == CASE_ID
    assert REPLAY_QUALIFICATION_CHECK_NAMES == CHECK_NAMES


def test_empty_cas_cannot_forge_replay_and_strict_failure_publishes_no_pass(
    tmp_path: Path,
) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)
    store = LocalArtifactStore(tmp_path / "empty-cas")

    with pytest.raises(ReplayQualificationError) as captured:
        load_replay_qualification_bundle(
            bundle,
            artifact_store=store,
            implementation_root=implementation,
            scene_gen_root=scene_gen,
            ledger_contract_root=ledger,
        )

    assert captured.value.reason == "replay_evidence_unavailable"
    assert not store.root.exists()


def test_replay_claim_failure_does_not_publish_generic_pass_documents(tmp_path: Path) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)
    _mutate_report(bundle, ("checks", 0, "evidence", "seed"), 8)
    store = LocalArtifactStore(tmp_path / "cas")

    with pytest.raises(ReplayQualificationError) as captured:
        load_replay_qualification_bundle(
            bundle,
            artifact_store=store,
            implementation_root=implementation,
            scene_gen_root=scene_gen,
            ledger_contract_root=ledger,
        )

    assert captured.value.reason == "replay_claim_mismatch"
    assert not store.root.exists()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "replay_check_set_mismatch"),
        ("extra", "replay_check_set_mismatch"),
        ("duplicate", "invalid_report"),
    ],
)
def test_rejects_missing_extra_or_duplicate_checks(
    tmp_path: Path,
    mutation: str,
    reason: str,
) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)
    report = json.loads((bundle / "report.json").read_bytes())
    checks = report["checks"]
    if mutation == "missing":
        checks.pop()  # type: ignore[union-attr]
    elif mutation == "extra":
        checks.append(  # type: ignore[union-attr]
            {"name": "09.extra", "status": "pass", "evidence": {}}
        )
    else:
        checks.insert(1, checks[0].copy())  # type: ignore[index,union-attr]
    _rewrite_report(bundle, report)

    with pytest.raises(ReplayQualificationError) as captured:
        _load(tmp_path, bundle, implementation, scene_gen, ledger)

    assert captured.value.reason == reason


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("checks", 0, "evidence", "unexpected"), "not-used"),
        (("checks", 0, "evidence", "seed"), 7.0),
        (("checks", 3, "evidence", "all_members_verified"), 1),
    ],
)
def test_rejects_extra_or_type_loose_claim_fields(
    tmp_path: Path,
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)
    _mutate_report(bundle, path, replacement)

    with pytest.raises(ReplayQualificationError) as captured:
        _load(tmp_path, bundle, implementation, scene_gen, ledger)

    assert captured.value.reason == "invalid_replay_claim"


def test_rejects_missing_claim_field(tmp_path: Path) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)
    report = json.loads((bundle / "report.json").read_bytes())
    report["checks"][4]["evidence"].pop("ffmpeg_sha256")
    _rewrite_report(bundle, report)

    with pytest.raises(ReplayQualificationError) as captured:
        _load(tmp_path, bundle, implementation, scene_gen, ledger)

    assert captured.value.reason == "invalid_replay_claim"


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("checks", 0, "evidence", "case_id"), "another-case"),
        (("checks", 0, "evidence", "request"), "Place a can on a table."),
        (("checks", 0, "evidence", "seed"), 8),
        (("checks", 0, "evidence", "runtime_config", "precheck_steps"), 1),
        (("checks", 0, "evidence", "runtime_config", "settle_steps"), 899),
        (("checks", 0, "evidence", "runtime_config", "contact_window_steps"), 119),
        (("checks", 0, "evidence", "runtime_config", "video_frames"), 119),
        (("checks", 0, "evidence", "runtime_config", "fps"), 11),
        (("checks", 0, "evidence", "environment_package_id"), SHA_A),
        (("checks", 1, "evidence", "dependencies", 0, "name"), "test.other"),
        (("checks", 1, "evidence", "dependencies", 0, "version"), "2"),
        (("checks", 2, "evidence", "checkpoint_steps"), 119),
        (("checks", 2, "evidence", "candidate_direct", "event_count"), 13),
        (
            ("checks", 2, "evidence", "candidate_direct", "event_kinds"),
            [*EVENT_KINDS[:-1], "evidence.completed"],
        ),
        (
            ("checks", 2, "evidence", "candidate_direct", "checkpoint_completed_steps"),
            [*CHECKPOINTS[:-1], 839],
        ),
        (("checks", 2, "evidence", "candidate_direct", "simulation_completed_steps"), 899),
        (("checks", 2, "evidence", "candidate_direct", "transcript_complete"), False),
        (("checks", 2, "evidence", "candidate_direct", "live_observer_matched"), False),
        (("checks", 2, "evidence", "candidate_direct", "streams_complete"), False),
        (("checks", 3, "evidence", "all_members_verified"), False),
        (("checks", 3, "evidence", "self_contained"), False),
        (("checks", 3, "evidence", "candidate_direct_manifest_sha256"), SHA_A),
        (("checks", 3, "evidence", "production_kernel_candidate_manifest_sha256"), SHA_A),
        (("checks", 1, "evidence", "dependencies", 4, "sha256"), SHA_A),
        (("checks", 1, "evidence", "dependencies", 3, "sha256"), SHA_A),
        (("checks", 4, "evidence", "candidate_direct", "fully_decoded"), False),
        (("checks", 4, "evidence", "candidate_direct", "frame_count"), 119),
        (("checks", 4, "evidence", "candidate_direct", "source_unique_frame_count"), 113),
        (("checks", 4, "evidence", "candidate_direct", "source_unique_frame_count"), 1),
        (("checks", 4, "evidence", "candidate_direct", "source_unique_frame_count"), 121),
        (("checks", 4, "evidence", "candidate_direct", "fps_numerator"), 11),
        (("checks", 4, "evidence", "candidate_direct", "fps_denominator"), 2),
        (("checks", 4, "evidence", "candidate_direct", "format_name"), "mov,mp4"),
        (("checks", 4, "evidence", "candidate_direct", "codec_name"), "hevc"),
        (("checks", 4, "evidence", "candidate_direct", "pixel_format"), "yuv444p"),
        (("checks", 4, "evidence", "candidate_direct", "sample_aspect_ratio"), "2/1"),
        (
            (
                "checks",
                4,
                "evidence",
                "candidate_direct",
                "square_sample_aspect_ratio_defaulted",
            ),
            False,
        ),
        (("checks", 4, "evidence", "candidate_direct", "decoded_png_count"), 6),
        (("checks", 4, "evidence", "candidate_direct", "all_pngs_decoded"), False),
        (("checks", 5, "evidence", "candidate_direct", "fail_count"), 1),
        (("checks", 5, "evidence", "candidate_direct", "not_run_count"), 1),
        (("checks", 5, "evidence", "candidate_direct", "resolved_scene_sha256"), SHA_A),
        (("checks", 5, "evidence", "candidate_direct", "settle_steps"), 899),
        (("checks", 5, "evidence", "candidate_direct", "contact_window_steps"), 119),
        (("checks", 5, "evidence", "candidate_direct", "video_frame_count"), 119),
        (("checks", 5, "evidence", "candidate_direct", "unique_video_frame_count"), 113),
        (("checks", 6, "evidence", "changed_during_qualification"), True),
        (("checks", 6, "evidence", "implementation_sha256"), SHA_A),
        (("checks", 6, "evidence", "implementation_file_count"), 2),
        (("checks", 6, "evidence", "scene_gen_tree_sha256"), SHA_A),
        (("checks", 6, "evidence", "ledger_contract_tree_sha256"), SHA_A),
        (("checks", 7, "evidence", "candidate_direct", "attempt_count"), 2),
        (("checks", 7, "evidence", "candidate_direct", "handler_call_count"), 2),
        (("checks", 7, "evidence", "candidate_direct", "cas_reread_verified"), False),
        (("checks", 7, "evidence", "candidate_direct", "all_artifacts_verified"), False),
        (("checks", 7, "evidence", "candidate_direct", "runtime_evidence_sha256"), SHA_A),
        (("checks", 7, "evidence", "candidate_direct", "validation_report_sha256"), SHA_A),
        (("checks", 7, "evidence", "candidate_direct", "event_transcript_sha256"), SHA_A),
        (
            ("checks", 7, "evidence", "candidate_direct", "runtime_asset_manifest_sha256"),
            SHA_A,
        ),
        (("checks", 7, "evidence", "candidate_direct", "video_sha256"), SHA_A),
        (("checks", 7, "evidence", "candidate_direct", "environment_package_id"), SHA_A),
        (
            ("checks", 7, "evidence", "candidate_direct", "dependencies", 0, "sha256"),
            SHA_B,
        ),
        (("checks", 7, "evidence", "production_kernel_candidate", "attempt_count"), 2),
        (("checks", 7, "evidence", "production_kernel_candidate", "handler_call_count"), 2),
        (("checks", 7, "evidence", "production_kernel_candidate", "cas_reread_verified"), False),
        (("checks", 7, "evidence", "production_kernel_candidate", "all_artifacts_verified"), False),
        (
            ("checks", 7, "evidence", "production_kernel_candidate", "runtime_evidence_sha256"),
            SHA_A,
        ),
        (
            ("checks", 7, "evidence", "production_kernel_candidate", "validation_report_sha256"),
            SHA_A,
        ),
        (
            ("checks", 7, "evidence", "production_kernel_candidate", "event_transcript_sha256"),
            SHA_A,
        ),
        (
            (
                "checks",
                7,
                "evidence",
                "production_kernel_candidate",
                "runtime_asset_manifest_sha256",
            ),
            SHA_A,
        ),
        (("checks", 7, "evidence", "production_kernel_candidate", "video_sha256"), SHA_A),
        (("checks", 7, "evidence", "production_kernel_candidate", "environment_package_id"), SHA_A),
        (
            ("checks", 7, "evidence", "production_kernel_candidate", "dependencies", 0, "sha256"),
            SHA_B,
        ),
    ],
)
def test_rejects_cross_claim_identity_or_invariant_mismatch(
    tmp_path: Path,
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)
    _mutate_report(bundle, path, replacement)

    with pytest.raises(ReplayQualificationError) as captured:
        _load(tmp_path, bundle, implementation, scene_gen, ledger)

    assert captured.value.reason == "replay_claim_mismatch"


@pytest.mark.parametrize("unique_frames", [2, 29])
def test_rejects_media_with_too_few_distinct_decoded_frames(
    tmp_path: Path,
    unique_frames: int,
) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)
    report = json.loads((bundle / "report.json").read_bytes())
    media = report["checks"][4]["evidence"]["candidate_direct"]
    media["source_unique_frame_count"] = unique_frames
    media["decoded_unique_frame_count"] = unique_frames
    report["checks"][5]["evidence"]["candidate_direct"]["unique_video_frame_count"] = unique_frames
    _rewrite_report(bundle, report)

    with pytest.raises(ReplayQualificationError) as captured:
        _load(tmp_path, bundle, implementation, scene_gen, ledger)

    assert captured.value.reason == "replay_claim_mismatch"


def test_source_and_decoded_uniqueness_are_independent_bounded_facts() -> None:
    claim = replay_qualification_module.ReplayMediaDecodeRunClaim.model_validate(
        {
            **_media(SHA_A, 30),
            "decoded_unique_frame_count": 31,
        }
    )

    replay_qualification_module._verify_media_claim(claim)


@pytest.mark.parametrize(
    "invalid_semantics",
    ["v1", "content", "wrong_skill", "wrong_implementation", "wrong_attempts"],
)
def test_rejects_descriptor_that_is_not_exact_replay_v2_evidence_repeatable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_semantics: str,
) -> None:
    bundle, implementation, scene_gen, ledger = _write_bundle(tmp_path)
    real_factory = replay_qualification_module.text2env_replay_descriptor

    def wrong_factory(*, qualification_artifact, implementation_sha256):
        descriptor = real_factory(
            qualification_artifact=qualification_artifact,
            implementation_sha256=implementation_sha256,
        )
        if invalid_semantics == "v1":
            from self_improving.harness.handlers.text2env_compile import (
                text2env_compile_descriptor,
            )

            return text2env_compile_descriptor(
                qualification_artifact=qualification_artifact,
                implementation_sha256=implementation_sha256,
            )
        if invalid_semantics == "content":
            return descriptor.model_copy(
                update={"reproducibility": (ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC)}
            )
        if invalid_semantics == "wrong_skill":
            return descriptor.model_copy(
                update={"skill_id": "test.replay", "mcp_tool_name": "test_replay_v1_0_0"}
            )
        if invalid_semantics == "wrong_implementation":
            return descriptor.model_copy(update={"implementation_sha256": SHA_A})
        return descriptor.model_copy(update={"max_attempts": 1})

    monkeypatch.setattr(
        replay_qualification_module,
        "text2env_replay_descriptor",
        wrong_factory,
    )

    with pytest.raises(ReplayQualificationError) as captured:
        _load(tmp_path, bundle, implementation, scene_gen, ledger)

    assert captured.value.reason == "descriptor_semantics_mismatch"


def _put_evidence_bytes(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str = "application/json",
    schema_version: str | None = "test.evidence.v1",
) -> ArtifactRef:
    source = tmp_path / f"{name}-{hashlib.sha256(payload).hexdigest()[:8]}"
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def test_evidence_model_locator_and_closure_aliases_fail_closed(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    nested = _put_evidence_bytes(
        store,
        tmp_path,
        name="nested",
        payload=b'{"value":1}\n',
    )
    closure = replay_qualification_module.ReplayEvidenceClosureManifest(
        schema_version="harness.replay_qualification.evidence_closure.v1",
        skill_ref=SKILL_REF,
        case_id=CASE_ID,
        refs=(nested,),
    )
    noncanonical = json.dumps(closure.model_dump(mode="json"), indent=2).encode()
    closure_ref = _put_evidence_bytes(
        store,
        tmp_path,
        name="closure",
        payload=noncanonical,
        schema_version="harness.replay_qualification.evidence_closure.v1",
    )
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._load_evidence_model(
            store,
            closure_ref,
            replay_qualification_module.ReplayEvidenceClosureManifest,
            label="closure",
        )
    assert captured.value.reason == "invalid_replay_evidence"

    canonical_ref = _put_evidence_bytes(
        store,
        tmp_path,
        name="canonical-closure",
        payload=(
            json.dumps(closure.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode(),
        schema_version="harness.replay_qualification.evidence_closure.v1",
    )
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._load_evidence_model(
            store,
            canonical_ref.model_copy(update={"media_type": "text/plain"}),
            replay_qualification_module.ReplayEvidenceClosureManifest,
            label="closure",
        )
    assert captured.value.reason == "invalid_replay_evidence_type"

    invalid_ref = _put_evidence_bytes(
        store,
        tmp_path,
        name="invalid-model",
        payload=b"{}\n",
    )
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._load_evidence_model(
            store,
            invalid_ref,
            replay_qualification_module.ReplayEvidenceClosureManifest,
            label="closure",
        )
    assert captured.value.reason == "invalid_replay_evidence"

    file_locator = nested.model_copy(update={"uri": str(tmp_path / "nested.json")})
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._resolve_evidence_ref(
            store,
            file_locator,
            label="nested",
        )
    assert captured.value.reason == "replay_evidence_locator_invalid"

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    drifting_store = SimpleNamespace(
        resolve_digest=lambda digest: first,
        resolve=lambda ref: SimpleNamespace(path=second),
    )
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._resolve_evidence_ref(
            drifting_store,  # type: ignore[arg-type]
            nested,
            label="nested",
        )
    assert captured.value.reason == "replay_evidence_locator_invalid"

    alias = nested.model_copy(update={"name": "alias"})
    assert replay_qualification_module._discover_evidence_closure(store, (nested, alias)) == (
        min((nested, alias), key=replay_qualification_module._artifact_sort_key),
    )
    conflicting = nested.model_copy(update={"schema_version": "test.other.v1"})
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._discover_evidence_closure(
            store,
            (nested, conflicting),
        )
    assert captured.value.reason == "replay_evidence_closure_mismatch"

    invalid_shaped = {
        **nested.model_dump(mode="json"),
        "sha256": "invalid",
    }
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._artifact_refs_in_json(invalid_shaped)
    assert captured.value.reason == "invalid_replay_evidence"


def _package_manifest_value() -> dict[str, object]:
    return {
        "schema_version": "robotwin.generated_scene_package.v1",
        "scene_id": "scene",
        "seed": 7,
        "source_scene_spec_sha256": SHA_A,
        "resolved_scene_sha256": SHA_B,
        "asset_catalog_sha256": SHA_C,
        "compiler_version": "1",
        "entrypoint": "generated_scene.py:load_scene",
        "resolved_only_entrypoint": "scene_gen.envs.generated_scene:load_resolved_scene",
        "files": [
            {"path": path, "sha256": digest, "bytes": 1}
            for path, digest in (
                ("request.txt", SHA_A),
                ("scene_spec.json", SHA_B),
                ("resolved_scene.json", SHA_C),
                ("generated_scene.py", SHA_D),
            )
        ],
    }


def test_package_member_manifest_is_exact_ordered_and_typed() -> None:
    value = _package_manifest_value()
    refs = replay_qualification_module._package_member_refs(value)
    assert tuple(item.name for item in refs) == (
        "request",
        "scene_spec",
        "resolved_scene",
        "generated_scene",
    )
    assert replay_qualification_module._package_member_refs({"schema_version": "test.v1"}) == ()

    attacks = []
    extra = json.loads(json.dumps(value))
    extra["extra"] = True
    attacks.append(extra)
    missing_member = json.loads(json.dumps(value))
    missing_member["files"].pop()
    attacks.append(missing_member)
    wrong_order = json.loads(json.dumps(value))
    wrong_order["files"][0]["path"] = "wrong.txt"
    attacks.append(wrong_order)
    invalid_identity = json.loads(json.dumps(value))
    invalid_identity["files"][0]["sha256"] = "invalid"
    attacks.append(invalid_identity)
    for attacked in attacks:
        with pytest.raises(ReplayQualificationError):
            replay_qualification_module._package_member_refs(attacked)


def test_known_json_package_ref_cannot_hide_recursive_members_by_media_relabel(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    member_refs = []
    for name, media_type, schema_version in (
        ("request", "text/plain", None),
        ("scene_spec", "application/json", "robotwin.scene_spec.v1"),
        ("resolved_scene", "application/json", "robotwin.resolved_scene.v1"),
        ("generated_scene", "text/x-python", None),
    ):
        member_refs.append(
            _put_evidence_bytes(
                store,
                tmp_path,
                name=name,
                payload=(
                    (json.dumps({"schema_version": schema_version}) + "\n").encode()
                    if schema_version is not None
                    else name.encode()
                ),
                media_type=media_type,
                schema_version=schema_version,
            )
        )
    manifest = _package_manifest_value()
    for record, ref in zip(manifest["files"], member_refs, strict=True):  # type: ignore[arg-type]
        record["sha256"] = ref.sha256
        record["bytes"] = ref.bytes
    manifest_ref = _put_evidence_bytes(
        store,
        tmp_path,
        name="package_manifest",
        payload=(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        schema_version="robotwin.generated_scene_package.v1",
    )

    discovered = replay_qualification_module._discover_evidence_closure(store, (manifest_ref,))
    assert {item.sha256 for item in discovered} == {
        manifest_ref.sha256,
        *(item.sha256 for item in member_refs),
    }

    relabeled = manifest_ref.model_copy(update={"media_type": "text/plain"})
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._discover_evidence_closure(store, (relabeled,))
    assert captured.value.reason == "invalid_replay_evidence_type"

    wrong_schema = manifest_ref.model_copy(update={"schema_version": "robotwin.scene_spec.v1"})
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._discover_evidence_closure(store, (wrong_schema,))
    assert captured.value.reason == "invalid_replay_evidence_type"

    invalid_model = _put_evidence_bytes(
        store,
        tmp_path,
        name="invalid_known_closure",
        payload=(
            json.dumps(
                {
                    "schema_version": "harness.replay_qualification.evidence_closure.v1",
                    "skill_ref": SKILL_REF,
                    "case_id": CASE_ID,
                    "refs": [],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode(),
        schema_version="harness.replay_qualification.evidence_closure.v1",
    )
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._discover_evidence_closure(store, (invalid_model,))
    assert captured.value.reason == "invalid_replay_evidence"


def test_machine_locators_strict_json_and_identity_helpers_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular = tmp_path / "regular"
    regular.write_text("value", encoding="utf-8")
    directory = tmp_path / "directory"
    directory.mkdir()
    linked_file = tmp_path / "linked-file"
    linked_file.symlink_to(regular)
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(directory, target_is_directory=True)

    assert (
        replay_qualification_module._checked_machine_file(str(regular), label="regular") == regular
    )
    assert replay_qualification_module._checked_machine_directory(str(directory)) == directory
    for value in ("relative", str(tmp_path / "missing"), str(directory), str(linked_file)):
        with pytest.raises(ReplayQualificationError):
            replay_qualification_module._checked_machine_file(value, label="file")
    for value in ("relative", str(tmp_path / "missing-dir"), str(regular), str(linked_directory)):
        with pytest.raises(ReplayQualificationError):
            replay_qualification_module._checked_machine_directory(value)

    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._require_actual_distribution_root("not-a-path")  # type: ignore[arg-type]
    assert captured.value.reason == "implementation_root_mismatch"
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._require_actual_distribution_root(tmp_path)
    assert captured.value.reason == "implementation_root_mismatch"

    for name, payload in (
        ("broken", b"{broken"),
        ("duplicate", b'{"a":1,"a":2}\n'),
        ("constant", b'{"a":NaN}\n'),
        ("array", b"[]\n"),
    ):
        path = tmp_path / f"{name}.json"
        path.write_bytes(payload)
        with pytest.raises(ReplayQualificationError) as captured:
            replay_qualification_module._load_strict_json(path, label=name)
        assert captured.value.reason == "invalid_replay_evidence"

    assert replay_qualification_module._unique_json_object([("a", 1)]) == {"a": 1}
    with pytest.raises(ValueError, match="duplicate"):
        replay_qualification_module._unique_json_object([("a", 1), ("a", 2)])
    with pytest.raises(ValueError, match="constant"):
        replay_qualification_module._reject_json_constant("NaN")
    with pytest.raises(ReplayQualificationError):
        replay_qualification_module._require_object([], label="object")
    with pytest.raises(ReplayQualificationError):
        replay_qualification_module._require_array({}, label="array")

    original_read = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == regular:
            raise OSError("unavailable")
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(ReplayQualificationError) as captured:
        replay_qualification_module._file_identity(regular)
    assert captured.value.reason == "replay_evidence_unavailable"

    assert replay_qualification_module._identity_document_sha256([]) == ""
    assert replay_qualification_module._identity_document_sha256({"nan": float("nan")}) == ""


def _sandbox_metrics() -> dict[str, object]:
    return {
        "memory_peak_bytes": 1,
        "memory_events": [],
        "pids_peak": 1,
        "pids_events": [],
        "cpu_stats": [],
    }


def test_sandbox_diagnostic_and_probe_fact_helpers_are_strict() -> None:
    valid = _sandbox_metrics()
    assert replay_qualification_module._valid_sandbox_metrics(valid)
    attacks: list[object] = [None, {}, {**valid, "extra": True}]
    attacks.extend(
        (
            {**valid, "memory_peak_bytes": -1},
            {**valid, "memory_peak_bytes": True},
            {**valid, "pids_peak": -1},
            {**valid, "memory_events": {}},
            {**valid, "memory_events": [{}]},
            {
                **valid,
                "memory_events": [
                    {"name": "z", "value": 1},
                    {"name": "a", "value": 1},
                ],
            },
            {
                **valid,
                "memory_events": [
                    {"name": "a", "value": 1},
                    {"name": "a", "value": 1},
                ],
            },
            {**valid, "memory_events": [{"name": "", "value": 1}]},
            {**valid, "memory_events": [{"name": "a", "value": -1}]},
        )
    )
    assert all(not replay_qualification_module._valid_sandbox_metrics(item) for item in attacks)

    artifact = ArtifactRef.model_validate(_ref(SHA_A, name="probe", schema_version="test.probe.v1"))
    identity = {
        "sha256": artifact.sha256,
        "bytes": artifact.bytes,
        "media_type": artifact.media_type,
        "schema_version": artifact.schema_version,
        "truncated": False,
    }
    assert replay_qualification_module._diagnostic_stream_identity_is_bound(
        identity,
        {artifact.sha256: artifact},
    )
    assert not replay_qualification_module._diagnostic_stream_identity_is_bound(
        {},
        {artifact.sha256: artifact},
    )
    assert not replay_qualification_module._diagnostic_stream_identity_is_bound(identity, {})

    probe = {"name": artifact.name, **{k: v for k, v in identity.items() if k != "truncated"}}
    assert replay_qualification_module._probe_artifact_records_are_bound(
        [probe],
        {artifact.sha256: artifact},
    )
    for records in (
        [None],
        [{"sha256": artifact.sha256}],
        [{**probe, "name": "wrong"}],
        [{**probe, "bytes": 2}],
        [probe, probe],
        [{**probe, "sha256": 1}],
    ):
        assert not replay_qualification_module._probe_artifact_records_are_bound(
            records,
            {artifact.sha256: artifact},
        )

    with pytest.raises(ReplayQualificationError):
        replay_qualification_module._one_schema_artifact(
            (),
            schema_version="test.probe.v1",
            label="probe",
        )
    with pytest.raises(ReplayQualificationError):
        replay_qualification_module._one_schema_artifact(
            (artifact, artifact),
            schema_version="test.probe.v1",
            label="probe",
        )

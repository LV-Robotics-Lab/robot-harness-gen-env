"""Operator bootstrap is a strict, pure-read preflight before real Genesis work."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.limited_image_preview_bootstrap import (
    LimitedImagePreviewBootstrapPlan,
    _publish_candidate,
    bootstrap_limited_image_preview,
    check_limited_image_preview_bootstrap,
    run_cli,
)

pytest_plugins = ("tests.self_improving.harness.test_limited_image_preview_library_saved_m1",)


def _put(store: LocalArtifactStore, root: Path, name: str, payload: bytes, schema: str | None):
    source = root / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema,
    )


def _git_repository(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Bootstrap Test"], check=True)
    (path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "source.py"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def bootstrap_plan(tmp_path):
    target_root = tmp_path / "target-cas"
    (target_root / "sha256").mkdir(parents=True)
    target = LocalArtifactStore(target_root)
    native_source = LocalArtifactStore(tmp_path / "native-source")
    saved_source = LocalArtifactStore(tmp_path / "saved-source")

    def target_ref(name, schema):
        return _put(target, tmp_path / "target-inputs", name, name.encode(), schema)

    qualifications = {
        name: target_ref(name + ".json", "harness.skill_qualification.v1")
        for name in ("compile", "replay", "observe", "validate", "revised")
    }
    manifests = {
        name: target_ref(name + ".json", "harness.genesis_runtime_manifest.v1")
        for name in ("asset-manifest", "scene-manifest", "capture-manifest")
    }
    catalog = target_ref("catalog.json", "harness.portable_asset_catalog.v1")
    source_scene = target_ref("source-scene.json", None)
    revised_admission = target_ref(
        "revised-admission.json", "harness.typed_qualification_admission.v1"
    )
    revised_request = target_ref(
        "revised-request.json", "harness.image2env_genesis_compile_request.v2"
    )
    revised_receipt = target_ref(
        "revised-receipt.json", "harness.image2env_genesis_compile_receipt.v2"
    )

    native_seeds = []
    for index, skill in enumerate(
        ("genesis.observe@3.0.0", "image2env.compile@2.0.0", "image2env.replay@2.0.0")
    ):
        admission = _put(
            native_source,
            tmp_path / "native-inputs",
            f"admission-{index}.json",
            f"admission-{index}".encode(),
            "harness.typed_qualification_admission.v1",
        )
        descriptor = _put(
            native_source,
            tmp_path / "native-inputs",
            f"descriptor-{index}.json",
            f"descriptor-{index}".encode(),
            "harness.skill_descriptor.v2" if index != 1 else "harness.skill_descriptor.v1",
        )
        native_seeds.append(
            {
                "skill_ref": skill,
                "source_cas_root": str(native_source.root),
                "admission_ref": admission.model_dump(mode="json"),
                "descriptor_ref": descriptor.model_dump(mode="json"),
            }
        )

    saved_refs = tuple(
        _put(
            saved_source,
            tmp_path / "saved-inputs",
            f"saved-{index}.json",
            f"saved-{index}".encode(),
            "harness.limited_image_preview_manifest.v1" if index == 0 else None,
        )
        for index in range(4)
    )
    authority_paths = {
        "asset_state_path": tmp_path / "state" / "asset.sqlite",
        "compile_state_root": tmp_path / "state" / "compile",
        "scene_state_root": tmp_path / "state" / "scene",
        "observation_state_root": tmp_path / "state" / "observe",
        "validation_state_root": tmp_path / "state" / "validate",
    }
    authority_paths["asset_state_path"].parent.mkdir(parents=True)
    authority_paths["asset_state_path"].touch()
    for key, path in authority_paths.items():
        if key == "asset_state_path":
            continue
        path.mkdir()
        (path / ("harness.sqlite3" if key == "compile_state_root" else "runs.sqlite")).touch()
    revised_root = tmp_path / "state" / "revised"
    revised_root.mkdir()
    (revised_root / "runs.sqlite").touch()
    roots = {}
    for role in ("backend", "distributions", "genesis", "interpreter", "native", "stdlib"):
        roots[role] = tmp_path / "runtime" / role
        roots[role].mkdir(parents=True)
    repository = tmp_path / "repo"
    commit = _git_repository(repository)
    codex = tmp_path / "bin" / "codex"
    python = tmp_path / "bin" / "python"
    codex.parent.mkdir()
    codex.write_bytes(b"codex")
    python.write_bytes(b"python")
    server_workdir = tmp_path / "server"
    server_workdir.mkdir()
    evidence_parent = tmp_path / "evidence-parent"
    evidence_parent.mkdir()

    plan = {
        "schema_version": "harness.limited_image_preview_bootstrap_plan.v1",
        "repository_root": str(repository),
        "expected_repository_commit": commit,
        "native_settings": {
            "cas_root": str(target.root),
            **{name: str(path) for name, path in authority_paths.items()},
            "compile_qualification": qualifications["compile"].model_dump(mode="json"),
            "scene_qualification": qualifications["replay"].model_dump(mode="json"),
            "observation_qualification": qualifications["observe"].model_dump(mode="json"),
            "validation_qualification": qualifications["validate"].model_dump(mode="json"),
            "asset_manifest": manifests["asset-manifest"].model_dump(mode="json"),
            "scene_manifest": manifests["scene-manifest"].model_dump(mode="json"),
            "capture_manifest": manifests["capture-manifest"].model_dump(mode="json"),
            "scene_roots": {name: str(path) for name, path in roots.items()},
            "capture_roots": {name: str(path) for name, path in roots.items()},
        },
        "native_projection_seeds": native_seeds,
        "asset_catalog": catalog.model_dump(mode="json"),
        "source_scene": source_scene.model_dump(mode="json"),
        "qualification_run_id": "10000000-0000-4000-8000-000000000001",
        "table_center_m": [0.0, 0.0, 0.2],
        "table_size_m": [1.0, 0.8, 0.4],
        "table_friction": 0.5,
        "seed": 41,
        "principal_id": "preview/operator",
        "project_id": "preview-project",
        "golden_database_path": str(tmp_path / "state" / "golden.sqlite"),
        "revised_state_root": str(revised_root),
        "revised_qualification": qualifications["revised"].model_dump(mode="json"),
        "revised_admission_ref": revised_admission.model_dump(mode="json"),
        "revised_registered_run_id": "20000000-0000-4000-8000-000000000001",
        "revised_registered_request": revised_request.model_dump(mode="json"),
        "revised_registered_receipt": revised_receipt.model_dump(mode="json"),
        "expected_revised_layout": {"x_m": 0.1, "y_m": 0.0, "yaw_rad": 0.0},
        "shared_state_root": str(tmp_path / "state" / "shared"),
        "qualification_library_root": str(tmp_path / "qualification-library"),
        "library_root": str(tmp_path / "library"),
        "publish_seed": {
            "source_cas_root": str(saved_source.root),
            "manifest_ref": saved_refs[0].model_dump(mode="json"),
            "supporting_refs": [ref.model_dump(mode="json") for ref in saved_refs[1:]],
            "project_id": "saved-m1-preview",
        },
        "checkpoint_path": str(tmp_path / "state" / "bootstrap-result.json"),
        "output_directory": str(tmp_path / "release"),
        "deployment": {
            "codex_executable": str(codex),
            "approved_executable_sha256": hashlib.sha256(codex.read_bytes()).hexdigest(),
            "server_python": str(python),
            "server_workdir": str(server_workdir),
            "model": "gpt-test",
            "evidence_root": str(evidence_parent / "runs"),
            "max_attempts": 2,
            "call_budget": 8,
            "attempt_seconds": 120,
        },
    }
    path = tmp_path / "bootstrap-plan.json"
    path.write_bytes(
        json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    )
    return path, plan


def test_check_is_strict_and_does_not_modify_target_or_authorities(bootstrap_plan):
    path, _ = bootstrap_plan
    parsed = LimitedImagePreviewBootstrapPlan.model_validate_json(path.read_bytes())
    tracked = (parsed.native_settings.cas_root, parsed.native_settings.asset_state_path.parent)
    before = {
        str(item): (item.stat().st_size, item.stat().st_mtime_ns)
        for root in tracked
        for item in root.rglob("*")
        if item.is_file()
    }

    result = check_limited_image_preview_bootstrap(path)

    after = {
        str(item): (item.stat().st_size, item.stat().st_mtime_ns)
        for root in tracked
        for item in root.rglob("*")
        if item.is_file()
    }
    assert result.status == "ready"
    assert result.missing_target_imports == 10
    assert result.source_objects_verified == 10
    assert before == after
    assert not parsed.checkpoint_path.exists()
    assert not parsed.output_directory.exists()


def test_plan_rejects_unknown_fields_and_incomplete_exact_skill_set(bootstrap_plan):
    _, document = bootstrap_plan
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LimitedImagePreviewBootstrapPlan.model_validate_json(
            json.dumps({**document, "generic_backend": "forbidden"})
        )
    with pytest.raises(ValidationError, match="exactly three native projection seeds"):
        LimitedImagePreviewBootstrapPlan.model_validate_json(
            json.dumps(
                {
                    **document,
                    "native_projection_seeds": document["native_projection_seeds"][:-1],
                }
            )
        )


def test_check_fails_closed_on_source_object_drift(bootstrap_plan):
    path, document = bootstrap_plan
    seed = document["native_projection_seeds"][0]
    ref = seed["admission_ref"]
    object_path = Path(seed["source_cas_root"]) / "sha256" / ref["sha256"][:2] / ref["sha256"]
    object_path.write_bytes(b"changed")

    with pytest.raises(ValueError, match="source CAS object"):
        check_limited_image_preview_bootstrap(path)


def test_check_cli_returns_machine_readable_result(bootstrap_plan, capsys):
    path, _ = bootstrap_plan

    assert run_cli(["--plan", str(path), "--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "harness.limited_image_preview_bootstrap_check.v1"
    assert payload["status"] == "ready"


def test_check_fails_closed_on_partial_release_directory(bootstrap_plan):
    path, _ = bootstrap_plan
    plan = LimitedImagePreviewBootstrapPlan.model_validate_json(path.read_bytes())
    plan.output_directory.mkdir()
    (plan.output_directory / "service-settings.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="release directory"):
        check_limited_image_preview_bootstrap(path)


def test_check_rejects_preexisting_production_library_before_bootstrap(bootstrap_plan):
    path, _ = bootstrap_plan
    plan = LimitedImagePreviewBootstrapPlan.model_validate_json(path.read_bytes())
    plan.library_root.mkdir()

    with pytest.raises(ValueError, match="production library_root must not exist"):
        check_limited_image_preview_bootstrap(path)


def test_publish_candidate_uses_real_library_manifest_ref(saved_m1_preview, tmp_path):
    """Regression for the formal bootstrap failure after a successful library write."""
    from self_improving.harness.genesis_scene_execution_v3 import GenesisSceneExecutionProducerV3
    from self_improving.harness.limited_image_preview_application import (
        LimitedImagePreviewApplication,
    )
    from self_improving.harness.limited_image_preview_library import (
        create_limited_image_preview_library,
    )
    from self_improving.harness.revised_image_compile_application import (
        RevisedImageCompileApplication,
    )

    _, store, compiler, verified = saved_m1_preview
    library = create_limited_image_preview_library(
        artifact_store=store,
        compiler=compiler,
        root=tmp_path / "qualification-library",
        project_id=verified.manifest.project_id,
    )
    revised = RevisedImageCompileApplication(tmp_path / "revised", compiler)
    shared = LimitedImagePreviewApplication(
        tmp_path / "shared",
        revised,
        GenesisSceneExecutionProducerV3(
            store, compiler, verified.manifest.initial_stage.runtime_manifest
        ),
        roots={},
        library=library,
    )
    candidate = shared.evaluate("image2env.preview_publish@1.0.0", verified.manifest)
    assert candidate.state.status.value == "succeeded"

    assert (
        _publish_candidate(shared, verified.manifest, saved_run_id=candidate.state.run_id)
        == candidate.state.run_id
    )


def test_execute_checkpoints_exact_imports_before_application_restore(bootstrap_plan):
    path, _ = bootstrap_plan
    plan = LimitedImagePreviewBootstrapPlan.model_validate_json(path.read_bytes())

    with pytest.raises(Exception, match="SQLite|schema|table"):
        bootstrap_limited_image_preview(path)

    checkpoint = json.loads(plan.checkpoint_path.read_bytes())
    assert checkpoint["schema_version"] == "harness.limited_image_preview_bootstrap_result.v1"
    assert checkpoint["stage"] == "imports_complete"
    assert check_limited_image_preview_bootstrap(path).missing_target_imports == 0

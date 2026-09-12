"""One Harness command owns assignment and bounded phases, not the user."""

import json
from pathlib import Path

import pytest

from self_improving.harness.codex_backend import CodexBackendResult
from self_improving.harness.limited_image_preview_cli import run_cli
from tests.self_improving.harness import test_limited_image_preview_controller as controller_tests

service = controller_tests.service


def _deployment(tmp_path):
    settings = tmp_path / "service.json"
    settings.write_text("{}")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            dict(
                service_settings=str(settings),
                codex_executable="/operator/codex",
                approved_executable_sha256="a" * 64,
                server_python="/operator/python",
                server_workdir=str(tmp_path),
                model="operator-model",
                evidence_root=str(tmp_path / "evidence"),
                max_attempts=2,
                call_budget=15,
                attempt_seconds=90,
            )
        )
    )
    return deployment, settings


def test_submit_persists_workflow_before_constructing_codex_backend(service, tmp_path, capsys):
    deployment, _ = _deployment(tmp_path)
    image = tmp_path / "raw.png"
    image.write_bytes(b"service fixture owns image validation")
    original_start = service.value.start
    trace = []

    def start(**arguments):
        result = original_start(**arguments)
        trace.append(("start", result["workflow_id"]))
        return result

    def unavailable_backend(**_arguments):
        trace.append(("backend", None))
        raise ValueError("Codex backend is unavailable")

    service.value.start = start
    code = run_cli(
        [
            "--deployment",
            str(deployment),
            "submit",
            "--image",
            str(image),
            "--text",
            "mouse on table",
            "--idempotency-key",
            "persist-first",
        ],
        service_factory=lambda _path: service.value,
        backend_factory=unavailable_backend,
    )

    assert code == 2
    assert trace == [("start", str(service.workflow)), ("backend", None)]
    assert service.harness.read(service.read).workflow_run_id == service.workflow
    assert "Codex backend is unavailable" in capsys.readouterr().err


def test_status_and_package_do_not_construct_codex_backend(service, tmp_path, capsys):
    deployment, _ = _deployment(tmp_path)

    def forbidden_backend(**_arguments):
        raise AssertionError("read-only Harness commands must not construct Codex")

    common = ["--deployment", str(deployment)]
    factories = dict(
        service_factory=lambda _path: service.value,
        backend_factory=forbidden_backend,
    )
    assert run_cli(common + ["status", "--workflow-id", str(service.workflow)], **factories) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["service"]["workflow_id"] == str(service.workflow)
    assert status["attempts"] == []

    package = service.value.status(workflow_id=str(service.workflow))["source_prompt"]
    service.value.package_get = lambda **_arguments: dict(
        workflow_id=str(service.workflow),
        revision=0,
        stage="package_get",
        status="published",
        package=package,
    )
    assert run_cli(common + ["package", "--workflow-id", str(service.workflow)], **factories) == 0
    assert json.loads(capsys.readouterr().out)["package"] == package


def test_submit_runs_owned_backend_and_reports_unsupported_without_manual_resume(
    service, tmp_path, capsys
):
    settings = tmp_path / "service.json"
    settings.write_text("{}")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            dict(
                service_settings=str(settings),
                codex_executable="/operator/codex",
                approved_executable_sha256="a" * 64,
                server_python="/operator/python",
                server_workdir=str(tmp_path),
                model="operator-model",
                evidence_root=str(tmp_path / "evidence"),
                max_attempts=2,
                call_budget=15,
                attempt_seconds=90,
            )
        )
    )
    image = tmp_path / "raw.png"
    image.write_bytes(b"service fixture owns image validation")
    called = []

    def service_factory(path):
        assert path == settings
        return service.value

    class Backend:
        def run(self, assignment, *, output_root):
            called.append(assignment)
            output_root.mkdir()
            return CodexBackendResult(
                "completed",
                output_root,
                1.0,
                0,
                None,
                None,
                {
                    "calls": [],
                    "terminal_decision": {
                        "workflow_id": str(service.workflow),
                        "decision": "unsupported",
                        "reason": "Only one mouse is supported.",
                    },
                },
                None,
            )

    def backend_factory(**kwargs):
        assert kwargs["executable"] == Path("/operator/codex")
        assert kwargs["server_settings"] == settings
        return Backend()

    code = run_cli(
        [
            "--deployment",
            str(deployment),
            "submit",
            "--image",
            str(image),
            "--text",
            "Use an unsupported object",
            "--idempotency-key",
            "first",
        ],
        service_factory=service_factory,
        backend_factory=backend_factory,
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported"
    assert len(called) == 1
    common = ["--deployment", str(deployment)]
    factories = dict(service_factory=service_factory, backend_factory=backend_factory)
    assert run_cli(common + ["resume", "--workflow-id", str(service.workflow)], **factories) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported"
    assert len(called) == 1
    assert run_cli(common + ["status", "--workflow-id", str(service.workflow)], **factories) == 0
    reported = json.loads(capsys.readouterr().out)
    assert reported["service"]["workflow_id"] == str(service.workflow)
    assert len(reported["attempts"]) == 1
    assert reported["attempts"][0]["terminal_decision"]["decision"] == "unsupported"

    def unpublished(**kwargs):
        return service.value.status(**kwargs)

    service.value.package_get = unpublished
    assert run_cli(common + ["package", "--workflow-id", str(service.workflow)], **factories) == 2
    assert "no published package" in capsys.readouterr().err

    ref = service.value.status(workflow_id=str(service.workflow))["source_prompt"]
    service.value.package_get = lambda **kwargs: dict(
        workflow_id=str(service.workflow),
        revision=0,
        stage="package_get",
        status="published",
        package=ref,
    )
    assert run_cli(common + ["package", "--workflow-id", str(service.workflow)], **factories) == 0
    assert json.loads(capsys.readouterr().out)["package"] == ref
    assert len(called) == 1

    # A text file is passed unchanged to the service; the CLI does not interpret its layout.
    received = []
    original_start = service.value.start

    def capture_start(**kwargs):
        received.append(kwargs)
        return original_start(**kwargs)

    service.value.start = capture_start
    text_file = tmp_path / "objective.txt"
    text_file.write_text("原始目标，保持换行\n", encoding="utf-8")
    assert (
        run_cli(
            common
            + [
                "submit",
                "--image",
                str(image),
                "--text-file",
                str(text_file),
                "--idempotency-key",
                "first",
            ],
            **factories,
        )
        == 2
    )
    assert received[0]["user_text"] == "原始目标，保持换行\n"
    assert received[0]["raw_image_path"] == str(image)
    capsys.readouterr()


@pytest.mark.parametrize("mode", ["normal", "repair"])
def test_package_can_materialize_a_published_preview_with_review_entrypoints(
    service, tmp_path, capsys, mode
):
    settings = tmp_path / "service.json"
    settings.write_text("{}")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            dict(
                service_settings=str(settings),
                codex_executable="/operator/codex",
                approved_executable_sha256="a" * 64,
                server_python="/operator/python",
                server_workdir=str(tmp_path),
                model="operator-model",
                evidence_root=str(tmp_path / "evidence"),
                max_attempts=2,
                call_budget=15,
                attempt_seconds=90,
            )
        )
    )
    manifest_path = tmp_path / "published-manifest.json"
    manifest_path.write_text(json.dumps({"preview_id": "b" * 64, "mode": mode}))
    manifest = service.value.store.put_file(
        manifest_path,
        name="published-manifest.json",
        media_type="application/json",
        schema_version="harness.limited_image_preview_manifest.v1",
    )
    calls = []
    trace = []

    class Library:
        def materialize(self, preview_id, destination):
            assert trace == ["package_get"]
            trace.append("materialize")
            calls.append((preview_id, destination))
            relatives = (
                "manifest.json",
                "inputs/raw-image.png",
                "inputs/prompt.txt",
                "asset/representation.json",
                "stages/initial/scene.json",
                "stages/initial/replay-report.json",
                "stages/initial/limited-validation.json",
                "stages/initial/observation.png",
                "stages/initial/baseline.mp4",
                "stages/initial/half-dt.mp4",
            )
            if mode == "repair":
                relatives += (
                    "stages/revised/scene.json",
                    "stages/revised/replay-report.json",
                    "stages/revised/limited-validation.json",
                    "stages/revised/baseline.mp4",
                    "stages/revised/half-dt.mp4",
                )
            for relative in relatives:
                path = destination / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"materialized by existing library seam")
            return destination

    class Runtime:
        def application(self, key):
            assert key == "library"
            return Library()

    service.value.runtime = Runtime()

    def package_get(**kwargs):
        trace.append("package_get")
        return dict(
            workflow_id=str(service.workflow),
            revision=0,
            stage="package_get",
            status="published",
            package=manifest.model_dump(mode="json"),
        )

    service.value.package_get = package_get
    output = tmp_path / f"review-package-{mode}"

    assert (
        run_cli(
            [
                "--deployment",
                str(deployment),
                "package",
                "--workflow-id",
                str(service.workflow),
                "--output",
                str(output),
            ],
            service_factory=lambda path: service.value,
            backend_factory=lambda **kwargs: object(),
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["materialized_path"] == str(output)
    expected = {
        "asset_closure": "asset",
        "initial_baseline_video": "stages/initial/baseline.mp4",
        "initial_half_dt_video": "stages/initial/half-dt.mp4",
        "initial_observation_image": "stages/initial/observation.png",
        "initial_report": "stages/initial/replay-report.json",
        "initial_scene": "stages/initial/scene.json",
        "initial_validation": "stages/initial/limited-validation.json",
        "input_image": "inputs/raw-image.png",
        "input_text": "inputs/prompt.txt",
        "manifest": "manifest.json",
    }
    if mode == "repair":
        expected.update(
            {
                "revised_baseline_video": "stages/revised/baseline.mp4",
                "revised_half_dt_video": "stages/revised/half-dt.mp4",
                "revised_report": "stages/revised/replay-report.json",
                "revised_scene": "stages/revised/scene.json",
                "revised_validation": "stages/revised/limited-validation.json",
            }
        )
    assert result["entrypoints"] == expected
    assert calls == [("b" * 64, output)]
    assert trace == ["package_get", "materialize"]


def test_package_materializes_development_evidence_without_formal_library(
    service, tmp_path, capsys
):
    settings = tmp_path / "service.json"
    settings.write_text("{}")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            dict(
                execution_mode="development",
                service_settings=str(settings),
                codex_executable="/operator/codex",
                approved_executable_sha256="a" * 64,
                server_python="/operator/python",
                server_workdir=str(tmp_path),
                model="operator-model",
                evidence_root=str(tmp_path / "evidence"),
                max_attempts=2,
                call_budget=15,
                attempt_seconds=90,
            )
        )
    )
    record_path = tmp_path / "development-record.json"
    record_path.write_text("{}")
    record = service.value.store.put_file(
        record_path,
        name="development-execution-record.json",
        media_type="application/json",
        schema_version="harness.development_execution_record.v1",
    )
    trace = []

    class DevelopmentArtifacts:
        def materialize(self, workflow_id, destination):
            trace.append(("development", workflow_id, destination))
            destination.mkdir()
            (destination / "development-record.json").write_text("{}")
            (destination / "development-context.json").write_text("{}")
            (destination / "index.json").write_text(
                json.dumps(
                    {
                        "kind": "limited_preview_development_materialization.v1",
                        "entrypoints": {
                            "record": "development-record.json",
                            "context": "development-context.json",
                            "inputs": [],
                            "outputs": [],
                            "logs": [],
                            "physical_evidence": [],
                            "images": [],
                            "videos": [],
                            "environment_packages": [],
                            "validation_reports": [],
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            return destination

    class Runtime:
        def application(self, key):
            raise AssertionError("development materialization must not open the formal library")

    service.value.execution_mode = "development"
    service.value.development_artifacts = DevelopmentArtifacts()
    service.value.runtime = Runtime()
    service.value.package_get = lambda **kwargs: {
        "workflow_id": str(service.workflow),
        "revision": 0,
        "stage": "package_get",
        "status": "validated_unreleased",
        "execution_mode": "development",
        "qualification_status": "development_unqualified",
        "package": record.model_dump(mode="json"),
    }
    output = tmp_path / "development-review"

    assert (
        run_cli(
            [
                "--deployment",
                str(deployment),
                "package",
                "--workflow-id",
                str(service.workflow),
                "--output",
                str(output),
            ],
            service_factory=lambda path: service.value,
            backend_factory=lambda **kwargs: object(),
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["materialized_path"] == str(output)
    assert result["entrypoints"]["record"] == "development-record.json"
    assert result["entrypoints"]["context"] == "development-context.json"
    assert trace == [("development", service.workflow, output)]


def test_package_output_rejects_relative_and_existing_destinations(service, tmp_path, capsys):
    settings = tmp_path / "service.json"
    settings.write_text("{}")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            dict(
                service_settings=str(settings),
                codex_executable="/operator/codex",
                approved_executable_sha256="a" * 64,
                server_python="/operator/python",
                server_workdir=str(tmp_path),
                model="operator-model",
                evidence_root=str(tmp_path / "evidence"),
                max_attempts=2,
                call_budget=15,
                attempt_seconds=90,
            )
        )
    )
    existing = tmp_path / "already-there"
    existing.mkdir()
    common = [
        "--deployment",
        str(deployment),
        "package",
        "--workflow-id",
        str(service.workflow),
        "--output",
    ]
    for rejected in (Path("relative-review-package"), existing):
        assert (
            run_cli(
                common + [str(rejected)],
                service_factory=lambda path: service.value,
                backend_factory=lambda **kwargs: object(),
            )
            == 2
        )
        error = json.loads(capsys.readouterr().err)
        assert error["status"] == "error"
        assert "absolute new directory" in error["message"]


def test_operator_configuration_rejects_unknown_overrides_before_factory(tmp_path, capsys):
    path = tmp_path / "deployment.json"
    path.write_text(
        json.dumps({"clock": "secret-must-not-appear", "service_settings": "relative.json"})
    )

    def forbidden(path):
        raise AssertionError("invalid configuration must not restore service")

    assert (
        run_cli(
            [
                "--deployment",
                str(path),
                "status",
                "--workflow-id",
                "00000000-0000-4000-8000-000000000001",
            ],
            service_factory=forbidden,
        )
        == 2
    )
    error = capsys.readouterr().err
    assert json.loads(error)["status"] == "error"
    assert "secret-must-not-appear" not in error


def test_operator_mode_mismatch_fails_before_codex_backend(service, tmp_path, capsys):
    settings = tmp_path / "service.json"
    settings.write_text("{}")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            dict(
                execution_mode="development",
                service_settings=str(settings),
                codex_executable="/operator/codex",
                approved_executable_sha256="a" * 64,
                server_python="/operator/python",
                server_workdir=str(tmp_path),
                model="operator-model",
                evidence_root=str(tmp_path / "evidence"),
                max_attempts=2,
                call_budget=15,
                attempt_seconds=90,
            )
        )
    )
    service.value.execution_mode = "release"

    def forbidden(**kwargs):
        raise AssertionError("mode mismatch must not start Codex")

    assert (
        run_cli(
            [
                "--deployment",
                str(deployment),
                "resume",
                "--workflow-id",
                str(service.workflow),
            ],
            service_factory=lambda path: service.value,
            backend_factory=forbidden,
        )
        == 2
    )
    assert "execution mode" in json.loads(capsys.readouterr().err)["message"]

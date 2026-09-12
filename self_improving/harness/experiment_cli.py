"""Harness-only experimental text/image/video entrypoint; models remain internal."""

import argparse
import json
import time
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ExperimentDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_mode: Literal["development"]
    allow_changed_skill_closure: Literal[True]
    state_root: Path
    runtime_roots: dict[str, Path]
    codex_executable: Path
    approved_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_python: Path
    server_workdir: Path
    model: str = Field(min_length=1)
    model_timeout: int = Field(default=120, gt=0, le=300, strict=True)
    default_source: Literal["local", "web", "generated"] = "generated"
    allowed_sources: tuple[Literal["local", "web", "generated"], ...] = (
        "local",
        "web",
        "generated",
    )
    evidence_root: Path
    max_attempts: int = Field(default=3, gt=0, le=10, strict=True)
    call_budget: int = Field(default=30, gt=0, le=100, strict=True)
    attempt_seconds: int = Field(default=3600, gt=0, le=43200, strict=True)
    catalog_path: Path | None = None
    asset_library_root: Path | None = None
    source_ref: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    denied_roots: tuple[Path, ...] = ()

    @field_validator("allow_changed_skill_closure", mode="before")
    @classmethod
    def explicit_opt_in(cls, value):
        if value is not True:
            raise ValueError("development requires explicit boolean opt-in")
        return value

    @field_validator(
        "state_root",
        "codex_executable",
        "server_python",
        "server_workdir",
        "evidence_root",
        "catalog_path",
        "asset_library_root",
    )
    @classmethod
    def absolute_path(cls, value):
        if value is not None and (not value.is_absolute() or ".." in value.parts):
            raise ValueError("deployment paths must be absolute without traversal")
        return value

    @field_validator("runtime_roots", "denied_roots")
    @classmethod
    def root_paths(cls, value):
        for path in value.values() if isinstance(value, dict) else value:
            cls.absolute_path(path)
        return value


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_request(argv=None):
    """Parse a Harness command without starting a model, database, or simulator."""
    parser = _Parser(description=__doc__)
    parser.add_argument("--deployment", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit")
    text = submit.add_mutually_exclusive_group()
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    submit.add_argument("--image", type=Path, action="append", default=[])
    submit.add_argument("--video", type=Path)
    submit.add_argument("--idempotency-key", required=True)
    for name in ("resume", "status", "package"):
        command = commands.add_parser(name)
        command.add_argument("--workflow-id", required=True, type=UUID)
        if name == "package":
            command.add_argument("--output", type=Path, required=True)
    request = parser.parse_args(argv)
    if request.command == "submit" and not (
        request.text or request.text_file or request.image or request.video
    ):
        raise ValueError("submit requires text, image, video, or a combination")
    return request


def _emit(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str))


def record_command_result(root, command, result, wall_seconds):
    """Private command diagnostics, never a workflow receipt or promotion authority."""
    folder = Path(root) / "commands"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (str(uuid4()) + ".json")
    value = {
        **result,
        "command": command,
        "command_wall_seconds": wall_seconds,
        "command_record": str(path),
    }
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return value


def run_cli(argv=None):
    """Return 0 for completed/read operations, 1 for execution failure, 2 for bad input."""
    started = time.monotonic()
    try:
        request = parse_request(argv)
    except ValueError as error:
        _emit({"status": "failed", "error_code": "invalid_request", "detail": str(error)})
        return 2
    try:
        deployment = ExperimentDeployment.model_validate_json(request.deployment.read_bytes())
    except (OSError, ValueError, ValidationError) as error:
        _emit({"status": "failed", "error_code": "invalid_deployment", "detail": str(error)})
        return 2
    try:
        text = None
        if request.command == "submit":
            text = (
                request.text_file.read_text(encoding="utf-8") if request.text_file else request.text
            )
            if text is not None and not text.strip():
                raise ValueError("text must not be empty")
            for path in [*request.image, *([request.video] if request.video else [])]:
                if path.is_symlink() or not path.is_file():
                    raise ValueError("input media must be existing regular non-symlink files")
            if not request.idempotency_key.strip():
                raise ValueError("idempotency key must not be empty")
        if request.command == "package" and (
            not request.output.is_absolute()
            or ".." in request.output.parts
            or request.output.exists()
            or request.output.is_symlink()
        ):
            raise ValueError("package output must be an absolute new directory")
    except (OSError, ValueError) as error:
        _emit({"status": "failed", "error_code": "invalid_request", "detail": str(error)})
        return 2
    invocation_identity = None
    try:
        from .experiment_invocation_identity import capture_invocation_identity
        from .experiment_service import ExperimentService

        if request.command != "status":
            invocation_identity = capture_invocation_identity(
                deployment.server_workdir, request.deployment
            )

        backend = None
        if request.command in {"submit", "resume"}:
            from .codex_backend import CodexBackend

            backend = CodexBackend(
                executable=deployment.codex_executable,
                approved_executable_sha256=deployment.approved_executable_sha256,
                server_python=deployment.server_python,
                server_settings=request.deployment.absolute(),
                server_workdir=deployment.server_workdir,
                model=deployment.model,
            )
        service = ExperimentService(
            request.deployment.absolute(), state_root=deployment.state_root, backend=backend
        )
        if request.command in {"submit", "resume"}:
            from .limited_image_preview_controller import LimitedImagePreviewController

            controller = LimitedImagePreviewController(
                service,
                backend,
                evidence_root=deployment.evidence_root,
                max_attempts=deployment.max_attempts,
                call_budget=deployment.call_budget,
                attempt_seconds=deployment.attempt_seconds,
            )
            result = (
                controller.start(
                    user_text=text,
                    images=tuple(path.absolute() for path in request.image),
                    video=request.video.absolute() if request.video else None,
                    idempotency_key=request.idempotency_key,
                )
                if request.command == "submit"
                else controller.resume(request.workflow_id)
            )
        elif request.command == "status":
            result = service.status(workflow_id=request.workflow_id)
        else:
            from .experiment_package import package_workflow

            result = package_workflow(service, request.workflow_id, request.output)
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        if request.command != "status":
            result = {**result, "invocation_identity": invocation_identity}
            result = record_command_result(
                deployment.evidence_root, request.command, result, time.monotonic() - started
            )
        _emit(result)
        return (
            0
            if request.command == "status"
            or isinstance(result, dict)
            and result.get("status") == "passed"
            else 1
        )
    except Exception as error:
        value = {
            "status": "failed",
            "error_code": getattr(error, "code", "execution_failed"),
            "detail": str(error),
            "invocation_identity": invocation_identity,
        }
        if request.command != "status":
            value = record_command_result(
                deployment.evidence_root, request.command, value, time.monotonic() - started
            )
        _emit(value)
        return 1


def main():
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

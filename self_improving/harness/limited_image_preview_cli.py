"""One operator-configured Harness entrypoint; Codex and MCP stay internal."""

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .codex_backend import CodexBackend
from .limited_image_preview_controller import LimitedImagePreviewController
from .schemas.limited_image_preview_service import PreviewServiceResult


class PreviewDeployment(BaseModel):
    """Operator configuration, never a model-supplied execution override."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    service_settings: Path
    codex_executable: Path
    approved_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    server_python: Path
    server_workdir: Path
    model: str = Field(min_length=1)
    evidence_root: Path
    max_attempts: int = Field(gt=0)
    call_budget: int = Field(gt=0)
    attempt_seconds: int = Field(gt=0)

    @field_validator(
        "service_settings", "codex_executable", "server_python", "server_workdir", "evidence_root"
    )
    @classmethod
    def absolute_path(cls, value):
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("deployment paths must be absolute without traversal")
        return value


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit", help="Start and complete bounded internal Codex phases")
    submit.add_argument("--image", required=True, type=Path)
    text = submit.add_mutually_exclusive_group(required=True)
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    submit.add_argument("--idempotency-key", required=True)
    for name in ("resume", "status", "package"):
        command = commands.add_parser(name)
        command.add_argument("--workflow-id", required=True, type=UUID)
    return parser


def _service_factory(path):
    # Import only at production assembly: protocol tests do not restore real applications.
    from .limited_image_preview_service import create_limited_image_preview_service

    return create_limited_image_preview_service(path)


def run_cli(argv=None, *, service_factory=_service_factory, backend_factory=CodexBackend):
    """Public protocol seam; main always uses the fixed production factories."""
    arguments = _parser().parse_args(argv)
    try:
        deployment = PreviewDeployment.model_validate_json(arguments.deployment.read_bytes())
        service = service_factory(deployment.service_settings)
        backend = backend_factory(
            executable=deployment.codex_executable,
            approved_executable_sha256=deployment.approved_executable_sha256,
            server_python=deployment.server_python,
            server_settings=deployment.service_settings,
            server_workdir=deployment.server_workdir,
            model=deployment.model,
        )
        controller = LimitedImagePreviewController(
            service,
            backend,
            evidence_root=deployment.evidence_root,
            max_attempts=deployment.max_attempts,
            call_budget=deployment.call_budget,
            attempt_seconds=deployment.attempt_seconds,
        )
        if arguments.command == "submit":
            text = (
                arguments.text
                if arguments.text is not None
                else arguments.text_file.read_text(encoding="utf-8")
            )
            if not text.strip() or not arguments.image.is_file():
                raise ValueError("submit requires an existing image and nonempty text")
            started = time.monotonic_ns()
            output = PreviewServiceResult.model_validate_json(
                json.dumps(
                    service.start(
                        raw_image_path=str(arguments.image.absolute()),
                        user_text=text,
                        idempotency_key=arguments.idempotency_key,
                    )
                )
            )
            result = controller.run_to_terminal(
                output.workflow_id, input_elapsed_ns=time.monotonic_ns() - started
            )
        elif arguments.command == "resume":
            result = controller.run_to_terminal(arguments.workflow_id)
        elif arguments.command == "status":
            status = PreviewServiceResult.model_validate_json(
                json.dumps(service.status(workflow_id=str(arguments.workflow_id)))
            )
            if status.workflow_id != arguments.workflow_id:
                raise ValueError("service returned foreign workflow status")
            result = dict(
                service=status.model_dump(mode="json"),
                attempts=[
                    {
                        key: row.get(key)
                        for key in (
                            "number",
                            "status",
                            "backend_status",
                            "elapsed_seconds",
                            "failure",
                            "call_count",
                            "terminal_decision",
                        )
                    }
                    for row in controller.attempts(arguments.workflow_id)
                ],
            )
        else:
            package = PreviewServiceResult.model_validate_json(
                json.dumps(service.package_get(workflow_id=str(arguments.workflow_id)))
            )
            if (
                package.workflow_id != arguments.workflow_id
                or package.status != "published"
                or package.package is None
            ):
                raise ValueError("workflow has no published package")
            service.store.resolve(package.package)
            result = package.model_dump(mode="json")
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
        if arguments.command in {"status", "package"} or result.get("status") == "published":
            return 0
        return 3 if result.get("status") in {"running", "recovering"} else 2
    except (ValueError, TypeError, OSError) as error:
        # Pydantic normally includes rejected input values; operator JSON may contain secrets.
        message = (
            error.errors(include_input=False, include_context=False)
            if isinstance(error, ValidationError)
            else str(error)
        )
        print(
            json.dumps({"status": "error", "message": message}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2


def main():
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())

"""Fixed command-line adapter for the qualified ``text2env.compile`` application."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .application import (
    DEFAULT_PRODUCTION_ASSET_LIBRARY_ROOT,
    CompileApplicationConfigurationError,
    CompileApplicationSettings,
    ExternalCatalogError,
    create_compile_application,
)
from .qualification import QualificationBundleError
from .registry import RegistryRegistrationError, RunPersistenceError
from .schemas import RunState, RunStatus


class _InputError(ValueError):
    pass


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(status)


class _TerminalStateError(RuntimeError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _InputError(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise _ParserExit(status)


def main(argv: Sequence[str] | None = None) -> int:
    """Compile one request and emit its durable terminal ``RunState`` as JSON."""

    try:
        arguments = _parser().parse_args(argv)
    except _InputError as error:
        sys.stderr.write(f"robot-harness-compile: input error: {error}\n")
        return 78
    except _ParserExit as exit_request:
        return exit_request.status
    try:
        application = create_compile_application(
            CompileApplicationSettings(
                state_root=arguments.state_root,
                external_catalog_roots=tuple(arguments.trusted_catalog_roots),
                allowed_asset_roots=tuple(arguments.allowed_asset_roots),
                admission_date=arguments.admission_date,
                asset_library_root=arguments.asset_library_root,
            )
        )
        state = application.compile(
            request=arguments.request,
            seed=arguments.seed,
            asset_catalog_path=arguments.catalog_path,
            generate_missing_assets=arguments.generate_missing,
        )
        if not isinstance(state, RunState) or state.status == RunStatus.RUNNING:
            raise _TerminalStateError
    except RunPersistenceError as error:
        sys.stderr.write(
            "robot-harness-compile: durable persistence error: "
            f"operation={error.operation} run_id={error.run_id} "
            f"cause={type(error.cause).__name__}\n"
        )
        return 74
    except (
        CompileApplicationConfigurationError,
        ExternalCatalogError,
        QualificationBundleError,
        RegistryRegistrationError,
    ) as error:
        sys.stderr.write(
            "robot-harness-compile: configuration/input/trust/qualification error: "
            f"{type(error).__name__}\n"
        )
        return 78
    except Exception as error:
        sys.stderr.write(
            "robot-harness-compile: durable/storage/internal adapter error: "
            f"{type(error).__name__}\n"
        )
        return 74
    _write_run_state(state)
    return {
        RunStatus.SUCCEEDED: 0,
        RunStatus.BLOCKED: 10,
        RunStatus.FAILED: 20,
    }[state.status]


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="robot-harness-compile",
        description="Invoke the fixed qualified text2env.compile Skill.",
    )
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument(
        "--asset-library-root",
        type=Path,
        default=DEFAULT_PRODUCTION_ASSET_LIBRARY_ROOT,
        help=(
            "persistent reusable-asset library; defaults to the project-local "
            "self_improving asset library"
        ),
    )
    parser.add_argument(
        "--trusted-catalog-root",
        action="append",
        dest="trusted_catalog_roots",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--allowed-asset-root",
        action="append",
        dest="allowed_asset_roots",
        required=True,
        type=Path,
    )
    parser.add_argument("--admission-date", required=True, type=_admission_date)
    parser.add_argument("--catalog-path", required=True, type=Path)
    parser.add_argument("--request", required=True, type=_request)
    parser.add_argument("--seed", required=True, type=_seed)
    parser.add_argument("--generate-missing", action="store_true")
    return parser


def _admission_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("admission date must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("admission date must use YYYY-MM-DD")
    return parsed


def _request(value: str) -> str:
    if not 3 <= len(value) <= 2000:
        raise argparse.ArgumentTypeError("request must contain 3 to 2000 characters")
    return value


def _seed(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("seed must be an integer") from error
    if not 0 <= parsed <= 2_147_483_647:
        raise argparse.ArgumentTypeError("seed must be between 0 and 2147483647")
    return parsed


def _write_run_state(state: RunState) -> None:
    payload = json.dumps(
        state.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stdout.write(f"{payload}\n")


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())

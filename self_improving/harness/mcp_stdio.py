"""Stdio entry point for the exact qualified Golden MCP server."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from mcp.server.stdio import stdio_server

from .mcp_adapter import HarnessMCPAdapter
from .mcp_assembly import CompileMCPServerSettings, create_compile_mcp_adapter
from .mcp_server import create_harness_mcp_server


def load_compile_mcp_settings(path: Path) -> CompileMCPServerSettings:
    """Load one strict operator settings document."""

    if not isinstance(path, Path):
        raise TypeError("settings path must be an exact Path")
    return CompileMCPServerSettings.model_validate_json(path.read_bytes())


async def _serve_stdio(adapter_factory: Callable[[], HarnessMCPAdapter]) -> None:
    """Run one server while redirecting all non-protocol Python stdout."""

    if not callable(adapter_factory):
        raise TypeError("adapter_factory must be callable")
    async with stdio_server() as (read_stream, write_stream):
        with contextlib.redirect_stdout(sys.stderr):
            adapter = adapter_factory()
            server = create_harness_mcp_server(adapter)
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robot-harness-golden-mcp",
        description="Serve exact qualified Golden Workflow tools over MCP stdio.",
    )
    parser.add_argument("--settings", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse local settings before handing fd0/fd1 to the MCP transport."""

    args = _parser().parse_args(argv)
    settings_path = args.settings.resolve(strict=True)
    asyncio.run(
        _serve_stdio(
            lambda: create_compile_mcp_adapter(load_compile_mcp_settings(settings_path))
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a real subprocess
    raise SystemExit(main())


__all__ = ["load_compile_mcp_settings", "main"]

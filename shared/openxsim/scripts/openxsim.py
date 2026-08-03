#!/usr/bin/env python3
"""Command line entry point for the four Open-X-Sim workflows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source" / "agenticsim"))

from agenticsim.openxsim.assets import (
    AssetScout,
    CatalogSearchProvider,
    GitHubRepositoryDiscoveryProvider,
    GitHubTreeSearchProvider,
)
from agenticsim.openxsim.anchors import ColorLayoutAnchorProvider
from agenticsim.openxsim.ir import EnvironmentPackage
from agenticsim.openxsim.pipeline import OpenXSimPipeline
from agenticsim.openxsim.robotwin import runtime_evidence_from_rollout


def _backends(value: str) -> tuple[str, ...]:
    result = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    if not result:
        raise argparse.ArgumentTypeError("at least one backend is required")
    return result


def _json_file(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print_results(package, results) -> None:
    print(
        json.dumps(
            {
                "package_id": package.package_id,
                "package_digest": package.digest(),
                "results": {backend: result.to_dict() for backend, result in results.items()},
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def command_text2env(args: argparse.Namespace) -> int:
    pipeline = OpenXSimPipeline(args.output)
    package, results = pipeline.text2env(
        args.instruction,
        repo_root=args.repo_root,
        backends=args.backends,
        strict=args.strict,
    )
    _print_results(package, results)
    return 0


def command_anchor2env(args: argparse.Namespace) -> int:
    pipeline = OpenXSimPipeline(args.output)
    vision_provider = ColorLayoutAnchorProvider() if args.vision_provider == "color-layout" else None
    package, results = pipeline.anchor2env(
        args.instruction,
        args.media,
        repo_root=args.repo_root,
        backends=args.backends,
        annotations=_json_file(args.annotations),
        vision_provider=vision_provider,
        sample_count=args.sample_count,
        strict=args.strict,
    )
    _print_results(package, results)
    return 0


def command_asset(args: argparse.Namespace) -> int:
    providers = []
    for catalog in args.catalog:
        providers.append(CatalogSearchProvider(catalog))
    for repository in args.github:
        providers.append(
            GitHubTreeSearchProvider(
                repository,
                branch=args.github_branch,
                token=args.github_token,
                license=args.license,
            )
        )
    if args.github_discovery:
        providers.append(
            GitHubRepositoryDiscoveryProvider(
                repository_query=args.github_repository_query,
                repository_limit=args.github_repository_limit,
                token=args.github_token,
            )
        )
    if not providers:
        raise SystemExit("asset-scout requires --catalog, --github, or --github-discovery")
    pipeline = OpenXSimPipeline(args.output)
    candidate, bundle = pipeline.acquire_asset(
        args.query,
        AssetScout(providers),
        asset_id=args.asset_id,
        candidate_index=args.candidate_index,
        target_formats=args.formats,
        smoke_backends=args.smoke_backends,
    )
    print(
        json.dumps(
            {
                "selected_candidate": candidate.candidate_id,
                "asset_id": bundle.asset_id,
                "representations": [item.__dict__ for item in bundle.representations],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def command_transfer(args: argparse.Namespace) -> int:
    pipeline = OpenXSimPipeline(args.output)
    package, results, reports = pipeline.transfer(
        args.source,
        source_backend=args.source_backend,
        target_backends=args.backends,
        strict=args.strict,
    )
    print(
        json.dumps(
            {
                "package_id": package.package_id,
                "compile_results": {backend: result.to_dict() for backend, result in results.items()},
                "conformance": {backend: report.to_dict() for backend, report in reports.items()},
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def command_robotwin_evidence(args: argparse.Namespace) -> int:
    package = EnvironmentPackage.read_json(args.package)
    evidence = runtime_evidence_from_rollout(
        package,
        args.task_program,
        args.rollout_report,
        minimum_video_frames=args.minimum_video_frames,
    )
    output = Path(args.evidence_output) if args.evidence_output else Path(args.rollout_report).parent / "runtime_evidence.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"runtime_evidence": str(output.resolve()), **evidence}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "artifacts" / "openxsim"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    text = subparsers.add_parser("text2env", help="compile text only")
    text.add_argument("--instruction", required=True)
    text.add_argument("--repo-root", default=str(ROOT))
    text.add_argument("--backends", type=_backends, default=("sapien",))
    text.add_argument("--strict", action="store_true")
    text.set_defaults(func=command_text2env)

    anchor = subparsers.add_parser("anchor2env", help="compile text plus image/video evidence")
    anchor.add_argument("--instruction", required=True)
    anchor.add_argument("--media", required=True)
    anchor.add_argument("--annotations", help="JSON constraints produced by a user or VLM")
    anchor.add_argument("--sample-count", type=int, default=8)
    anchor.add_argument("--vision-provider", choices=("none", "color-layout"), default="none")
    anchor.add_argument("--repo-root", default=str(ROOT))
    anchor.add_argument("--backends", type=_backends, default=("sapien",))
    anchor.add_argument("--strict", action="store_true")
    anchor.set_defaults(func=command_anchor2env)

    asset = subparsers.add_parser("asset-scout", help="search, download, convert, and register an asset")
    asset.add_argument("--query", required=True)
    asset.add_argument("--asset-id", required=True)
    asset.add_argument("--catalog", action="append", default=[])
    asset.add_argument("--github", action="append", default=[])
    asset.add_argument("--github-discovery", action="store_true")
    asset.add_argument("--github-repository-query")
    asset.add_argument("--github-repository-limit", type=int, default=5)
    asset.add_argument("--github-branch", default="main")
    asset.add_argument("--github-token")
    asset.add_argument("--license", default="unknown")
    asset.add_argument("--candidate-index", type=int, default=0)
    asset.add_argument(
        "--formats",
        type=_backends,
        default=("usda", "mjcf", "urdf", "sapien_manifest", "metasim_object"),
    )
    asset.add_argument("--smoke-backends", type=_backends, default=())
    asset.set_defaults(func=command_asset)

    transfer = subparsers.add_parser("transfer", help="import an existing environment and compile it elsewhere")
    transfer.add_argument("--source", required=True)
    transfer.add_argument("--source-backend")
    transfer.add_argument("--backends", type=_backends, required=True)
    transfer.add_argument("--strict", action="store_true")
    transfer.set_defaults(func=command_transfer)

    evidence = subparsers.add_parser(
        "robotwin-evidence",
        help="validate a RoboTwin rollout against its package and task program",
    )
    evidence.add_argument("--package", required=True)
    evidence.add_argument("--task-program", required=True)
    evidence.add_argument("--rollout-report", required=True)
    evidence.add_argument("--evidence-output")
    evidence.add_argument("--minimum-video-frames", type=int, default=3)
    evidence.set_defaults(func=command_robotwin_evidence)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

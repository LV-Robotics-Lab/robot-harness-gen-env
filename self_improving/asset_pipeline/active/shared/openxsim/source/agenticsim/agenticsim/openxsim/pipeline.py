"""One orchestration surface for all four Open-X-Sim user workflows."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .anchors import VisionAnchorProvider, extract_anchor, fuse_anchor
from .assets import AssetCandidate, AssetScout, compile_downloaded_asset, download_candidate
from .backends import CompileResult, compile_package
from .conformance import ConformanceReport, evaluate_conformance
from .importers import import_environment
from .ir import AssetBundle, EnvironmentPackage, EnvSpec, Pose, SceneObject, TaskSpec
from .text2env import compile_text


class OpenXSimPipeline:
    """Shared pipeline; workflows differ only in their input and optional stages."""

    def __init__(self, output_root: str | Path):
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)

    def text2env(
        self,
        instruction: str,
        *,
        repo_root: str | Path | None = None,
        backends: Iterable[str] = ("sapien",),
        strict: bool = False,
    ) -> tuple[EnvironmentPackage, dict[str, CompileResult]]:
        backend_names = tuple(backends)
        package = compile_text(instruction, repo_root=repo_root, target_backends=backend_names)
        run_dir = self.output_root / package.package_id / "text2env"
        package.write_json(run_dir / "environment_package.json")
        results = compile_package(package, run_dir / "compiled", backend_names, strict=strict)
        self._write_workflow_manifest(run_dir, "text2env", package, results)
        return package, results

    def anchor2env(
        self,
        instruction: str,
        media_path: str | Path,
        *,
        repo_root: str | Path | None = None,
        backends: Iterable[str] = ("sapien",),
        annotations: Mapping[str, Any] | None = None,
        vision_provider: VisionAnchorProvider | None = None,
        sample_count: int = 8,
        strict: bool = False,
    ) -> tuple[EnvironmentPackage, dict[str, CompileResult]]:
        backend_names = tuple(backends)
        base = compile_text(instruction, repo_root=repo_root, target_backends=backend_names)
        run_dir = self.output_root / base.package_id / "anchor2env"
        anchor = extract_anchor(
            media_path,
            run_dir / "anchor_evidence",
            instruction=instruction,
            annotations=annotations,
            vision_provider=vision_provider,
            sample_count=sample_count,
        )
        package = fuse_anchor(base, anchor)
        package.write_json(run_dir / "environment_package.json")
        results = compile_package(package, run_dir / "compiled", backend_names, strict=strict)
        self._write_workflow_manifest(run_dir, "anchor2env", package, results)
        return package, results

    def acquire_asset(
        self,
        query: str,
        scout: AssetScout,
        *,
        asset_id: str,
        candidate_index: int = 0,
        target_formats: Iterable[str] = ("usda", "mjcf", "urdf", "sapien_manifest", "metasim_object"),
        smoke_backends: Iterable[str] = (),
    ) -> tuple[AssetCandidate, AssetBundle]:
        run_dir = self.output_root / "assets" / asset_id
        candidates = scout.search(query)
        if not candidates:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "search_failure.json").write_text(
                json.dumps(
                    {
                        "schema": "agenticsim.asset_search_failure.v1",
                        "query": query,
                        "status": "no_candidates",
                        "provider_errors": scout.last_errors,
                        "recovery": "Retry with a saved search_evidence.json via CatalogSearchProvider.",
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(f"no asset candidate found for query {query!r}; provider errors={scout.last_errors}")
        if not 0 <= candidate_index < len(candidates):
            raise IndexError(f"candidate_index {candidate_index} outside {len(candidates)} search results")
        candidate = candidates[candidate_index]
        downloaded = download_candidate(candidate, run_dir / "cache")
        bundle = compile_downloaded_asset(
            downloaded,
            run_dir / "compiled",
            asset_id=asset_id,
            target_formats=target_formats,
        )
        (run_dir / "asset_bundle.json").write_text(
            json.dumps(asdict(bundle), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (run_dir / "search_evidence.json").write_text(
            json.dumps(
                {
                    "schema": "agenticsim.asset_search_evidence.v1",
                    "query": query,
                    "selected": asdict(candidate),
                    "candidate_count": len(candidates),
                    "candidates": [asdict(item) for item in candidates],
                    "provider_errors": scout.last_errors,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        backend_names = tuple(smoke_backends)
        if backend_names:
            package = self._asset_smoke_package(bundle, backend_names)
            package.write_json(run_dir / "import_smoke" / "environment_package.json")
            smoke_results = compile_package(
                package,
                run_dir / "import_smoke" / "compiled",
                backend_names,
                strict=True,
            )
            (run_dir / "import_smoke" / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "agenticsim.asset_import_smoke.v1",
                        "asset_id": bundle.asset_id,
                        "package_digest": package.digest(),
                        "compile_results": {
                            backend: result.to_dict() for backend, result in smoke_results.items()
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        return candidate, bundle

    @staticmethod
    def _asset_smoke_package(bundle: AssetBundle, backends: tuple[str, ...]) -> EnvironmentPackage:
        package = EnvironmentPackage(
            package_id=f"{bundle.asset_id}_import_smoke",
            env=EnvSpec(
                name=f"{bundle.asset_id}_import_smoke",
                objects=(
                    SceneObject(
                        instance_id=bundle.asset_id,
                        asset_id=bundle.asset_id,
                        pose=Pose(position=(0.0, 0.0, 0.25)),
                        scale=(0.1, 0.1, 0.1),
                    ),
                ),
            ),
            assets=(bundle,),
            task=TaskSpec(
                instruction=f"Load and step the imported {bundle.asset_id} asset.",
                intent="asset_import_smoke",
                reset={"object_poses": "from_env_spec"},
                action={"interface": "zero_action", "operations": []},
                observation={"state": ["object_pose", "contact"]},
                plan=(),
                success=({"type": "state_trace_available"},),
                termination=({"type": "timeout", "steps": 20},),
            ),
            source={"mode": "asset_import_smoke", "asset_source": bundle.source},
            target_backends=backends,
        )
        package.validate()
        return package

    def transfer(
        self,
        source_path: str | Path,
        *,
        target_backends: Iterable[str],
        source_backend: str | None = None,
        strict: bool = False,
    ) -> tuple[EnvironmentPackage, dict[str, CompileResult], dict[str, ConformanceReport]]:
        package = import_environment(source_path, source_backend=source_backend)
        run_dir = self.output_root / package.package_id / "transfer"
        package.write_json(run_dir / "imported_environment_package.json")
        results = compile_package(package, run_dir / "compiled", target_backends, strict=strict)
        reports: dict[str, ConformanceReport] = {}
        for backend, result in results.items():
            report = evaluate_conformance(
                package,
                result,
                source_backend=source_backend or str(package.source.get("backend") or "environment_package"),
            )
            report.write_json(run_dir / "conformance" / f"{backend}.json")
            reports[backend] = report
        self._write_workflow_manifest(run_dir, "transfer", package, results)
        return package, results, reports

    @staticmethod
    def _write_workflow_manifest(
        run_dir: Path,
        workflow: str,
        package: EnvironmentPackage,
        results: dict[str, CompileResult],
    ) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "agenticsim.openxsim_workflow.v1",
            "workflow": workflow,
            "package_id": package.package_id,
            "package_digest": package.digest(),
            "source": package.source,
            "anchors": [anchor.anchor_id for anchor in package.anchors],
            "compile_results": {backend: result.to_dict() for backend, result in results.items()},
        }
        (run_dir / "workflow_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

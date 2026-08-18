#!/usr/bin/env python3
"""Inspect and execute the released USG native output contract without fake weights."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def ast_dataclass_fields(path: Path, class_names: set[str]) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    result: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in class_names:
            continue
        fields = []
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                fields.append(
                    {
                        "name": child.target.id,
                        "annotation": ast.unparse(child.annotation),
                        "line": child.lineno,
                    }
                )
        result[node.name] = {
            "file": str(path),
            "line": node.lineno,
            "fields": fields,
        }
    missing = class_names - set(result)
    if missing:
        raise RuntimeError(f"classes absent from {path}: {sorted(missing)}")
    return result


def tensor_shape(value: Any) -> list[int] | None:
    return list(value.shape) if value is not None else None


def tensor_finite(value: Any) -> bool | None:
    if value is None:
        return None
    import torch

    return bool(torch.isfinite(value).all().item())


def released_import_probe(usg_root: Path) -> dict[str, Any]:
    """Exercise the checkout's public import path without changing the environment."""
    environment = dict(os.environ)
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{usg_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(usg_root)
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from usg_par.encoders.types import EncodedModality; print(EncodedModality.__name__)",
        ],
        cwd=usg_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    return {
        "command": [
            sys.executable,
            "-c",
            "from usg_par.encoders.types import EncodedModality; print(EncodedModality.__name__)",
        ],
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "public_import_succeeded": result.returncode == 0,
        "interpretation": (
            "This measures import compatibility in the fixed reproduction environment; "
            "it is not a USG accuracy metric."
        ),
    }


def released_test_collection_probe(usg_root: Path) -> dict[str, Any]:
    """Record the repository's own pytest collection result without treating exit 5 as a crash."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=usg_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    tracked_test_files = [
        path
        for path in git(usg_root, "ls-files").splitlines()
        if Path(path).name.startswith("test") and Path(path).suffix == ".py"
    ]
    return {
        "command": [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "tracked_python_test_files": tracked_test_files,
        "tests_collected": result.returncode == 0 and "no tests collected" not in result.stdout,
        "interpretation": "pytest exit code 5 means no tests were collected",
    }


def install_core_only_encoder_namespace(usg_root: Path) -> None:
    """Load encoders.types without executing encoders/__init__.py's optional imports."""
    package_name = "usg_par.encoders"
    if package_name in sys.modules:
        return
    namespace = types.ModuleType(package_name)
    namespace.__file__ = str(usg_root / "usg_par" / "encoders" / "__init__.py")
    namespace.__package__ = package_name
    namespace.__path__ = [str(usg_root / "usg_par" / "encoders")]
    sys.modules[package_name] = namespace


def core_forward_probe(usg_root: Path, seed: int) -> dict[str, Any]:
    sys.path.insert(0, str(usg_root))
    install_core_only_encoder_namespace(usg_root)
    import torch

    from usg_par.encoders.types import EncodedModality
    from usg_par.model import USGParCore

    torch.set_num_threads(1)
    torch.manual_seed(seed)
    dim = 32
    core = USGParCore(
        modalities=["text", "point"],
        dim=dim,
        num_queries=8,
        num_predicates=6,
        num_scales=3,
        mask_decoder_layers=1,
        rpc_layers=1,
        relation_layers=1,
        top_k=10,
    ).cpu().eval()
    generator = torch.Generator(device="cpu").manual_seed(seed + 1)
    random = lambda *shape: torch.randn(*shape, generator=generator)
    features = {
        "text": EncodedModality(
            feats_per_scale=[random(1, 6, dim) for _ in range(3)],
            context_tokens=random(1, 5, dim),
            is_point=False,
        ),
        "point": EncodedModality(
            feats_per_scale=[random(1, length, dim) for length in (5, 7, 9)],
            context_tokens=random(1, 7, dim),
            is_point=True,
        ),
    }
    class_embeddings = {
        "text": random(4, dim),
        "point": random(4, dim),
    }
    started = time.perf_counter()
    with torch.no_grad():
        first = core(features, class_embeddings)
        second = core(features, class_embeddings)
    elapsed = time.perf_counter() - started
    per_modality: dict[str, Any] = {}
    deterministic = True
    for modality, output in first.per_modality.items():
        other = second.per_modality[modality]
        tensors = {
            "refined_query": output.refined_query,
            "mask_logits": output.mask_logits,
            "fused_query": output.fused_query,
            "cls_logits": output.cls_logits,
            "pred_masks": output.pred_masks,
            "relation_logits": output.relation_logits,
            "rpc_pair_confidence": output.rpc_out.pair_confidence,
            "rpc_sub_idx": output.rpc_out.sub_idx,
            "rpc_obj_idx": output.rpc_out.obj_idx,
            "rpc_scores": output.rpc_out.scores,
        }
        other_tensors = {
            "refined_query": other.refined_query,
            "mask_logits": other.mask_logits,
            "fused_query": other.fused_query,
            "cls_logits": other.cls_logits,
            "pred_masks": other.pred_masks,
            "relation_logits": other.relation_logits,
            "rpc_pair_confidence": other.rpc_out.pair_confidence,
            "rpc_sub_idx": other.rpc_out.sub_idx,
            "rpc_obj_idx": other.rpc_out.obj_idx,
            "rpc_scores": other.rpc_out.scores,
        }
        equality: dict[str, bool | None] = {}
        for name, tensor in tensors.items():
            comparison = other_tensors[name]
            equality[name] = None if tensor is None else bool(torch.equal(tensor, comparison))
            if equality[name] is False:
                deterministic = False
        per_modality[modality] = {
            "shapes": {name: tensor_shape(tensor) for name, tensor in tensors.items()},
            "finite": {name: tensor_finite(tensor) for name, tensor in tensors.items()},
            "repeat_exactly_equal": equality,
        }
    associations = {
        key: {
            "shape": tensor_shape(value),
            "finite": tensor_finite(value),
            "repeat_exactly_equal": bool(torch.equal(value, second.associations[key])),
        }
        for key, value in first.associations.items()
    }
    deterministic = deterministic and all(
        item["repeat_exactly_equal"] for item in associations.values()
    )
    return {
        "purpose": "executable shape/field probe only; random initialization has no semantic meaning",
        "semantic_accuracy_interpretation_allowed": False,
        "device": "cpu",
        "seed": seed,
        "torch_version": torch.__version__,
        "elapsed_seconds_for_two_forwards": elapsed,
        "parameter_count": sum(parameter.numel() for parameter in core.parameters()),
        "per_modality": per_modality,
        "associations": associations,
        "repeat_forward_exactly_equal": deterministic,
    }


def tracked_manifest(repo: Path) -> tuple[list[dict[str, Any]], str]:
    files = git(repo, "ls-files", "-z").split("\0")
    files = [value for value in files if value]
    rows = [
        {
            "path": relative,
            "bytes": (repo / relative).stat().st_size,
            "sha256": sha256_file(repo / relative),
        }
        for relative in files
    ]
    manifest_text = "".join(f"{row['sha256']}  {row['path']}\n" for row in rows)
    manifest_path = ROOT / "audit" / "usg-tracked-files.sha256"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest_text, encoding="utf-8")
    return rows, hashlib.sha256(manifest_text.encode()).hexdigest()


def source_token_counts(repo: Path, terms: Iterable[str]) -> dict[str, Any]:
    source_files = [path for path in repo.rglob("*.py") if ".git" not in path.parts]
    result: dict[str, Any] = {}
    for term in terms:
        occurrences = []
        for path in source_files:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if term.lower() in line.lower():
                    occurrences.append(
                        {
                            "path": str(path.relative_to(repo)),
                            "line": line_number,
                            "text": line.strip()[:240],
                        }
                    )
        result[term] = {"count": len(occurrences), "occurrences": occurrences}
    return result


def coverage_rows() -> list[dict[str, Any]]:
    return [
        {
            "required_capability": "object categories and instances",
            "native_explicit_output": "cls_logits, pred_masks",
            "native_coverage": "yes",
            "relevance": "can improve semantic grounding if trained weights are available",
        },
        {
            "required_capability": "semantic relations",
            "native_explicit_output": "relation_logits",
            "native_coverage": "yes",
            "relevance": "can propose semantic predicates, subject to accuracy",
        },
        {
            "required_capability": "cross-modal object identity",
            "native_explicit_output": "associations",
            "native_coverage": "yes",
            "relevance": "aligns object queries across input modalities",
        },
        {
            "required_capability": "metric world pose and complete occupied volume",
            "native_explicit_output": "none",
            "native_coverage": "no",
            "relevance": "required for final-qpos collision checks",
        },
        {
            "required_capability": "articulation joint state or qpos",
            "native_explicit_output": "none",
            "native_coverage": "no",
            "relevance": "required for open-drawer geometry",
        },
        {
            "required_capability": "collision/contact/penetration evidence",
            "native_explicit_output": "none",
            "native_coverage": "no",
            "relevance": "required to reject forbidden contact",
        },
        {
            "required_capability": "mass, friction, gravity, CoM, support polygon",
            "native_explicit_output": "none",
            "native_coverage": "no",
            "relevance": "required for stable physical support",
        },
        {
            "required_capability": "simulator body/link to logical-object ownership",
            "native_explicit_output": "none",
            "native_coverage": "no",
            "relevance": "required for evidence closure",
        },
        {
            "required_capability": "failed-scene repair or constrained rearrangement",
            "native_explicit_output": "none",
            "native_coverage": "no",
            "relevance": "required to close generation-validation loop",
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_json(args.config.resolve())
    usg_root = Path(config["usg_root"])
    gen_env_root = Path(config["gen_env_root"])
    robotwin_root = Path(config["robotwin_root"])

    model_contract = ast_dataclass_fields(
        usg_root / "usg_par" / "model.py", {"ModalityOutput", "USGOutput"}
    )
    rpc_contract = ast_dataclass_fields(usg_root / "usg_par" / "rpc.py", {"RPCOutput"})
    public_import = released_import_probe(usg_root)
    test_collection = released_test_collection_probe(usg_root)
    forward = core_forward_probe(usg_root, int(config["random_seed"]))
    tracked, manifest_sha = tracked_manifest(usg_root)
    weight_suffixes = {".pt", ".pth", ".ckpt", ".safetensors"}
    present_weight_files = sorted(
        str(path.relative_to(usg_root))
        for path in usg_root.rglob("*")
        if path.is_file() and path.suffix.lower() in weight_suffixes
    )
    tracked_weight_files = [
        row["path"] for row in tracked if Path(row["path"]).suffix.lower() in weight_suffixes
    ]
    releases = load_json(ROOT / "source" / "usg" / "github-releases.json")
    issues = load_json(ROOT / "source" / "usg" / "github-issues.json")
    issue_7 = next((item for item in issues if item.get("number") == 7), None)
    coverage = coverage_rows()
    write_csv(ROOT / "data" / "raw" / "usg_native_coverage.csv", coverage)

    probe = {
        "schema_version": "usg_env_quality_reproduction.usg_contract.v1",
        "scope": "released native output contract; does not constrain future external adapters",
        "repository": {
            "path": str(usg_root),
            "head": git(usg_root, "rev-parse", "HEAD"),
            "tree": git(usg_root, "rev-parse", "HEAD^{tree}"),
            "head_commit_date": git(usg_root, "show", "-s", "--format=%aI", "HEAD"),
            "status_porcelain": git(usg_root, "status", "--porcelain"),
            "tracked_file_count": len(tracked),
            "tracked_manifest_sha256": manifest_sha,
        },
        "native_output_contract": {**model_contract, **rpc_contract},
        "released_public_import_probe": public_import,
        "released_test_collection_probe": test_collection,
        "executable_core_probe": forward,
        "checkpoint_availability": {
            "weight_files_present_in_checkout": present_weight_files,
            "weight_files_tracked_by_git": tracked_weight_files,
            "github_release_count": len(releases),
            "tracked_dependency_manifest_files": [
                row["path"]
                for row in tracked
                if Path(row["path"]).name
                in {
                    "environment.yml",
                    "environment.yaml",
                    "pyproject.toml",
                    "requirements.txt",
                    "setup.cfg",
                    "setup.py",
                }
            ],
            "trained_usg_checkpoint_available": bool(
                present_weight_files or tracked_weight_files or releases
            ),
            "semantic_accuracy_evaluable_from_released_artifacts": False,
            "reason": (
                "no trained USG-Par checkpoint is present or released; encoder initialization "
                "is not a trained USG scene-graph model"
            ),
        },
        "public_reproduction_signal": {
            "issue_7_present": issue_7 is not None,
            "issue_7_state": issue_7.get("state") if issue_7 else None,
            "issue_7_comment_count": issue_7.get("comments") if issue_7 else None,
            "issue_7_is_third_party_claim_not_primary_performance_evidence": True,
            "issue_7_reported_body": issue_7.get("body") if issue_7 else None,
        },
        "source_token_audit": source_token_counts(
            usg_root,
            [
                "qpos",
                "collision",
                "penetration",
                "friction",
                "impulse",
                "support_polygon",
                "center_of_mass",
            ],
        ),
        "native_coverage_rows": coverage,
        "inference_boundary": {
            "supported": (
                "the released native USG output does not itself implement final-qpos geometry, "
                "physics validation, body ownership, or repair"
            ),
            "not_supported": (
                "USG can never help, or no adapter can convert trained semantic outputs into "
                "inputs for a separate physics-aware solver"
            ),
        },
    }
    write_json(ROOT / "data" / "raw" / "usg_contract_probe.json", probe)

    inputs = []
    input_paths = [
        args.config.resolve(),
        Path(config["paper_path"]),
        Path(config["catalog_path"]),
        ROOT / "source" / "paper" / "arxiv-2503.15005.atom",
        ROOT / "source" / "usg" / "github-repository.json",
        ROOT / "source" / "usg" / "github-releases.json",
        ROOT / "source" / "usg" / "github-issues.json",
        usg_root / "README.md",
        usg_root / "usg_par" / "model.py",
        usg_root / "usg_par" / "rpc.py",
        gen_env_root / "scene_gen" / "solver.py",
        gen_env_root / "scene_gen" / "validator.py",
        gen_env_root / "script" / "run_scene_runtime.py",
        robotwin_root / config["cabinet"]["urdf_relative_path"],
        robotwin_root / config["cabinet"]["metadata_relative_path"],
        robotwin_root / config["basket"]["collision_relative_path"],
        robotwin_root / config["basket"]["visual_relative_path"],
    ]
    for path in input_paths:
        inputs.append(
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    inventory = {
        "created_by": str(Path(__file__).resolve()),
        "python": platform.python_version(),
        "gen_env_head": git(gen_env_root, "rev-parse", "HEAD"),
        "gen_env_status_porcelain": git(gen_env_root, "status", "--porcelain"),
        "robotwin_head": git(robotwin_root, "rev-parse", "HEAD"),
        "robotwin_status_porcelain": git(robotwin_root, "status", "--porcelain"),
        "usg_head": git(usg_root, "rev-parse", "HEAD"),
        "inputs": inputs,
    }
    write_json(ROOT / "audit" / "input-source-inventory.json", inventory)
    print(
        f"PASS head={probe['repository']['head'][:12]} fields="
        f"{sum(len(item['fields']) for item in probe['native_output_contract'].values())} "
        f"trained_checkpoint={probe['checkpoint_availability']['trained_usg_checkpoint_available']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

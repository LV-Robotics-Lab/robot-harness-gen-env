"""Generated-asset admission into the versioned asset-ledger library."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

from scene_gen.catalog import AssetCatalog, CatalogEntry, CatalogModel
from scene_gen.schema import SceneSpec


class AssetAdmissionError(RuntimeError):
    """A generated asset could not be validated or atomically promoted."""


@dataclass(frozen=True)
class GeneratedAssetAdmitter:
    """Promote deterministic generated assets with an honest v3 ledger."""

    library_root: Path
    admission_date: date

    def admit(
        self,
        *,
        scene_spec: SceneSpec,
        asset_catalog: AssetCatalog,
        generation_report: dict[str, Any],
    ) -> tuple[AssetCatalog, dict[str, Any]]:
        contract = _ledger_contract()
        entries = {entry.asset_id: entry for entry in asset_catalog.entries}
        admitted: list[dict[str, Any]] = []
        updated: dict[str, CatalogEntry] = {}
        for provenance in generation_report.get("generated", []):
            asset_id = provenance["asset_id"]
            entry = entries.get(asset_id)
            if entry is None:
                raise AssetAdmissionError(f"generated catalog entry is missing: {asset_id}")
            destination = self.library_root / "generated" / asset_id
            disposition = self._promote(
                scene_spec=scene_spec,
                entry=entry,
                provenance=provenance,
                destination=destination,
                contract=contract,
            )
            ledger_path = destination / "ledger.json"
            updated[asset_id] = _relocate_entry(entry, destination)
            admitted.append(
                {
                    "asset_id": asset_id,
                    "ledger_path": str(ledger_path.resolve()),
                    "ledger_sha256": _sha256(ledger_path),
                    "disposition": disposition,
                    "qualification": "generation_qc_only",
                }
            )

        effective = asset_catalog.model_copy(
            update={
                "objects_root": str(self.library_root.resolve()),
                "entries": tuple(
                    updated.get(entry.asset_id, entry) for entry in asset_catalog.entries
                ),
            }
        )
        dispositions = {item["disposition"] for item in admitted}
        status = (
            "not_needed"
            if not admitted
            else next(iter(dispositions))
            if len(dispositions) == 1
            else "mixed"
        )
        return effective, {
            "schema_version": "harness.generated_asset_admission.v1",
            "scene_id": scene_spec.scene_id,
            "status": status,
            "assets": admitted,
            "asset_catalog_sha256": effective.digest(),
            "physical_qualification": "pending_settle",
        }

    def _promote(
        self,
        *,
        scene_spec: SceneSpec,
        entry: CatalogEntry,
        provenance: dict[str, Any],
        destination: Path,
        contract: ModuleType,
    ) -> str:
        if destination.exists():
            _verify_existing(destination, provenance, contract)
            return "reused"

        source = Path(entry.asset_path).resolve()
        incoming_root = self.library_root / ".incoming"
        incoming_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f"{entry.asset_id}.", dir=incoming_root))
        try:
            shutil.copytree(source, temporary, dirs_exist_ok=True)
            ledger = _build_ledger(
                scene_spec=scene_spec,
                entry=entry,
                provenance=provenance,
                destination=destination,
                source_root=temporary,
                admission_date=self.admission_date,
                contract=contract,
            )
            structural = contract.validate_ledger(ledger, check_files=False)
            if structural:
                raise AssetAdmissionError(_violation_summary(structural))
            contract.write_ledger(temporary / "ledger.json", ledger)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, destination)
            complete = contract.validate_ledger(ledger, check_files=True)
            if complete:
                rejected = self.library_root / ".rejected" / destination.name
                rejected.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, rejected)
                raise AssetAdmissionError(_violation_summary(complete))
            return "admitted"
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def _ledger_contract() -> ModuleType:
    return importlib.import_module(
        "self_improving.asset_pipeline.active.1_asset_reuse.lib.ledger"
    )


def _build_ledger(
    *,
    scene_spec: SceneSpec,
    entry: CatalogEntry,
    provenance: dict[str, Any],
    destination: Path,
    source_root: Path,
    admission_date: date,
    contract: ModuleType,
) -> dict[str, Any]:
    model = entry.models[0]
    dimensions = list(provenance["dimensions_m"])
    representations = [
        _representation(
            destination / "visual" / "textured0.obj",
            source_root / "visual" / "textured0.obj",
            role="visual",
            collision=False,
        ),
        _representation(
            destination / "collision" / "textured0.obj",
            source_root / "collision" / "textured0.obj",
            role="collision",
            collision=True,
        ),
    ]
    model_entry = contract.new_model_entry(
        model=model.model_id,
        representations=representations,
        mesh_bbox_m=dimensions,
        size_resolution={
            "mode": "generated_exact",
            "actual_max_dim_m": max(dimensions),
            "scale": 1.0,
            "reference_max_dim_m": None,
            "reference_assets": [],
            "verdict": "generated",
        },
        conventions={
            "is_static": False,
            "z_policy": "origin_on_table",
            "footprint_shape": "box",
            "stable_poses": [
                {
                    "pose_id": "procedural_flat_base",
                    "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                    "is_default": True,
                    "measured_against": {
                        "backend": "portable",
                        "run_id": provenance["generator"],
                        "note": "analytic flat base from deterministic generator geometry",
                    },
                }
            ],
            "inherited_from": None,
        },
        source={
            "kind": "generated",
            "generator": {
                "tool": "scene_gen.asset_generator",
                "tool_version": provenance["generator"],
                "model": "deterministic_procedural_proxy",
                "model_version": provenance["generator"],
                "input": {"type": "text", "prompt": scene_spec.request},
                "seed": scene_spec.seed,
                "params": {
                    "generation_kind": provenance["generation_kind"],
                    "geometry_family": provenance["geometry_family"],
                    "dimensions_m": dimensions,
                },
                "generated_at": admission_date.isoformat(),
            },
            "license": {
                "spdx": "Apache-2.0",
                "status": "declared",
                "terms_note": "deterministic geometry generated by this Apache-2.0 project",
            },
        },
        verification=[],
    )
    model_entry["verification"].append(
        {
            "backend": "sapien",
            "check": "generation_qc",
            "verdict": "pass",
            "run_id": scene_spec.scene_id,
            "timestamp": f"{admission_date.isoformat()}T00:00:00",
            "verified_digest": contract.reps_digest(model_entry, "sapien"),
        }
    )
    ledger = contract.upsert_model(
        None,
        asset=entry.asset_id,
        category=entry.category,
        kind="rigid",
        profile="sapien_only",
        identity={
            "basis": "requested_by_acquire",
            "verified": False,
            "evidence": "generation_provenance.json",
        },
        aliases=entry.aliases or (entry.category,),
        colors=entry.colors,
        materials=entry.materials,
        tags=(),
        model_entry=model_entry,
        asset_id_prefix="generated",
    )
    ledger["external_ids"] = {"env_gen": entry.asset_id}
    return ledger


def _representation(
    path: Path,
    source_path: Path,
    *,
    role: str,
    collision: bool,
) -> dict[str, Any]:
    digest = _sha256(source_path)
    size = source_path.stat().st_size
    record: dict[str, Any] = {
        "format": "obj",
        "uri": str(path.resolve()),
        "backend": "sapien",
        "role": role,
        "sha256": digest,
        "size_bytes": size,
        "metadata": {"generator_owned": True},
        "frame": {"up_axis": "Z"},
        "geometry_state": {"scale_baked": True, "origin": "bottom-center"},
        "files": [
            {
                "uri": str(path.resolve()),
                "sha256": digest,
                "bytes": size,
            }
        ],
    }
    if collision:
        record["collision_meta"] = {"mode": "explicit_mesh", "convex": False}
    return record


def _relocate_entry(entry: CatalogEntry, destination: Path) -> CatalogEntry:
    models: list[CatalogModel] = []
    for model in entry.models:
        models.append(
            model.model_copy(
                update={
                    "model_path": str(destination.resolve()),
                    "metadata_path": str((destination / "model_data0.json").resolve()),
                    "visual_path": str(
                        (destination / "visual" / "textured0.obj").resolve()
                    ),
                    "collision_path": str(
                        (destination / "collision" / "textured0.obj").resolve()
                    ),
                }
            )
        )
    return entry.model_copy(
        update={"asset_path": str(destination.resolve()), "models": tuple(models)}
    )


def _verify_existing(destination: Path, provenance: dict[str, Any], contract: ModuleType) -> None:
    ledger_path = destination / "ledger.json"
    if not ledger_path.is_file():
        raise AssetAdmissionError(f"existing asset has no ledger: {destination}")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    violations = contract.validate_ledger(ledger, check_files=True)
    if violations:
        raise AssetAdmissionError(_violation_summary(violations))
    if ledger.get("external_ids", {}).get("env_gen") != provenance["asset_id"]:
        raise AssetAdmissionError("existing ledger identity does not match generated asset")


def _violation_summary(violations: list[Any]) -> str:
    return "; ".join(f"{item.path}:{item.code}" for item in violations[:8])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

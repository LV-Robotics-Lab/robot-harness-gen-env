"""Generated-asset admission into the versioned asset-ledger library."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import secrets
import shutil
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

from scene_gen.asset_generator import (
    PROXY_GENERATOR_VERSION,
    SCALE_GENERATOR_VERSION,
    SCALE_HEADROOM,
)
from scene_gen.catalog import AssetCatalog, CatalogEntry, CatalogModel
from scene_gen.schema import RelationType, SceneSpec


class AssetAdmissionError(RuntimeError):
    """A generated asset could not be validated or atomically promoted."""


_PROVENANCE_SCHEMA = "robotwin.generated_asset_provenance.v1"
_GENERATION_REPORT_SCHEMA = "robotwin.asset_generation_report.v1"
_COMMON_PROVENANCE_FIELDS = frozenset(
    {
        "asset_id",
        "dimensions_m",
        "files",
        "generated_license",
        "generation_kind",
        "generator",
        "geometry_family",
        "geometry_fidelity",
        "requested_color",
        "requested_material",
        "schema_version",
        "semantic_category",
    }
)
_DERIVED_PROVENANCE_FIELDS = frozenset(
    {
        "adaptation_reasons",
        "aliases",
        "compatibility",
        "materials",
        "semantic_name",
        "source_asset_id",
        "source_dimensions_m",
        "source_model_id",
        "uniform_scale_factor",
    }
)
_COMPATIBILITY_FIELDS = frozenset(
    {
        "headroom_fraction",
        "relation",
        "source_runtime_probe",
        "target_asset_id",
        "target_model_id",
    }
)
_GENERATOR_BY_KIND = {
    "procedural_proxy": PROXY_GENERATOR_VERSION,
    "derived_scaled_proxy": SCALE_GENERATOR_VERSION,
}
_GEOMETRY_FIDELITY_BY_KIND = {
    "procedural_proxy": "semantic_shape_when_recognized_otherwise_bounded_proxy",
    "derived_scaled_proxy": "primitive_proxy_preserving_uniform_source_dimensions",
}
_GEOMETRY_FAMILIES = frozenset(
    {
        "bounded_box_proxy",
        "cylindrical_proxy",
        "hexagonal_prism",
        "octagonal_prism",
        "rectangular_pedestal",
    }
)
_DERIVED_ADAPTATION_REASONS = frozenset(
    {"nested_geometry_incompatible", "source_runtime_instability"}
)
_DERIVED_RELATIONS = frozenset(
    {RelationType.ON_TABLE.value, RelationType.ON_TOP_OF.value, RelationType.INSIDE.value}
)
_SEMANTIC_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REQUEST_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_GENERATED_FILES = frozenset(
    {
        "collision/textured0.obj",
        "generation_provenance.json",
        "model_data0.json",
        "visual/material.mtl",
        "visual/textured0.obj",
    }
)


@dataclass(frozen=True)
class _PromotionResult:
    disposition: str
    ledger_sha256: str


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
        library_root = self.library_root.absolute()
        # Establish the root before deriving any child locator.  The fd walk
        # rejects symlinks in every existing component rather than erasing
        # them with Path.resolve().
        with _safe_directory(library_root, create=True):
            pass
        _require_generation_report(generation_report, scene_spec)
        entries: dict[str, CatalogEntry] = {}
        for entry in asset_catalog.entries:
            _require_safe_asset_id(entry.asset_id)
            if entry.asset_id in entries:
                raise AssetAdmissionError(f"asset catalog duplicates asset_id: {entry.asset_id}")
            entries[entry.asset_id] = entry
        generated = generation_report.get("generated", [])
        generated_ids = []
        for item in generated:
            asset_id = item.get("asset_id")
            _require_safe_asset_id(asset_id)
            generated_ids.append(asset_id)
        if len(generated_ids) != len(set(generated_ids)):
            raise AssetAdmissionError("generation report duplicates asset_id")
        admitted: list[dict[str, Any]] = []
        updated: dict[str, CatalogEntry] = {}
        for provenance in generated:
            asset_id = provenance["asset_id"]
            entry = entries.get(asset_id)
            if entry is None:
                raise AssetAdmissionError(f"generated catalog entry is missing: {asset_id}")
            destination = library_root / "generated" / asset_id
            promotion = self._promote(
                scene_spec=scene_spec,
                entry=entry,
                provenance=provenance,
                destination=destination,
                contract=contract,
                catalog_root=Path(asset_catalog.objects_root),
            )
            ledger_path = destination / "ledger.json"
            updated[asset_id] = _relocate_entry(entry, destination)
            admitted.append(
                {
                    "asset_id": asset_id,
                    "ledger_path": str(ledger_path.resolve()),
                    "ledger_sha256": promotion.ledger_sha256,
                    "disposition": promotion.disposition,
                    "qualification": "generation_qc_only",
                }
            )

        effective = asset_catalog.model_copy(
            update={
                "objects_root": str(library_root),
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
        catalog_root: Path,
    ) -> _PromotionResult:
        source = _validate_generation_inputs(
            scene_spec=scene_spec,
            entry=entry,
            provenance=provenance,
            catalog_root=catalog_root,
        )
        library_root = self.library_root.absolute()
        incoming_root = library_root / ".incoming"
        generated_root = library_root / "generated"
        with (
            _safe_directory(library_root, create=True) as library_fd,
            _safe_child_directory(library_fd, ".incoming", create=True) as incoming_fd,
            _safe_child_directory(library_fd, "generated", create=True) as generated_fd,
        ):
            _require_directory_identity(library_root, library_fd)
            _require_directory_identity(incoming_root, incoming_fd)
            _require_directory_identity(generated_root, generated_fd)
            try:
                status = os.stat(entry.asset_id, dir_fd=generated_fd, follow_symlinks=False)
            except FileNotFoundError:
                status = None
            if status is not None:
                if not stat.S_ISDIR(status.st_mode):
                    raise AssetAdmissionError("generated destination is not a safe directory")
                with _safe_child_directory(
                    generated_fd, entry.asset_id, create=False
                ) as destination_fd:
                    _require_directory_identity(destination, destination_fd)
                    verified_manifest = _verify_existing(
                        destination=destination,
                        source=source,
                        scene_spec=scene_spec,
                        entry=entry,
                        provenance=provenance,
                        contract=contract,
                    )
                    _require_directory_identity(destination, destination_fd)
                    _require_tree_manifest(
                        Path(f"/proc/self/fd/{destination_fd}"),
                        verified_manifest,
                        ignore={"ledger.lock"},
                    )
                    _require_directory_identity(destination, destination_fd)
                _require_directory_identity(generated_root, generated_fd)
                return _PromotionResult(
                    disposition="reused",
                    ledger_sha256=verified_manifest["ledger.json"]["sha256"],
                )

            temporary_name = _make_private_directory(incoming_fd, f"{entry.asset_id}.")
            temporary_fd = os.open(
                temporary_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=incoming_fd,
            )
            temporary = Path(f"/proc/self/fd/{temporary_fd}")
            published = False
            try:
                source_manifest = _tree_manifest(source)
                shutil.copytree(source, temporary, dirs_exist_ok=True)
                copied_manifest = _tree_manifest(temporary)
                if copied_manifest != source_manifest:
                    raise AssetAdmissionError("generated source changed while it was staged")
                published_provenance = _publishable_provenance(provenance)
                _write_json_object(
                    temporary / "generation_provenance.json",
                    published_provenance,
                )
                staged_manifest = _tree_manifest(temporary)
                if frozenset(staged_manifest) != _EXPECTED_GENERATED_FILES:
                    raise AssetAdmissionError(
                        "generated asset tree has missing or unexpected files"
                    )
                if (
                    _load_json_object(temporary / "generation_provenance.json")
                    != published_provenance
                ):
                    raise AssetAdmissionError("staged generation provenance changed after rewrite")
                _validate_provenance_files(
                    published_provenance,
                    temporary,
                    staged_manifest,
                    relocated=True,
                )
                # Run the expensive file/closure gate against the private staging
                # tree.  The asset must not become visible in the library and then
                # be moved aside on failure: even a short publication window lets
                # another compiler select bytes that never passed admission.
                staged_ledger = _build_ledger(
                    scene_spec=scene_spec,
                    entry=entry,
                    provenance=published_provenance,
                    destination=temporary,
                    source_root=temporary,
                    admission_date=self.admission_date,
                    contract=contract,
                )
                complete = contract.validate_ledger(staged_ledger, check_files=True)
                if complete:
                    raise AssetAdmissionError(_violation_summary(complete))

                # The file gate above is the last point that may derive identity
                # from bytes.  Final-ledger construction is a pure locator rewrite
                # over that gated document; it never re-hashes a mutable tree.
                ledger = _relocate_ledger(staged_ledger, temporary, destination, contract)
                structural = contract.validate_ledger(ledger, check_files=False)
                if structural:
                    raise AssetAdmissionError(_violation_summary(structural))
                contract.write_ledger_at(temporary_fd, ledger)
                (temporary / "ledger.lock").unlink(missing_ok=True)
                _require_tree_manifest(temporary, staged_manifest, ignore={"ledger.json"})
                ledger_sha256 = _sha256(temporary / "ledger.json")
                _require_directory_identity(library_root, library_fd)
                _require_directory_identity(incoming_root, incoming_fd)
                _require_directory_identity(generated_root, generated_fd)
                os.rename(
                    temporary_name,
                    entry.asset_id,
                    src_dir_fd=incoming_fd,
                    dst_dir_fd=generated_fd,
                )
                published = True
                try:
                    _require_directory_identity(generated_root, generated_fd)
                    try:
                        _require_directory_identity(destination, temporary_fd)
                    except AssetAdmissionError as exc:
                        raise AssetAdmissionError(
                            "published destination identity changed during admission"
                        ) from exc
                    _require_tree_manifest(temporary, staged_manifest, ignore={"ledger.json"})
                    try:
                        _require_directory_identity(destination, temporary_fd)
                    except AssetAdmissionError as exc:
                        raise AssetAdmissionError(
                            "published destination identity changed during admission"
                        ) from exc
                except Exception:
                    # Remove only the child inode this admission published. A
                    # replaced path must never redirect cleanup into another
                    # writer's or an attacker-controlled directory.
                    _remove_child_directory_if_identity(
                        generated_fd,
                        entry.asset_id,
                        expected_fd=temporary_fd,
                    )
                    raise
                return _PromotionResult(
                    disposition="admitted",
                    ledger_sha256=ledger_sha256,
                )
            finally:
                os.close(temporary_fd)
                if not published:
                    shutil.rmtree(
                        Path(f"/proc/self/fd/{incoming_fd}") / temporary_name,
                        ignore_errors=True,
                    )


@contextmanager
def _safe_directory(path: Path, *, create: bool):
    """Open an absolute directory path one no-follow component at a time."""

    path = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path.anchor or "/", flags)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                child = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                raise AssetAdmissionError(
                    f"safe directory path rejects symlink or non-directory component: {path}"
                ) from exc
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _safe_child_directory(parent_fd: int, name: str, *, create: bool):
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise AssetAdmissionError(f"safe directory rejects symlink: {name}") from exc
    try:
        yield fd
    finally:
        os.close(fd)


def _require_directory_identity(path: Path, fd: int) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise AssetAdmissionError(f"safe directory changed during admission: {path}") from exc
    opened = os.fstat(fd)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        raise AssetAdmissionError(f"safe directory changed during admission: {path}")


def _remove_child_directory_if_identity(parent_fd: int, name: str, *, expected_fd: int) -> None:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        expected = os.fstat(expected_fd)
    except OSError:
        return
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        expected.st_dev,
        expected.st_ino,
    ):
        return
    shutil.rmtree(Path(f"/proc/self/fd/{parent_fd}") / name, ignore_errors=True)


def _make_private_directory(parent_fd: int, prefix: str) -> str:
    for _ in range(100):
        name = f"{prefix}{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        return name
    raise AssetAdmissionError("could not create private incoming directory")


def _ledger_contract() -> ModuleType:
    return importlib.import_module("self_improving.asset_pipeline.active.asset_reuse.lib.ledger")


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
    provenance_path = source_root / "generation_provenance.json"
    provenance_sha256 = _sha256(provenance_path)
    representations = [
        _representation(
            destination / "visual" / "textured0.obj",
            source_root / "visual" / "textured0.obj",
            contract=contract,
            role="visual",
            collision=False,
        ),
        _representation(
            destination / "collision" / "textured0.obj",
            source_root / "collision" / "textured0.obj",
            contract=contract,
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
                        "evidence_schema": _PROVENANCE_SCHEMA,
                        "evidence_sha256": provenance_sha256,
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
                    "generation_provenance_schema": _PROVENANCE_SCHEMA,
                    "generation_provenance_sha256": provenance_sha256,
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
            "report_path": contract.to_portable_uri(destination / "generation_provenance.json"),
            "report_sha256": provenance_sha256,
            "report_schema": _PROVENANCE_SCHEMA,
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
    contract: ModuleType,
    role: str,
    collision: bool,
) -> dict[str, Any]:
    digest = _sha256(source_path)
    # The deterministic visual OBJ references material.mtl.  Publishing only
    # the primary OBJ made files[] look complete while the loader still read
    # an unbound sibling.  Snapshot every regular member under this generated
    # representation directory and let the ledger closure validator prove the
    # OBJ/MTL graph before admission.
    members = [member for member in sorted(source_path.parent.rglob("*")) if member.is_file()]
    files = []
    for member in members:
        relative = member.relative_to(source_path.parent)
        installed = path.parent / relative
        files.append(
            {
                "uri": contract.to_portable_uri(installed),
                "sha256": _sha256(member),
                "bytes": member.stat().st_size,
            }
        )
    record: dict[str, Any] = {
        "format": "obj",
        "uri": contract.to_portable_uri(path),
        "backend": "sapien",
        "role": role,
        "sha256": digest,
        "metadata": {"generator_owned": True},
        "frame": {"up_axis": "Z"},
        "geometry_state": {"scale_baked": True, "origin": "bottom-center"},
        "files": sorted(files, key=lambda member: member["uri"]),
    }
    if collision:
        record["collision_meta"] = {"mode": "explicit_mesh"}
    return record


def _relocate_entry(entry: CatalogEntry, destination: Path) -> CatalogEntry:
    models: list[CatalogModel] = []
    for model in entry.models:
        models.append(
            model.model_copy(
                update={
                    "model_path": str(destination.resolve()),
                    "metadata_path": str((destination / "model_data0.json").resolve()),
                    "visual_path": str((destination / "visual" / "textured0.obj").resolve()),
                    "collision_path": str((destination / "collision" / "textured0.obj").resolve()),
                }
            )
        )
    return entry.model_copy(
        update={"asset_path": str(destination.resolve()), "models": tuple(models)}
    )


def _verify_existing(
    *,
    destination: Path,
    source: Path,
    scene_spec: SceneSpec,
    entry: CatalogEntry,
    provenance: dict[str, Any],
    contract: ModuleType,
) -> dict[str, dict[str, Any]]:
    ledger_path = destination / "ledger.json"
    if not ledger_path.is_file():
        raise AssetAdmissionError(f"existing asset has no ledger: {destination}")
    ledger_bytes = ledger_path.read_bytes()
    ledger = json.loads(ledger_bytes)
    violations = contract.validate_ledger(ledger, check_files=True)
    if violations:
        raise AssetAdmissionError(_violation_summary(violations))
    if ledger.get("external_ids", {}).get("env_gen") != provenance["asset_id"]:
        raise AssetAdmissionError("existing ledger identity does not match generated asset")
    installed_provenance = _load_json_object(destination / "generation_provenance.json")
    _require_provenance_shape(installed_provenance)
    installed_manifest = _tree_manifest(destination, ignore={"ledger.json", "ledger.lock"})
    _validate_provenance_files(
        installed_provenance,
        destination,
        installed_manifest,
        relocated=True,
    )
    if _provenance_identity(installed_provenance) != _provenance_identity(provenance):
        raise AssetAdmissionError("installed generation provenance does not match this run")
    ignored = {"generation_provenance.json"}
    if _tree_manifest(source, ignore=ignored) != _tree_manifest(
        destination, ignore={"ledger.json", "ledger.lock", *ignored}
    ):
        raise AssetAdmissionError("existing generated payload does not match this run")
    expected = _build_ledger(
        scene_spec=scene_spec,
        entry=entry,
        provenance=installed_provenance,
        destination=destination,
        source_root=destination,
        admission_date=date.fromisoformat(
            ledger["models"][0]["source"]["generator"]["generated_at"]
        ),
        contract=contract,
    )
    if _ledger_without_verification(ledger) != _ledger_without_verification(expected):
        raise AssetAdmissionError("existing ledger does not match this generation request")
    expected_generation_qc = expected["models"][0]["verification"][0]
    actual_verification = ledger["models"][0].get("verification")
    if (
        not isinstance(actual_verification, list)
        or expected_generation_qc not in actual_verification
    ):
        raise AssetAdmissionError(
            "existing ledger generation_qc does not match this generation request"
        )
    return {
        "ledger.json": {
            "sha256": hashlib.sha256(ledger_bytes).hexdigest(),
            "bytes": len(ledger_bytes),
        },
        **installed_manifest,
    }


def _provenance_identity(provenance: dict[str, Any]) -> dict[str, Any]:
    """Return the generation claim with ephemeral staging locators removed."""
    normalized = json.loads(json.dumps(provenance))
    files = normalized.get("files")
    if isinstance(files, dict):
        for record in files.values():
            if isinstance(record, dict) and isinstance(record.get("path"), str):
                record["path"] = Path(record["path"]).name
    return normalized


def _publishable_provenance(provenance: dict[str, Any]) -> dict[str, Any]:
    """Replace validated staging locators with paths inside the published asset."""
    normalized = json.loads(json.dumps(provenance))
    files = normalized["files"]
    relative_paths = {
        "collision": "collision/textured0.obj",
        "material": "visual/material.mtl",
        "metadata": "model_data0.json",
        "visual": "visual/textured0.obj",
    }
    for key, relative_path in relative_paths.items():
        files[key]["path"] = relative_path
    return normalized


def _write_json_object(path: Path, value: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _ledger_without_verification(document: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(document))
    for model in normalized.get("models", []):
        if isinstance(model, dict):
            model["verification"] = []
    return normalized


def _require_generation_report(report: dict[str, Any], scene_spec: SceneSpec) -> None:
    if not isinstance(report, dict) or report.get("schema_version") != _GENERATION_REPORT_SCHEMA:
        raise AssetAdmissionError("unsupported generation report schema")
    if report.get("scene_id") != scene_spec.scene_id:
        raise AssetAdmissionError("generation report scene identity mismatch")
    generated = report.get("generated")
    if not isinstance(generated, list) or not all(isinstance(item, dict) for item in generated):
        raise AssetAdmissionError("generation report generated must be a list of objects")


def _require_provenance_shape(provenance: dict[str, Any]) -> None:
    if type(provenance) is not dict:
        raise AssetAdmissionError("generation provenance must be an exact object")
    generation_kind = provenance.get("generation_kind")
    if type(generation_kind) is not str:
        raise AssetAdmissionError("unsupported generation provenance kind")
    if generation_kind == "procedural_proxy":
        expected_fields = _COMMON_PROVENANCE_FIELDS
    elif generation_kind == "derived_scaled_proxy":
        expected_fields = _COMMON_PROVENANCE_FIELDS | _DERIVED_PROVENANCE_FIELDS
    else:
        raise AssetAdmissionError("unsupported generation provenance kind")
    if any(type(key) is not str for key in provenance) or set(provenance) != expected_fields:
        raise AssetAdmissionError("generation provenance has unexpected provenance fields")
    if (
        type(provenance.get("schema_version")) is not str
        or provenance["schema_version"] != _PROVENANCE_SCHEMA
    ):
        raise AssetAdmissionError("unsupported generation provenance schema")
    if (
        type(provenance.get("generator")) is not str
        or provenance["generator"] != _GENERATOR_BY_KIND[generation_kind]
    ):
        raise AssetAdmissionError("generation provenance generator mismatch")
    _require_nonempty_string(provenance.get("asset_id"), "asset_id")
    semantic_category = _require_nonempty_string(
        provenance.get("semantic_category"), "semantic_category"
    )
    if _SEMANTIC_TOKEN.fullmatch(semantic_category) is None:
        raise AssetAdmissionError("generation provenance semantic_category is invalid")
    for field in ("requested_color", "requested_material"):
        value = provenance.get(field)
        if value is not None and (
            type(value) is not str or _REQUEST_TOKEN.fullmatch(value) is None
        ):
            raise AssetAdmissionError(f"generation provenance {field} must be a token or null")
    geometry_family = _require_nonempty_string(provenance.get("geometry_family"), "geometry_family")
    if geometry_family not in _GEOMETRY_FAMILIES:
        raise AssetAdmissionError("generation provenance geometry_family is invalid")
    geometry_fidelity = _require_nonempty_string(
        provenance.get("geometry_fidelity"), "geometry_fidelity"
    )
    if geometry_fidelity != _GEOMETRY_FIDELITY_BY_KIND[generation_kind]:
        raise AssetAdmissionError("generation provenance geometry_fidelity mismatch")
    generated_license = _require_nonempty_string(
        provenance.get("generated_license"), "generated_license"
    )
    if generated_license != "project_generated_academic_artifact":
        raise AssetAdmissionError("generation provenance generated_license mismatch")
    _require_positive_vector(provenance.get("dimensions_m"), "dimensions_m")
    _require_payload_identity_shape(provenance.get("files"))
    if generation_kind == "derived_scaled_proxy":
        compatibility = provenance.get("compatibility")
        if (
            type(compatibility) is not dict
            or any(type(key) is not str for key in compatibility)
            or set(compatibility) != _COMPATIBILITY_FIELDS
        ):
            raise AssetAdmissionError("generation provenance has invalid compatibility fields")
        _require_derived_provenance_shape(provenance, compatibility)


def _require_nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value:
        raise AssetAdmissionError(f"generation provenance {field} must be a non-empty string")
    return value


def _require_locator_free_string(value: Any, field: str) -> str:
    normalized = _require_nonempty_string(value, field)
    if (
        "/" in normalized
        or "\\" in normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise AssetAdmissionError(f"generation provenance {field} must be a locator-free string")
    return normalized


def _require_string_list(value: Any, field: str, *, nonempty: bool = False) -> list[str]:
    if type(value) is not list or (nonempty and not value):
        raise AssetAdmissionError(
            f"generation provenance {field} must be a locator-free string list"
        )
    try:
        for item in value:
            _require_locator_free_string(item, field)
    except AssetAdmissionError as exc:
        raise AssetAdmissionError(
            f"generation provenance {field} must be a locator-free string list"
        ) from exc
    return value


def _require_finite_number(value: Any, field: str, *, positive: bool = False) -> float:
    if type(value) not in {int, float}:
        raise AssetAdmissionError(f"generation provenance {field} must be finite")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise AssetAdmissionError(f"generation provenance {field} must be finite") from exc
    if not math.isfinite(normalized):
        raise AssetAdmissionError(f"generation provenance {field} must be finite")
    if positive and normalized <= 0.0:
        raise AssetAdmissionError(f"generation provenance {field} must be positive")
    return normalized


def _require_nonnegative_integer_or_none(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise AssetAdmissionError(
            f"generation provenance {field} must be a non-negative integer or null"
        )
    return value


def _require_positive_vector(value: Any, field: str) -> tuple[float, float, float]:
    if type(value) is not list or len(value) != 3:
        raise AssetAdmissionError(f"generation provenance {field} must have three values")
    return tuple(_require_finite_number(item, field, positive=True) for item in value)


def _require_payload_identity_shape(files: Any) -> None:
    expected_file_keys = {"visual", "collision", "material", "metadata"}
    if (
        type(files) is not dict
        or any(type(key) is not str for key in files)
        or set(files) != expected_file_keys
    ):
        raise AssetAdmissionError("generation provenance has unexpected payload identities")
    for key in sorted(expected_file_keys):
        record = files.get(key)
        if (
            type(record) is not dict
            or any(type(field) is not str for field in record)
            or set(record) != {"path", "sha256"}
        ):
            raise AssetAdmissionError(f"generation provenance {key} identity has unexpected fields")
        if type(record.get("path")) is not str or not record["path"]:
            raise AssetAdmissionError(f"generation provenance {key} path mismatch")
        if type(record.get("sha256")) is not str or _SHA256.fullmatch(record["sha256"]) is None:
            raise AssetAdmissionError(f"generation provenance {key} digest is invalid")


def _require_derived_provenance_shape(
    provenance: dict[str, Any], compatibility: dict[str, Any]
) -> None:
    scale = _require_finite_number(
        provenance.get("uniform_scale_factor"), "uniform_scale_factor", positive=True
    )
    if scale > 1.0:
        raise AssetAdmissionError("generation provenance uniform_scale_factor exceeds one")
    source_asset_id = _require_nonempty_string(provenance.get("source_asset_id"), "source_asset_id")
    _require_safe_asset_id(source_asset_id)
    source_model_id = provenance.get("source_model_id")
    if type(source_model_id) is not int or source_model_id < 0:
        raise AssetAdmissionError(
            "generation provenance source_model_id must be a non-negative integer"
        )
    _require_positive_vector(provenance.get("source_dimensions_m"), "source_dimensions_m")
    reasons = _require_string_list(
        provenance.get("adaptation_reasons"), "adaptation_reasons", nonempty=True
    )
    if not set(reasons).issubset(_DERIVED_ADAPTATION_REASONS):
        raise AssetAdmissionError("generation provenance adaptation_reasons are invalid")
    _require_locator_free_string(provenance.get("semantic_name"), "semantic_name")
    _require_string_list(provenance.get("aliases"), "aliases")
    _require_string_list(provenance.get("materials"), "materials")

    relation = _require_nonempty_string(compatibility.get("relation"), "compatibility.relation")
    if relation not in _DERIVED_RELATIONS:
        raise AssetAdmissionError("generation provenance compatibility.relation is invalid")
    target_asset_id = _require_nonempty_string(
        compatibility.get("target_asset_id"), "compatibility.target_asset_id"
    )
    target_model_id = _require_nonnegative_integer_or_none(
        compatibility.get("target_model_id"), "compatibility.target_model_id"
    )
    if relation == RelationType.ON_TABLE.value:
        if target_asset_id != "table" or target_model_id is not None:
            raise AssetAdmissionError("generation provenance table compatibility is invalid")
    else:
        if target_model_id is None:
            raise AssetAdmissionError("generation provenance target compatibility is invalid")
        _require_safe_asset_id(target_asset_id)
    headroom = _require_finite_number(
        compatibility.get("headroom_fraction"),
        "compatibility.headroom_fraction",
        positive=True,
    )
    if headroom != SCALE_HEADROOM:
        raise AssetAdmissionError("generation provenance compatibility headroom mismatch")
    expected_probe = (
        "catalog collision unstable; primitive proxy required"
        if "source_runtime_instability" in reasons
        else "not_run"
    )
    source_runtime_probe = _require_nonempty_string(
        compatibility.get("source_runtime_probe"), "compatibility.source_runtime_probe"
    )
    if source_runtime_probe != expected_probe:
        raise AssetAdmissionError("generation provenance source_runtime_probe mismatch")


def _require_safe_asset_id(asset_id: Any) -> None:
    contract = _ledger_contract()
    try:
        contract.canonical_asset_key(asset_id)
    except contract.UnsafeAssetKeyError as exc:
        raise AssetAdmissionError(f"unsafe asset_id: {asset_id!r}") from exc


def _validate_generation_inputs(
    *,
    scene_spec: SceneSpec,
    entry: CatalogEntry,
    provenance: dict[str, Any],
    catalog_root: Path,
) -> Path:
    del scene_spec
    _require_safe_asset_id(entry.asset_id)
    if provenance.get("schema_version") != _PROVENANCE_SCHEMA:
        raise AssetAdmissionError("unsupported generation provenance schema")
    _require_provenance_shape(provenance)
    if provenance.get("asset_id") != entry.asset_id:
        raise AssetAdmissionError("generation provenance/catalog asset identity mismatch")
    source = Path(entry.asset_path).absolute()
    root = catalog_root.absolute()
    with _safe_directory(root, create=False), _safe_directory(source, create=False):
        pass
    if source.name != entry.asset_id or not source.is_relative_to(root):
        raise AssetAdmissionError("generated catalog asset path escapes its objects root")
    if not source.is_dir():
        raise AssetAdmissionError("generated catalog asset path is not a regular directory")
    if len(entry.models) != 1:
        raise AssetAdmissionError("generated catalog entry must contain exactly one model")
    model = entry.models[0]
    expected_paths = {
        "model_path": source,
        "metadata_path": source / "model_data0.json",
        "visual_path": source / "visual" / "textured0.obj",
        "collision_path": source / "collision" / "textured0.obj",
    }
    for field, expected in expected_paths.items():
        actual = Path(getattr(model, field)).absolute()
        if actual != expected.absolute() or not actual.is_relative_to(source):
            raise AssetAdmissionError(f"generated catalog {field} is not bound to its asset tree")
    dimensions = tuple(provenance.get("dimensions_m") or ())
    if model.dimensions_m is None or tuple(model.dimensions_m) != dimensions:
        raise AssetAdmissionError("generation provenance/catalog dimensions mismatch")
    installed = _load_json_object(source / "generation_provenance.json")
    if installed != provenance:
        raise AssetAdmissionError("generation report does not match installed provenance")
    manifest = _tree_manifest(source)
    if frozenset(manifest) != _EXPECTED_GENERATED_FILES:
        raise AssetAdmissionError("generated asset tree has missing or unexpected files")
    _validate_provenance_files(provenance, source, manifest)
    return source


def _validate_provenance_files(
    provenance: dict[str, Any],
    source: Path,
    manifest: dict[str, dict[str, Any]],
    *,
    relocated: bool = False,
) -> None:
    files = provenance.get("files")
    if not isinstance(files, dict):
        raise AssetAdmissionError("generation provenance has no payload identities")
    expected_file_keys = {
        "visual": source / "visual" / "textured0.obj",
        "collision": source / "collision" / "textured0.obj",
        "material": source / "visual" / "material.mtl",
        "metadata": source / "model_data0.json",
    }
    if set(files) != set(expected_file_keys):
        raise AssetAdmissionError("generation provenance has unexpected payload identities")
    for key, expected in expected_file_keys.items():
        record = files.get(key)
        if not isinstance(record, dict):
            raise AssetAdmissionError(f"generation provenance is missing {key} identity")
        if set(record) != {"path", "sha256"}:
            raise AssetAdmissionError(f"generation provenance {key} identity has unexpected fields")
        recorded_value = record.get("path")
        if type(recorded_value) is not str:
            raise AssetAdmissionError(f"generation provenance {key} path mismatch")
        recorded_path = Path(recorded_value)
        expected_relative = expected.relative_to(source)
        path_matches = (
            recorded_value == expected_relative.as_posix()
            if relocated
            else recorded_path.resolve() == expected.resolve()
        )
        if not path_matches:
            raise AssetAdmissionError(f"generation provenance {key} path mismatch")
        logical = expected.relative_to(source).as_posix()
        if record.get("sha256") != manifest[logical]["sha256"]:
            raise AssetAdmissionError(f"generation provenance {key} digest mismatch")


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssetAdmissionError(f"invalid generated evidence file: {path.name}") from exc
    if not isinstance(value, dict):
        raise AssetAdmissionError(f"generated evidence is not an object: {path.name}")
    return value


def _tree_manifest(root: Path, *, ignore: set[str] | None = None) -> dict[str, dict[str, Any]]:
    ignored = ignore or set()
    manifest: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        logical = path.relative_to(root).as_posix()
        if logical in ignored:
            continue
        if path.is_symlink():
            raise AssetAdmissionError(f"generated asset tree contains symlink: {logical}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise AssetAdmissionError(f"generated asset tree contains non-file: {logical}")
        manifest[logical] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    return manifest


def _require_tree_manifest(
    root: Path,
    expected: dict[str, dict[str, Any]],
    *,
    ignore: set[str] | None = None,
) -> None:
    if _tree_manifest(root, ignore=ignore) != expected:
        raise AssetAdmissionError("generated asset changed after its full file gate")


def _relocate_ledger(
    document: dict[str, Any], source: Path, destination: Path, contract: ModuleType
) -> dict[str, Any]:
    source_prefixes = {
        str(source.resolve()): str(destination.resolve()),
        contract.to_portable_uri(source): contract.to_portable_uri(destination),
    }

    def relocate(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, str):
            for source_prefix, destination_prefix in source_prefixes.items():
                separator = "/" if "/" in source_prefix else os.sep
                if value == source_prefix or value.startswith(source_prefix + separator):
                    return destination_prefix + value[len(source_prefix) :]
        return value

    relocated = relocate(document)
    model = relocated["models"][0]
    for verification in model["verification"]:
        verification["verified_digest"] = contract.reps_digest(model, verification["backend"])
    return relocated


def _violation_summary(violations: list[Any]) -> str:
    return "; ".join(f"{item.path}:{item.code}" for item in violations[:8])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

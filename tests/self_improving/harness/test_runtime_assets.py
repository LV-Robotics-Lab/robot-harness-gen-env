from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import ResolvedSceneSpec
from scene_gen.solver import solve_scene
from self_improving.harness import runtime_assets as runtime_assets_module
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.runtime_assets import (
    RUNTIME_ASSET_DRIFT_EXIT_CODE,
    RUNTIME_ASSET_PREFLIGHT_EXIT_CODE,
    RUNTIME_ASSET_SNAPSHOT_SCHEMA,
    RuntimeAssetSnapshotError,
    RuntimeAssetStore,
    RuntimeAssetWorkerError,
    verify_runtime_asset_snapshot,
)

ROOT = Path(__file__).resolve().parents[3]


def _asset_fixture(
    tmp_path: Path,
) -> tuple[ResolvedSceneSpec, AssetCatalog, Path, dict[str, bytes]]:
    source_root = tmp_path / "trusted"
    robotwin_root = source_root / "RoboTwin"
    objects_root = robotwin_root / "assets" / "objects"
    base_catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    resolved = solve_scene(
        parse_rule_based(
            "A red can is left of a plastic basket near the center.",
            seed=19,
        ),
        base_catalog,
    )
    selected = {(item.asset_id, item.model_id) for item in resolved.objects}
    entries = []
    file_contents: dict[str, bytes] = {}
    sources_by_model: dict[tuple[str, int], tuple[str, ...]] = {}
    for entry in base_catalog.entries:
        selected_models = [
            model for model in entry.models if (entry.asset_id, model.model_id) in selected
        ]
        if not selected_models:
            continue
        asset_root = objects_root / entry.asset_id
        models = []
        for model in selected_models:
            model_id = model.model_id
            metadata = asset_root / f"model_data{model_id}.json"
            visual = asset_root / "visual" / f"base{model_id}.obj"
            collision = asset_root / "collision" / f"base{model_id}.obj"
            material = asset_root / "materials" / f"base{model_id}.mtl"
            texture = asset_root / "textures" / f"base{model_id}.png"
            payloads = {
                metadata: b'{"scale":[1,1,1]}\n',
                visual: f"mtllib ../materials/base{model_id}.mtl\n".encode(),
                collision: b"v 0 0 0\n",
                material: f"map_Kd ../textures/base{model_id}.png\n".encode(),
                texture: f"texture-{entry.asset_id}-{model_id}".encode(),
            }
            for path, payload in payloads.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                file_contents[path.relative_to(objects_root).as_posix()] = payload
            source_files = tuple(str(path) for path in (collision, metadata, visual))
            sources_by_model[(entry.asset_id, model_id)] = source_files
            models.append(
                model.model_copy(
                    update={
                        "model_path": str(asset_root),
                        "metadata_path": str(metadata),
                        "visual_path": str(visual),
                        "collision_path": str(collision),
                        "urdf_path": None,
                    }
                )
            )
        entries.append(
            entry.model_copy(
                update={
                    "asset_path": str(asset_root),
                    "models": tuple(models),
                }
            )
        )
    catalog = AssetCatalog(
        robotwin_root=str(robotwin_root),
        objects_root=str(objects_root),
        entries=tuple(entries),
    )
    resolved = resolved.model_copy(
        update={
            "asset_catalog_sha256": catalog.digest(),
            "objects": tuple(
                item.model_copy(
                    update={"source_files": sources_by_model[(item.asset_id, item.model_id)]}
                )
                for item in resolved.objects
            ),
        }
    )
    return resolved, catalog, source_root, file_contents


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _replace_entry(catalog: AssetCatalog, asset_id: str, **updates) -> AssetCatalog:
    return catalog.model_copy(
        update={
            "entries": tuple(
                entry.model_copy(update=updates) if entry.asset_id == asset_id else entry
                for entry in catalog.entries
            )
        }
    )


def _replace_model(catalog: AssetCatalog, asset_id: str, model_id: int, **updates) -> AssetCatalog:
    entry = next(item for item in catalog.entries if item.asset_id == asset_id)
    models = tuple(
        model.model_copy(update=updates) if model.model_id == model_id else model
        for model in entry.models
    )
    return _replace_entry(catalog, asset_id, models=models)


def _replace_object(resolved: ResolvedSceneSpec, object_id: str, **updates) -> ResolvedSceneSpec:
    return resolved.model_copy(
        update={
            "objects": tuple(
                item.model_copy(update=updates) if item.object_id == object_id else item
                for item in resolved.objects
            )
        }
    )


def _snapshot_and_materialize(tmp_path: Path):
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    runtime_assets = RuntimeAssetStore(artifact_store)
    snapshot = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    materialized = runtime_assets.materialize(snapshot.manifest, tmp_path / "inputs")
    return resolved, catalog, artifact_store, runtime_assets, snapshot, materialized


def _write_manifest(path: Path, value, *, canonical: bool = True) -> str:
    path.chmod(0o644)
    payload = (
        runtime_assets_module.canonical_runtime_asset_manifest_bytes(value)
        if canonical
        else (json.dumps(value, indent=2) + "\n").encode()
    )
    path.write_bytes(payload)
    return _sha256(payload)


def _fd_path(descriptor: int) -> Path | None:
    try:
        return Path(os.readlink(f"/proc/self/fd/{descriptor}"))
    except OSError:
        return None


def _changed_stat(value: os.stat_result, **updates: int) -> SimpleNamespace:
    fields = {
        name: getattr(value, name)
        for name in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    }
    fields.update(updates)
    return SimpleNamespace(**fields)


def test_snapshot_preserves_complete_loader_trees_after_sources_change(
    tmp_path: Path,
) -> None:
    resolved, catalog, allowed_root, original = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    runtime_assets = RuntimeAssetStore(artifact_store)

    snapshot = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    manifest_payload = artifact_store.resolve(snapshot.manifest).path.read_bytes()
    manifest = json.loads(manifest_payload)
    assert snapshot.manifest.schema_version == RUNTIME_ASSET_SNAPSHOT_SCHEMA
    assert snapshot.manifest.sha256 == _sha256(manifest_payload)
    assert str(tmp_path) not in manifest_payload.decode()
    assert [item["asset_id"] for item in manifest["assets"]] == sorted(
        {item.asset_id for item in resolved.objects}
    )
    assert any(
        file["path"].startswith("textures/")
        for asset in manifest["assets"]
        for file in asset["files"]
    )
    assert all(
        asset["required_files"] == sorted(asset["required_files"]) for asset in manifest["assets"]
    )

    changed = Path(catalog.entries[0].asset_path) / "textures" / "base0.png"
    changed.write_bytes(b"source-mutated-after-snapshot")
    materialized = runtime_assets.materialize(
        snapshot.manifest,
        tmp_path / "attempt-inputs",
    )
    verified = verify_runtime_asset_snapshot(
        root=materialized.root,
        manifest_path=materialized.manifest_path,
        expected_sha256=snapshot.manifest.sha256,
        resolved=resolved,
        catalog=catalog,
    )

    assert verified.manifest_sha256 == snapshot.manifest.sha256
    assert set(verified.object_roots) == {item.object_id for item in resolved.objects}
    for relative, payload in original.items():
        asset_id, *asset_relative = Path(relative).parts
        staged = materialized.root / asset_id / Path(*asset_relative)
        assert staged.read_bytes() == payload
        assert staged.stat().st_mode & 0o222 == 0
    assert materialized.root.stat().st_mode & 0o222 == 0


def test_transitive_loader_file_changes_snapshot_identity(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    runtime_assets = RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas"))

    first = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    material = Path(catalog.entries[0].asset_path) / "materials" / "base0.mtl"
    material.write_bytes(b"map_Kd ../textures/revised.png\n")
    (material.parent.parent / "textures" / "revised.png").write_bytes(b"revised-texture")
    second = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )

    assert first.manifest.sha256 != second.manifest.sha256


def test_snapshot_rejects_resolved_catalog_digest_mismatch(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    changed_catalog = catalog.model_copy(update={"source_commit": "changed-after-resolve"})

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=resolved,
            catalog=changed_catalog,
            allowed_roots=(allowed_root,),
        )

    assert captured.value.reason == "asset_catalog_mismatch"


def test_verify_rejects_manifest_catalog_digest_that_disagrees_with_resolved(
    tmp_path: Path,
) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    manifest = json.loads(materialized.manifest_path.read_bytes())
    manifest["asset_catalog_sha256"] = "f" * 64
    expected = _write_manifest(materialized.manifest_path, manifest)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=expected,
            resolved=resolved,
            catalog=None,
        )

    assert captured.value.reason == "asset_catalog_mismatch"


def test_verify_rejects_forged_loader_root_set_even_when_tree_is_exact(
    tmp_path: Path,
) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    manifest = json.loads(materialized.manifest_path.read_bytes())
    manifest["assets"][0]["required_files"] = manifest["assets"][0]["required_files"][:-1]
    expected = _write_manifest(materialized.manifest_path, manifest)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=expected,
            resolved=resolved,
            catalog=catalog,
        )

    assert captured.value.reason == "loader_roots_mismatch"


def test_verify_reparses_loader_closure_in_materialized_bytes(tmp_path: Path) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    manifest = json.loads(materialized.manifest_path.read_bytes())
    asset = manifest["assets"][0]
    mtl_record = next(record for record in asset["files"] if record["path"].endswith(".mtl"))
    mtl = materialized.root / asset["asset_id"] / mtl_record["path"]
    mtl.chmod(0o644)
    payload = b"map_Kd ../textures/missing.png\n"
    mtl.write_bytes(payload)
    old_bytes = mtl_record["bytes"]
    mtl_record.update({"sha256": _sha256(payload), "bytes": len(payload)})
    asset["bytes"] += len(payload) - old_bytes
    asset["tree_sha256"] = _sha256(
        runtime_assets_module.canonical_runtime_asset_manifest_bytes(
            {"directories": asset["directories"], "files": asset["files"]}
        )
    )
    expected = _write_manifest(materialized.manifest_path, manifest)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=expected,
            resolved=resolved,
            catalog=catalog,
        )

    assert captured.value.reason == "loader_reference_missing"


@pytest.mark.parametrize(
    "reference",
    [
        "/tmp/outside.obj",
        "../../outside.obj",
        "https://example.invalid/outside.obj",
        "package://outside/mesh.obj",
    ],
)
def test_snapshot_rejects_urdf_loader_references_outside_selected_tree(
    tmp_path: Path,
    reference: str,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    model = next(value for value in entry.models if value.model_id == item.model_id)
    asset_root = Path(entry.asset_path)
    model_root = asset_root / "1"
    model_root.mkdir()
    metadata = model_root / "model_data.json"
    metadata.write_text('{"scale":[1,1,1]}\n', encoding="utf-8")
    urdf = model_root / "mobility.urdf"
    urdf.write_text(
        f'<robot name="fixture"><link name="base"><visual><geometry>'
        f'<mesh filename="{reference}"/></geometry></visual></link></robot>\n',
        encoding="utf-8",
    )
    updated_model = model.model_copy(
        update={
            "model_path": str(model_root),
            "metadata_path": str(metadata),
            "visual_path": None,
            "collision_path": None,
            "urdf_path": str(urdf),
        }
    )
    catalog = _replace_entry(
        catalog,
        item.asset_id,
        load_type="urdf",
        models=tuple(
            updated_model if value.model_id == item.model_id else value for value in entry.models
        ),
    )
    resolved = _replace_object(
        resolved,
        item.object_id,
        load_type="urdf",
        source_files=(str(metadata), str(urdf)),
    ).model_copy(update={"asset_catalog_sha256": catalog.digest()})

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )

    assert captured.value.reason == "loader_reference_unsafe"


def test_verify_maps_urdf_object_to_exact_catalog_model_root(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    original_model = next(value for value in entry.models if value.model_id == item.model_id)
    asset_root = Path(entry.asset_path)
    for name in ("1", "2", "9"):
        (asset_root / name).mkdir()
    model_root = asset_root / "2"
    metadata = model_root / "model_data.json"
    metadata.write_text('{"scale":[1,1,1]}\n', encoding="utf-8")
    urdf = model_root / "mobility.urdf"
    urdf.write_text('<robot name="fixture"><link name="base"/></robot>\n', encoding="utf-8")
    selected_model = original_model.model_copy(
        update={
            "model_id": 2,
            "model_path": str(model_root),
            "metadata_path": str(metadata),
            "visual_path": None,
            "collision_path": None,
            "urdf_path": str(urdf),
        }
    )
    catalog = _replace_entry(
        catalog,
        item.asset_id,
        load_type="urdf",
        models=(selected_model,),
    )
    resolved = _replace_object(
        resolved,
        item.object_id,
        model_id=2,
        load_type="urdf",
        source_files=(str(metadata), str(urdf)),
    ).model_copy(update={"asset_catalog_sha256": catalog.digest()})
    store = RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas"))

    snapshot = store.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    materialized = store.materialize(snapshot.manifest, tmp_path / "inputs")
    verified = verify_runtime_asset_snapshot(
        root=materialized.root,
        manifest_path=materialized.manifest_path,
        expected_sha256=snapshot.manifest.sha256,
        resolved=resolved,
        catalog=catalog,
    )

    assert verified.object_roots[item.object_id] == materialized.root / item.asset_id / "2"


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("obj", b"mtllib missing.mtl\n"),
        ("mtl", b"map_Kd ../missing/texture.png\n"),
        ("gltf", b'{"asset":{"version":"2.0"},"buffers":[{"uri":"missing.bin"}]}\n'),
    ],
)
def test_snapshot_rejects_missing_transitive_loader_reference(
    tmp_path: Path,
    kind: str,
    payload: bytes,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    model = next(value for value in entry.models if value.model_id == item.model_id)
    asset_root = Path(entry.asset_path)
    if kind == "obj":
        assert model.visual_path is not None
        Path(model.visual_path).write_bytes(payload)
    elif kind == "mtl":
        material = asset_root / "materials" / f"base{item.model_id}.mtl"
        material.write_bytes(payload)
    else:
        gltf = asset_root / "visual" / f"base{item.model_id}.gltf"
        gltf.write_bytes(payload)
        catalog = _replace_model(
            catalog,
            item.asset_id,
            item.model_id,
            visual_path=str(gltf),
        )
        resolved = _replace_object(
            resolved,
            item.object_id,
            source_files=tuple(
                str(gltf) if value == model.visual_path else value for value in item.source_files
            ),
        ).model_copy(update={"asset_catalog_sha256": catalog.digest()})

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )

    assert captured.value.reason == "loader_reference_missing"


def test_empty_selected_model_directory_is_hash_bound_preserved_and_exact(
    tmp_path: Path,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    empty_model_root = Path(entry.asset_path) / f"model{item.model_id}"
    empty_model_root.mkdir()
    catalog = _replace_model(
        catalog,
        item.asset_id,
        item.model_id,
        model_path=str(empty_model_root),
    )
    resolved = resolved.model_copy(update={"asset_catalog_sha256": catalog.digest()})
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    runtime_assets = RuntimeAssetStore(artifact_store)

    first = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    manifest = json.loads(artifact_store.resolve(first.manifest).path.read_bytes())
    asset = next(value for value in manifest["assets"] if value["asset_id"] == item.asset_id)
    assert asset["directories"] == sorted(asset["directories"])
    assert "." in asset["directories"]
    assert f"model{item.model_id}" in asset["directories"]

    materialized = runtime_assets.materialize(first.manifest, tmp_path / "inputs")
    staged_model_root = materialized.root / item.asset_id / f"model{item.model_id}"
    assert staged_model_root.is_dir()
    assert not list(staged_model_root.iterdir())
    staged_model_root.parent.chmod(0o755)
    staged_model_root.rmdir()
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=first.manifest.sha256,
            resolved=resolved,
            catalog=catalog,
        )
    assert captured.value.reason == "runtime_asset_tree_drift"

    (Path(entry.asset_path) / "another-empty-directory").mkdir()
    second = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    assert first.manifest.sha256 != second.manifest.sha256


def test_snapshot_rejects_source_files_not_declared_by_selected_catalog_model(
    tmp_path: Path,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    first = resolved.objects[0]
    attacked = resolved.model_copy(
        update={
            "objects": (
                first.model_copy(update={"source_files": first.source_files[:-1]}),
                *resolved.objects[1:],
            )
        }
    )

    with pytest.raises(RuntimeAssetSnapshotError, match="source_files"):
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=attacked,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )


def test_snapshot_rejects_symlink_and_empty_asset_trees(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    first_root = Path(catalog.entries[0].asset_path)
    (first_root / "escape").symlink_to(tmp_path / "outside")
    store = RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas"))
    with pytest.raises(RuntimeAssetSnapshotError, match="symlink"):
        store.snapshot(resolved=resolved, catalog=catalog, allowed_roots=(allowed_root,))

    (first_root / "escape").unlink()
    for path in sorted(first_root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    with pytest.raises(RuntimeAssetSnapshotError, match="empty"):
        store.snapshot(resolved=resolved, catalog=catalog, allowed_roots=(allowed_root,))


def test_verify_rejects_extra_symlink_and_manifest_path_escape(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    runtime_assets = RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas"))
    snapshot = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    materialized = runtime_assets.materialize(snapshot.manifest, tmp_path / "inputs")
    os.chmod(materialized.root, 0o755)
    extra = materialized.root / "extra"
    extra.symlink_to(tmp_path)
    with pytest.raises(RuntimeAssetSnapshotError, match="symlink|unexpected"):
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=snapshot.manifest.sha256,
            resolved=resolved,
            catalog=catalog,
        )

    extra.unlink()
    manifest = json.loads(materialized.manifest_path.read_bytes())
    manifest["assets"][0]["files"][0]["path"] = "../escape"
    os.chmod(materialized.manifest_path, 0o644)
    materialized.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    attacked_sha256 = _sha256(materialized.manifest_path.read_bytes())
    with pytest.raises(RuntimeAssetSnapshotError, match="path|canonical"):
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=attacked_sha256,
            resolved=resolved,
            catalog=catalog,
        )


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("invalid_json", "manifest_invalid"),
        ("invalid_utf8", "manifest_invalid"),
        ("duplicate_key", "manifest_invalid"),
        ("nan", "manifest_invalid"),
        ("not_object", "manifest_shape_invalid"),
        ("missing_field", "manifest_shape_invalid"),
        ("schema", "manifest_schema_unsupported"),
        ("resolved_digest", "manifest_digest_invalid"),
        ("catalog_digest", "manifest_digest_invalid"),
        ("assets_type", "manifest_assets_invalid"),
        ("assets_empty", "manifest_assets_invalid"),
        ("asset_shape", "manifest_asset_shape_invalid"),
        ("asset_id_type", "manifest_asset_id_invalid"),
        ("asset_id_empty", "manifest_asset_id_invalid"),
        ("asset_id_escape", "manifest_asset_id_invalid"),
        ("asset_id_windows", "manifest_asset_id_invalid"),
        ("asset_order", "manifest_assets_not_unique_sorted"),
        ("asset_duplicate", "manifest_assets_not_unique_sorted"),
        ("model_ids_type", "manifest_model_ids_invalid"),
        ("model_ids_empty", "manifest_model_ids_invalid"),
        ("model_ids_bool", "manifest_model_ids_invalid"),
        ("model_ids_negative", "manifest_model_ids_invalid"),
        ("model_ids_duplicate", "manifest_model_ids_invalid"),
        ("directories_missing", "manifest_asset_shape_invalid"),
        ("directories_type", "manifest_directories_invalid"),
        ("directories_empty", "manifest_directories_invalid"),
        ("directories_root_missing", "manifest_directories_invalid"),
        ("directories_duplicate", "manifest_directories_invalid"),
        ("directories_order", "manifest_directories_invalid"),
        ("directory_windows", "manifest_path_invalid"),
        ("directory_noncanonical", "manifest_directory_invalid"),
        ("directory_parent_missing", "manifest_directories_not_closed"),
        ("files_type", "manifest_files_invalid"),
        ("files_empty", "manifest_files_invalid"),
        ("file_shape", "manifest_file_shape_invalid"),
        ("file_path_type", "manifest_path_invalid"),
        ("file_path_empty", "manifest_path_invalid"),
        ("file_path_windows", "manifest_path_invalid"),
        ("file_path_absolute", "manifest_path_escape"),
        ("file_path_parent", "manifest_path_escape"),
        ("file_path_noncanonical", "manifest_path_invalid"),
        ("file_duplicate", "manifest_files_not_unique_sorted"),
        ("file_parent_missing", "manifest_file_parent_missing"),
        ("tree_path_collision", "manifest_tree_path_collision"),
        ("file_digest_type", "manifest_file_digest_invalid"),
        ("file_digest_bad", "manifest_file_digest_invalid"),
        ("file_bytes_bool", "manifest_file_bytes_invalid"),
        ("file_bytes_negative", "manifest_file_bytes_invalid"),
        ("file_count", "manifest_file_count_invalid"),
        ("required_files_type", "manifest_required_files_invalid"),
        ("required_files_empty", "manifest_required_files_invalid"),
        ("required_files_duplicate", "manifest_required_files_invalid"),
        ("required_files_missing", "manifest_required_files_invalid"),
        ("asset_bytes", "manifest_asset_bytes_invalid"),
        ("tree_digest_type", "manifest_tree_digest_invalid"),
        ("tree_digest_bad", "manifest_tree_digest_invalid"),
        ("noncanonical", "manifest_not_canonical"),
    ],
)
def test_manifest_attacks_fail_closed(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    manifest = json.loads(materialized.manifest_path.read_bytes())
    canonical = True
    raw_payload: bytes | None = None
    asset = manifest["assets"][0]
    file = asset["files"][0]
    if attack == "invalid_json":
        raw_payload = b"{"
    elif attack == "invalid_utf8":
        raw_payload = b"\xff"
    elif attack == "duplicate_key":
        raw_payload = b'{"schema_version":1,"schema_version":2}\n'
    elif attack == "nan":
        raw_payload = b'{"value":NaN}\n'
    elif attack == "not_object":
        manifest = []
    elif attack == "missing_field":
        manifest.pop("asset_catalog_sha256")
    elif attack == "schema":
        manifest["schema_version"] = "unsupported"
    elif attack == "resolved_digest":
        manifest["resolved_scene_sha256"] = "bad"
    elif attack == "catalog_digest":
        manifest["asset_catalog_sha256"] = 1
    elif attack == "assets_type":
        manifest["assets"] = {}
    elif attack == "assets_empty":
        manifest["assets"] = []
    elif attack == "asset_shape":
        asset.pop("bytes")
    elif attack == "asset_id_type":
        asset["asset_id"] = 1
    elif attack == "asset_id_empty":
        asset["asset_id"] = ""
    elif attack == "asset_id_escape":
        asset["asset_id"] = "../escape"
    elif attack == "asset_id_windows":
        asset["asset_id"] = "bad\\name"
    elif attack == "asset_order":
        manifest["assets"].reverse()
    elif attack == "asset_duplicate":
        manifest["assets"].insert(1, deepcopy(asset))
    elif attack == "model_ids_type":
        asset["selected_model_ids"] = {}
    elif attack == "model_ids_empty":
        asset["selected_model_ids"] = []
    elif attack == "model_ids_bool":
        asset["selected_model_ids"] = [True]
    elif attack == "model_ids_negative":
        asset["selected_model_ids"] = [-1]
    elif attack == "model_ids_duplicate":
        asset["selected_model_ids"] = [0, 0]
    elif attack == "directories_missing":
        asset.pop("directories")
    elif attack == "directories_type":
        asset["directories"] = {}
    elif attack == "directories_empty":
        asset["directories"] = []
    elif attack == "directories_root_missing":
        asset["directories"] = asset["directories"][1:]
    elif attack == "directories_duplicate":
        asset["directories"].insert(1, ".")
    elif attack == "directories_order":
        asset["directories"].reverse()
    elif attack == "directory_windows":
        asset["directories"].append("bad\\name")
        asset["directories"].sort()
    elif attack == "directory_noncanonical":
        asset["directories"].append("collision//nested")
        asset["directories"].sort()
    elif attack == "directory_parent_missing":
        asset["directories"].append("missing/child")
        asset["directories"].sort()
    elif attack == "files_type":
        asset["files"] = {}
    elif attack == "files_empty":
        asset["files"] = []
    elif attack == "file_shape":
        file.pop("bytes")
    elif attack == "file_path_type":
        file["path"] = 1
    elif attack == "file_path_empty":
        file["path"] = ""
    elif attack == "file_path_windows":
        file["path"] = "bad\\name"
    elif attack == "file_path_absolute":
        file["path"] = "/escape"
    elif attack == "file_path_parent":
        file["path"] = "../escape"
    elif attack == "file_path_noncanonical":
        file["path"] = file["path"].replace("/", "//", 1)
    elif attack == "file_duplicate":
        asset["files"].insert(1, deepcopy(file))
    elif attack == "file_parent_missing":
        parent = str(PurePosixPath(file["path"]).parent)
        asset["directories"].remove(parent)
    elif attack == "tree_path_collision":
        asset["directories"].append(file["path"])
        asset["directories"].sort()
    elif attack == "file_digest_type":
        file["sha256"] = 1
    elif attack == "file_digest_bad":
        file["sha256"] = "bad"
    elif attack == "file_bytes_bool":
        file["bytes"] = True
    elif attack == "file_bytes_negative":
        file["bytes"] = -1
    elif attack == "file_count":
        asset["file_count"] = 999
    elif attack == "required_files_type":
        asset["required_files"] = {}
    elif attack == "required_files_empty":
        asset["required_files"] = []
    elif attack == "required_files_duplicate":
        asset["required_files"] = [asset["required_files"][0]] * 2
    elif attack == "required_files_missing":
        asset["required_files"] = ["missing.bin"]
    elif attack == "asset_bytes":
        asset["bytes"] += 1
    elif attack == "tree_digest_type":
        asset["tree_sha256"] = 1
    elif attack == "tree_digest_bad":
        asset["tree_sha256"] = "f" * 64
    else:
        canonical = False
    if raw_payload is None:
        expected = _write_manifest(materialized.manifest_path, manifest, canonical=canonical)
    else:
        materialized.manifest_path.chmod(0o644)
        materialized.manifest_path.write_bytes(raw_payload)
        expected = _sha256(raw_payload)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=expected,
            resolved=resolved,
            catalog=catalog,
        )
    assert captured.value.reason == reason
    assert snapshot.manifest.sha256 != expected or attack == "noncanonical"


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("empty", "allowed_roots_empty"),
        ("list", "allowed_roots_empty"),
        ("wrong_type", "allowed_root_invalid"),
        ("missing", "allowed_root_unavailable"),
        ("file", "allowed_root_unsafe"),
        ("symlink", "allowed_root_unsafe"),
        ("duplicate", "allowed_root_duplicate"),
    ],
)
def test_allowed_root_attacks_fail_closed(tmp_path: Path, attack: str, reason: str) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    if attack == "empty":
        roots = ()
    elif attack == "list":
        roots = []
    elif attack == "wrong_type":
        roots = ("not-a-path",)
    elif attack == "missing":
        roots = (tmp_path / "missing",)
    elif attack == "file":
        candidate = tmp_path / "root-file"
        candidate.write_bytes(b"file")
        roots = (candidate,)
    elif attack == "symlink":
        candidate = tmp_path / "root-link"
        candidate.symlink_to(allowed_root, target_is_directory=True)
        roots = (candidate,)
    else:
        roots = (allowed_root, allowed_root)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=roots,  # type: ignore[arg-type]
        )
    assert captured.value.reason == reason


def test_snapshot_type_contracts_fail_before_io(tmp_path: Path) -> None:
    store = RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas"))
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    with pytest.raises(TypeError, match="artifact_store"):
        RuntimeAssetStore(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="resolved"):
        store.snapshot(resolved={}, catalog=catalog, allowed_roots=(allowed_root,))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="catalog"):
        store.snapshot(resolved=resolved, catalog={}, allowed_roots=(allowed_root,))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="manifest"):
        store.materialize(object(), tmp_path / "out")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("entry_missing", "catalog_entry_missing"),
        ("entry_duplicate", "catalog_entry_duplicate"),
        ("entry_unavailable", "catalog_entry_unavailable"),
        ("root_name", "asset_root_name_mismatch"),
        ("root_escape", "asset_root_escape"),
        ("model_duplicate", "catalog_model_duplicate"),
        ("load_type", "load_type_mismatch"),
        ("model_missing", "catalog_model_missing"),
        ("model_unusable", "catalog_model_unusable"),
        ("model_path_missing", "catalog_path_missing"),
        ("model_path_relative", "catalog_path_unsafe"),
        ("model_path_escape", "catalog_path_escape"),
        ("source_empty", "source_files_empty"),
        ("source_duplicate", "source_files_mismatch"),
        ("source_escape", "catalog_path_escape"),
        ("model_path_file", "catalog_model_root_missing"),
        ("required_file_missing", "catalog_file_missing"),
    ],
)
def test_catalog_and_resolved_binding_attacks_fail_closed(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    model = next(value for value in entry.models if value.model_id == item.model_id)
    if attack == "entry_missing":
        catalog = catalog.model_copy(
            update={"entries": tuple(value for value in catalog.entries if value != entry)}
        )
    elif attack == "entry_duplicate":
        catalog = catalog.model_copy(update={"entries": (*catalog.entries, entry)})
    elif attack == "entry_unavailable":
        catalog = _replace_entry(catalog, item.asset_id, available=False)
    elif attack == "root_name":
        catalog = _replace_entry(catalog, item.asset_id, asset_path=str(allowed_root / "wrong"))
    elif attack == "root_escape":
        outside = tmp_path / "outside" / item.asset_id
        outside.mkdir(parents=True)
        catalog = _replace_entry(catalog, item.asset_id, asset_path=str(outside))
    elif attack == "model_duplicate":
        catalog = _replace_entry(catalog, item.asset_id, models=(*entry.models, model))
    elif attack == "load_type":
        resolved = _replace_object(
            resolved,
            item.object_id,
            load_type="urdf" if item.load_type == "rigid" else "rigid",
        )
    elif attack == "model_missing":
        catalog = _replace_entry(catalog, item.asset_id, models=())
    elif attack == "model_unusable":
        catalog = _replace_model(catalog, item.asset_id, item.model_id, usable=False)
    elif attack == "model_path_missing":
        catalog = _replace_model(catalog, item.asset_id, item.model_id, model_path=None)
    elif attack == "model_path_relative":
        catalog = _replace_model(catalog, item.asset_id, item.model_id, model_path="relative")
    elif attack == "model_path_escape":
        catalog = _replace_model(
            catalog,
            item.asset_id,
            item.model_id,
            model_path=str(tmp_path / "outside-model"),
        )
    elif attack == "source_empty":
        catalog = _replace_model(
            catalog,
            item.asset_id,
            item.model_id,
            metadata_path=None,
            visual_path=None,
            collision_path=None,
            urdf_path=None,
        )
        resolved = _replace_object(resolved, item.object_id, source_files=())
    elif attack == "source_duplicate":
        resolved = _replace_object(
            resolved,
            item.object_id,
            source_files=(*item.source_files, item.source_files[0]),
        )
    elif attack == "source_escape":
        outside = tmp_path / "outside.obj"
        outside.write_bytes(b"outside")
        old_visual = model.visual_path
        catalog = _replace_model(
            catalog,
            item.asset_id,
            item.model_id,
            visual_path=str(outside),
        )
        resolved = _replace_object(
            resolved,
            item.object_id,
            source_files=tuple(
                str(outside) if value == old_visual else value for value in item.source_files
            ),
        )
    elif attack == "model_path_file":
        catalog = _replace_model(
            catalog,
            item.asset_id,
            item.model_id,
            model_path=model.metadata_path,
        )
    else:
        Path(item.source_files[0]).unlink()

    if attack not in {"load_type", "source_duplicate", "required_file_missing"}:
        resolved = resolved.model_copy(update={"asset_catalog_sha256": catalog.digest()})

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )
    assert captured.value.reason == reason


def test_tree_nonregular_permission_and_source_race_attacks_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    store = RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas"))
    asset_root = Path(catalog.entries[0].asset_path)
    fifo = asset_root / "unsupported"
    os.mkfifo(fifo)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        store.snapshot(resolved=resolved, catalog=catalog, allowed_roots=(allowed_root,))
    assert captured.value.reason == "tree_nonregular"
    fifo.unlink()

    first_model = catalog.entries[0].models[0]
    assert first_model.collision_path is not None
    source = Path(first_model.collision_path)
    original_mode = stat.S_IMODE(source.stat().st_mode)
    source.chmod(0)
    try:
        with pytest.raises(RuntimeAssetSnapshotError) as captured:
            store.snapshot(resolved=resolved, catalog=catalog, allowed_roots=(allowed_root,))
        assert captured.value.reason in {"file_unreadable", "source_permission"}
    finally:
        source.chmod(original_mode)

    original_read = runtime_assets_module.os.read
    mutated = False

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, size)
        if chunk and not mutated:
            mutated = True
            source.write_bytes(source.read_bytes() + b"race")
        return chunk

    monkeypatch.setattr(runtime_assets_module.os, "read", racing_read)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        store.snapshot(resolved=resolved, catalog=catalog, allowed_roots=(allowed_root,))
    assert captured.value.reason == "source_race"


def test_repeated_selected_asset_is_snapshotted_once(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    repeated = resolved.objects[0].model_copy(update={"object_id": "repeated_asset"})
    resolved = resolved.model_copy(update={"objects": (*resolved.objects, repeated)})
    artifact_store = LocalArtifactStore(tmp_path / "cas")

    snapshot = RuntimeAssetStore(artifact_store).snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    manifest = json.loads(artifact_store.resolve(snapshot.manifest).path.read_bytes())

    assert len(manifest["assets"]) == len({item.asset_id for item in resolved.objects})
    assert [asset["asset_id"] for asset in manifest["assets"]].count(repeated.asset_id) == 1


def test_generated_provenance_is_bound_inside_the_complete_tree(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    provenance = (
        Path(next(entry.asset_path for entry in catalog.entries if entry.asset_id == item.asset_id))
        / "generation_provenance.json"
    )
    provenance.write_text('{"generator":"fixture"}\n', encoding="utf-8")
    resolved = _replace_object(
        resolved,
        item.object_id,
        generation_metadata_path=str(provenance),
        source_files=(*item.source_files, str(provenance)),
    )
    artifact_store = LocalArtifactStore(tmp_path / "cas")

    snapshot = RuntimeAssetStore(artifact_store).snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    manifest = json.loads(artifact_store.resolve(snapshot.manifest).path.read_bytes())

    selected = next(asset for asset in manifest["assets"] if asset["asset_id"] == item.asset_id)
    assert "generation_provenance.json" in {file["path"] for file in selected["files"]}


def test_verify_type_identity_and_semantic_attacks_fail_closed(tmp_path: Path) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    common = {
        "root": materialized.root,
        "manifest_path": materialized.manifest_path,
        "expected_sha256": snapshot.manifest.sha256,
        "resolved": resolved,
        "catalog": catalog,
    }
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(**{**common, "expected_sha256": "bad"})
    assert captured.value.reason == "expected_digest_invalid"
    with pytest.raises(TypeError, match="resolved"):
        verify_runtime_asset_snapshot(**{**common, "resolved": {}})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="catalog"):
        verify_runtime_asset_snapshot(**{**common, "catalog": {}})  # type: ignore[arg-type]
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(**{**common, "expected_sha256": "f" * 64})
    assert captured.value.reason == "manifest_digest_mismatch"

    changed_resolved = resolved.model_copy(update={"seed": resolved.seed + 1})
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(**{**common, "resolved": changed_resolved})
    assert captured.value.reason == "resolved_scene_mismatch"
    changed_catalog = catalog.model_copy(update={"source_commit": "changed"})
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(**{**common, "catalog": changed_catalog})
    assert captured.value.reason == "asset_catalog_mismatch"

    manifest = json.loads(materialized.manifest_path.read_bytes())
    manifest["assets"][0]["selected_model_ids"] = [999]
    expected = _write_manifest(materialized.manifest_path, manifest)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(**{**common, "expected_sha256": expected})
    assert captured.value.reason == "selected_assets_mismatch"


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("root_missing", "directory_unavailable"),
        ("root_file", "directory_unsafe"),
        ("root_symlink", "directory_unsafe"),
        ("root_ancestor_symlink", "directory_unavailable"),
        ("asset_missing", "runtime_asset_root_unexpected"),
        ("asset_symlink", "runtime_asset_tree_unsafe"),
        ("file_symlink", "runtime_asset_tree_symlink"),
        ("file_fifo", "runtime_asset_tree_nonregular"),
        ("file_extra", "runtime_asset_tree_drift"),
        ("directory_extra", "runtime_asset_tree_drift"),
        ("file_drift", "runtime_asset_tree_drift"),
        ("file_same_size_drift", "runtime_asset_tree_drift"),
        ("file_as_directory", "runtime_asset_tree_drift"),
    ],
)
def test_materialized_tree_attacks_fail_closed(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    root = materialized.root
    root.chmod(0o755)
    if attack == "root_missing":
        attacked_root = tmp_path / "missing"
    elif attack == "root_file":
        attacked_root = tmp_path / "root-file"
        attacked_root.write_bytes(b"file")
    elif attack == "root_symlink":
        attacked_root = tmp_path / "root-link"
        attacked_root.symlink_to(root, target_is_directory=True)
    elif attack == "root_ancestor_symlink":
        linked_parent = tmp_path / "root-parent-link"
        linked_parent.symlink_to(root.parent, target_is_directory=True)
        attacked_root = linked_parent / root.name
    else:
        attacked_root = root
        asset_root = next(path for path in root.iterdir() if path.is_dir())
        asset_root.chmod(0o755)
        if attack == "asset_missing":
            moved = tmp_path / asset_root.name
            asset_root.rename(moved)
        elif attack == "asset_symlink":
            moved = tmp_path / asset_root.name
            asset_root.rename(moved)
            asset_root.symlink_to(moved, target_is_directory=True)
        elif attack == "file_extra":
            (asset_root / "unexpected.bin").write_bytes(b"unexpected")
        elif attack == "directory_extra":
            (asset_root / "unexpected-empty-directory").mkdir()
        else:
            file = next(path for path in asset_root.rglob("*") if path.is_file())
            file.chmod(0o644)
            if attack == "file_symlink":
                target = tmp_path / "target"
                target.write_bytes(file.read_bytes())
                file.unlink()
                file.symlink_to(target)
            elif attack == "file_fifo":
                file.unlink()
                os.mkfifo(file)
            elif attack == "file_as_directory":
                file.unlink()
                file.mkdir()
            elif attack == "file_same_size_drift":
                payload = file.read_bytes()
                file.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
            else:
                file.write_bytes(b"drift")

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=attacked_root,
            manifest_path=materialized.manifest_path,
            expected_sha256=snapshot.manifest.sha256,
            resolved=resolved,
            catalog=catalog,
        )
    assert captured.value.reason == reason


def test_materialize_contract_and_cas_attacks_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    runtime_assets = RuntimeAssetStore(artifact_store)
    snapshot = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )

    wrong_schema = snapshot.manifest.model_copy(update={"schema_version": None})
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(wrong_schema, tmp_path / "wrong-schema")
    assert captured.value.reason == "manifest_schema_mismatch"
    destination = tmp_path / "exists"
    destination.mkdir()
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(snapshot.manifest, destination)
    assert captured.value.reason == "destination_exists"

    unsupported_uri = snapshot.manifest.model_copy(update={"uri": "file:///tmp/unsafe"})
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(unsupported_uri, tmp_path / "unsupported")
    assert captured.value.reason == "artifact_uri_unsupported"
    wrong_uri_digest = snapshot.manifest.model_copy(update={"uri": f"artifact://sha256/{'f' * 64}"})
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(wrong_uri_digest, tmp_path / "wrong-uri")
    assert captured.value.reason == "artifact_uri_digest_mismatch"
    wrong_bytes = snapshot.manifest.model_copy(update={"bytes": snapshot.manifest.bytes + 1})
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(wrong_bytes, tmp_path / "wrong-bytes")
    assert captured.value.reason == "artifact_bytes_mismatch"

    monkeypatch.setattr(runtime_assets_module, "_MAX_MANIFEST_BYTES", 1)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(snapshot.manifest, tmp_path / "too-large")
    assert captured.value.reason == "file_too_large"


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("missing", "artifact_unavailable"),
        ("symlink", "artifact_unavailable"),
        ("directory", "artifact_nonregular"),
        ("content", "artifact_identity_mismatch"),
    ],
)
def test_materialize_member_cas_attacks_cleanup_staging(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, artifact_store, runtime_assets, snapshot, _ = _snapshot_and_materialize(
        tmp_path
    )
    del resolved, catalog
    manifest = json.loads(artifact_store.resolve(snapshot.manifest).path.read_bytes())
    digest = manifest["assets"][0]["files"][0]["sha256"]
    member = artifact_store.root / "sha256" / digest[:2] / digest
    member.chmod(0o644)
    original = member.read_bytes()
    if attack == "missing":
        member.unlink()
    elif attack == "symlink":
        member.unlink()
        target = tmp_path / "member-target"
        target.write_bytes(original)
        member.symlink_to(target)
    elif attack == "directory":
        member.unlink()
        member.mkdir()
    else:
        member.write_bytes(b"x" * len(original))
    destination = tmp_path / "attacked-inputs"

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(snapshot.manifest, destination)
    assert captured.value.reason == reason
    assert not destination.exists()
    assert not list(tmp_path.glob(".attacked-inputs.*"))


def test_cas_root_and_existing_object_corruption_fail_closed(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    cas_file = tmp_path / "cas-file"
    cas_file.write_bytes(b"not-directory")
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(cas_file)).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )
    assert captured.value.reason == "cas_root_unsafe"

    artifact_store = LocalArtifactStore(tmp_path / "cas")
    model = catalog.entries[0].models[0]
    assert model.collision_path is not None
    payload = Path(model.collision_path).read_bytes()
    digest = _sha256(payload)
    corrupt = artifact_store.root / "sha256" / digest[:2] / digest
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"corrupt")
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(artifact_store).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )
    assert captured.value.reason == "cas_object_corrupt"


def test_snapshot_rejects_symlinked_cas_namespace_without_writing_outside(
    tmp_path: Path,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    artifact_store.root.mkdir()
    outside = tmp_path / "outside-cas"
    outside.mkdir()
    (artifact_store.root / "sha256").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(artifact_store).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )

    assert captured.value.reason == "cas_layout_unsafe"
    assert list(outside.iterdir()) == []


def test_snapshot_cas_rename_stays_on_the_verified_directory_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    outside = tmp_path / "outside-cas"
    outside.mkdir()
    held = tmp_path / "verified-shard"
    original_replace = runtime_assets_module.os.replace
    swapped = False

    def attacked_replace(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            if kwargs.get("dst_dir_fd") is not None:
                destination_parent = _fd_path(kwargs["dst_dir_fd"])
            else:
                destination_parent = Path(destination).parent
            if destination_parent is not None and destination_parent.parent.name == "sha256":
                swapped = True
                destination_parent.rename(held)
                destination_parent.symlink_to(outside, target_is_directory=True)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(runtime_assets_module.os, "replace", attacked_replace)
    with pytest.raises(RuntimeAssetSnapshotError):
        RuntimeAssetStore(artifact_store).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )

    assert swapped is True
    assert list(outside.iterdir()) == []


def test_snapshot_stops_before_reading_an_oversized_existing_cas_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    model = catalog.entries[0].models[0]
    assert model.collision_path is not None
    payload = Path(model.collision_path).read_bytes()
    digest = _sha256(payload)
    corrupt = artifact_store.root / "sha256" / digest[:2] / digest
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"x" * (8 * 1024 * 1024))
    identity = (corrupt.stat().st_dev, corrupt.stat().st_ino)
    original_read = runtime_assets_module.os.read
    bytes_read = 0

    def observed_read(descriptor: int, size: int) -> bytes:
        nonlocal bytes_read
        chunk = original_read(descriptor, size)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == identity:
            bytes_read += len(chunk)
        return chunk

    monkeypatch.setattr(runtime_assets_module.os, "read", observed_read)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(artifact_store).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )

    assert captured.value.reason == "cas_object_corrupt"
    assert bytes_read <= len(payload) + 1


def test_materialize_stops_reading_corrupt_member_after_expected_byte_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, artifact_store, runtime_assets, snapshot, _ = _snapshot_and_materialize(tmp_path)
    manifest = json.loads(artifact_store.resolve(snapshot.manifest).path.read_bytes())
    record = manifest["assets"][0]["files"][0]
    member = artifact_store.root / "sha256" / record["sha256"][:2] / record["sha256"]
    member.chmod(0o644)
    member.write_bytes(b"x" * (8 * 1024 * 1024))
    member_identity = (member.stat().st_dev, member.stat().st_ino)
    original_read = runtime_assets_module.os.read
    bytes_read = 0

    def observed_read(descriptor: int, size: int) -> bytes:
        nonlocal bytes_read
        payload = original_read(descriptor, size)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == member_identity:
            bytes_read += len(payload)
        return payload

    monkeypatch.setattr(runtime_assets_module.os, "read", observed_read)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(snapshot.manifest, tmp_path / "bounded-copy")

    assert captured.value.reason == "artifact_identity_mismatch"
    assert bytes_read <= record["bytes"] + 1


def test_verify_rejects_undeclared_large_file_before_hashing_its_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    materialized.root.chmod(0o755)
    asset_root = next(path for path in materialized.root.iterdir() if path.is_dir())
    asset_root.chmod(0o755)
    extra = asset_root / "undeclared-large.bin"
    extra.write_bytes(b"x" * (8 * 1024 * 1024))
    extra_identity = (extra.stat().st_dev, extra.stat().st_ino)
    original_read = runtime_assets_module.os.read
    bytes_read = 0

    def observed_read(descriptor: int, size: int) -> bytes:
        nonlocal bytes_read
        payload = original_read(descriptor, size)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == extra_identity:
            bytes_read += len(payload)
        return payload

    monkeypatch.setattr(runtime_assets_module.os, "read", observed_read)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=snapshot.manifest.sha256,
            resolved=resolved,
            catalog=catalog,
        )

    assert captured.value.reason == "runtime_asset_tree_drift"
    assert bytes_read == 0


def test_identical_snapshot_reuses_valid_member_and_manifest_objects(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    runtime_assets = RuntimeAssetStore(artifact_store)

    first = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    second = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )

    assert second == first


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("trusted_root_open", "directory_unavailable"),
        ("path_component_open", "directory_unavailable"),
        ("asset_directory_permission", "directory_permission"),
    ],
)
def test_snapshot_rejects_loader_root_open_and_permission_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    asset_root = Path(catalog.entries[0].asset_path)
    original_open = runtime_assets_module.os.open
    original_mode = stat.S_IMODE(asset_root.stat().st_mode)

    if attack == "asset_directory_permission":
        asset_root.chmod(0o400)
    else:

        def attacked_open(
            path: str | bytes | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if (
                attack == "trusted_root_open"
                and dir_fd is None
                and Path(path) == Path(allowed_root.anchor)
            ):
                raise PermissionError("injected trusted-root open failure")
            if attack == "path_component_open" and dir_fd is not None and str(path) == "RoboTwin":
                raise FileNotFoundError("injected path-component race")
            return original_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(runtime_assets_module.os, "open", attacked_open)

    try:
        with pytest.raises(RuntimeAssetSnapshotError) as captured:
            RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
                resolved=resolved,
                catalog=catalog,
                allowed_roots=(allowed_root,),
            )
        assert captured.value.reason == reason
    finally:
        asset_root.chmod(original_mode)


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("nested_permission", "directory_permission"),
        ("listdir", "directory_unreadable"),
        ("entry_stat", "tree_race"),
        ("child_open", "tree_race"),
        ("directory_identity", "tree_race"),
    ],
)
def test_snapshot_rejects_directory_walk_boundary_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    asset_root = Path(catalog.entries[0].asset_path)
    nested = asset_root / "visual"
    original_mode = stat.S_IMODE(nested.stat().st_mode)

    if attack == "nested_permission":
        nested.chmod(0o400)
    elif attack == "listdir":
        original_listdir = runtime_assets_module.os.listdir

        def attacked_listdir(path: int | str | bytes | os.PathLike[str]):
            if isinstance(path, int) and _fd_path(path) == asset_root:
                raise PermissionError("injected directory listing failure")
            return original_listdir(path)

        monkeypatch.setattr(runtime_assets_module.os, "listdir", attacked_listdir)
    elif attack == "entry_stat":
        original_stat = runtime_assets_module.os.stat

        def attacked_stat(path, *args, **kwargs):
            if (
                str(path) == "collision"
                and kwargs.get("dir_fd") is not None
                and kwargs.get("follow_symlinks") is False
            ):
                raise FileNotFoundError("injected directory entry race")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(runtime_assets_module.os, "stat", attacked_stat)
    elif attack == "child_open":
        original_open = runtime_assets_module.os.open

        def attacked_open(
            path: str | bytes | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if str(path) == "collision" and dir_fd is not None and flags & os.O_DIRECTORY:
                raise FileNotFoundError("injected child directory race")
            return original_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(runtime_assets_module.os, "open", attacked_open)
    else:
        original_fstat = runtime_assets_module.os.fstat
        asset_calls = 0

        def attacked_fstat(descriptor: int):
            nonlocal asset_calls
            value = original_fstat(descriptor)
            if _fd_path(descriptor) == asset_root:
                asset_calls += 1
                if asset_calls == 3:
                    return _changed_stat(value, st_mtime_ns=value.st_mtime_ns + 1)
            return value

        monkeypatch.setattr(runtime_assets_module.os, "fstat", attacked_fstat)

    try:
        with pytest.raises(RuntimeAssetSnapshotError) as captured:
            RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
                resolved=resolved,
                catalog=catalog,
                allowed_roots=(allowed_root,),
            )
        assert captured.value.reason == reason
    finally:
        nested.chmod(original_mode)


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("nonregular_after_open", "source_nonregular"),
        ("permission_after_open", "source_permission"),
        ("identity_after_read", "source_race"),
    ],
)
def test_snapshot_rechecks_open_source_descriptor_identity_and_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    model = catalog.entries[0].models[0]
    assert model.collision_path is not None
    target = Path(model.collision_path)
    original_fstat = runtime_assets_module.os.fstat
    calls = 0

    def attacked_fstat(descriptor: int):
        nonlocal calls
        value = original_fstat(descriptor)
        if _fd_path(descriptor) == target:
            calls += 1
            if attack == "identity_after_read":
                if calls == 2:
                    return _changed_stat(value, st_mtime_ns=value.st_mtime_ns + 1)
            else:
                mode = stat.S_IFIFO | 0o600 if attack == "nonregular_after_open" else stat.S_IFREG
                return _changed_stat(value, st_mode=mode)
        return value

    monkeypatch.setattr(runtime_assets_module.os, "fstat", attacked_fstat)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )
    assert captured.value.reason == reason


def test_regular_loader_inputs_are_opened_nonblocking_to_fail_closed_on_fifo_swaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    runtime_assets = RuntimeAssetStore(artifact_store)
    original_open = runtime_assets_module.os.open
    flags_by_path: dict[Path, list[int]] = {}

    def observing_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        opened = _fd_path(descriptor)
        if opened is not None:
            flags_by_path.setdefault(opened, []).append(flags)
        return descriptor

    monkeypatch.setattr(runtime_assets_module.os, "open", observing_open)
    snapshot = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    materialized = runtime_assets.materialize(snapshot.manifest, tmp_path / "inputs")
    verify_runtime_asset_snapshot(
        root=materialized.root,
        manifest_path=materialized.manifest_path,
        expected_sha256=snapshot.manifest.sha256,
        resolved=resolved,
        catalog=catalog,
    )

    model = catalog.entries[0].models[0]
    assert model.collision_path is not None
    staged_file = next(path for path in materialized.root.rglob("*") if path.is_file())
    for opened_file in (Path(model.collision_path), materialized.manifest_path, staged_file):
        assert opened_file in flags_by_path
        assert all(flags & os.O_NONBLOCK for flags in flags_by_path[opened_file])


def test_snapshot_rejects_corrupt_existing_manifest_cas_object(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    runtime_assets = RuntimeAssetStore(artifact_store)
    snapshot = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    manifest_object = artifact_store.resolve(snapshot.manifest).path
    manifest_object.chmod(0o644)
    manifest_object.write_bytes(b"x" * snapshot.manifest.bytes)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )
    assert captured.value.reason == "cas_object_corrupt"


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("missing", "file_unavailable"),
        ("nonregular", "file_nonregular"),
        ("digest", "artifact_digest_mismatch"),
        ("growth", "artifact_bytes_mismatch"),
        ("short", "artifact_bytes_mismatch"),
        ("read_race", "file_race"),
    ],
)
def test_materialize_rejects_manifest_cas_read_attacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    runtime_assets = RuntimeAssetStore(artifact_store)
    snapshot = runtime_assets.snapshot(
        resolved=resolved,
        catalog=catalog,
        allowed_roots=(allowed_root,),
    )
    manifest_object = artifact_store.resolve(snapshot.manifest).path
    if attack == "missing":
        manifest_object.unlink()
    elif attack == "nonregular":
        manifest_object.unlink()
        manifest_object.mkdir()
    elif attack == "digest":
        manifest_object.chmod(0o644)
        payload = manifest_object.read_bytes()
        manifest_object.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    elif attack == "read_race":
        original_fstat = runtime_assets_module.os.fstat
        calls = 0

        def attacked_fstat(descriptor: int):
            nonlocal calls
            value = original_fstat(descriptor)
            if _fd_path(descriptor) == manifest_object:
                calls += 1
                if calls == 2:
                    return _changed_stat(value, st_ctime_ns=value.st_ctime_ns + 1)
            return value

        monkeypatch.setattr(runtime_assets_module.os, "fstat", attacked_fstat)
    else:
        original_read = runtime_assets_module.os.read
        injected = False

        def attacked_read(descriptor: int, size: int) -> bytes:
            nonlocal injected
            payload = original_read(descriptor, size)
            if not injected and _fd_path(descriptor) == manifest_object:
                injected = True
                if attack == "growth" and payload:
                    return payload + b"x"
                if attack == "short":
                    return b""
            return payload

        monkeypatch.setattr(runtime_assets_module.os, "read", attacked_read)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(snapshot.manifest, tmp_path / "inputs")
    assert captured.value.reason == reason


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("missing", "file_unavailable"),
        ("directory", "file_nonregular"),
        ("too_large", "file_too_large"),
        ("race", "file_race"),
    ],
)
def test_verify_rejects_manifest_path_read_attacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    manifest_path = materialized.manifest_path
    manifest_path.parent.chmod(0o755)
    manifest_path.chmod(0o644)
    if attack == "missing":
        manifest_path.unlink()
    elif attack == "directory":
        manifest_path.unlink()
        manifest_path.mkdir()
    elif attack == "too_large":
        monkeypatch.setattr(runtime_assets_module, "_MAX_MANIFEST_BYTES", 1)
    else:
        original_fstat = runtime_assets_module.os.fstat
        calls = 0

        def attacked_fstat(descriptor: int):
            nonlocal calls
            value = original_fstat(descriptor)
            if _fd_path(descriptor) == manifest_path:
                calls += 1
                if calls == 2:
                    return _changed_stat(value, st_mtime_ns=value.st_mtime_ns + 1)
            return value

        monkeypatch.setattr(runtime_assets_module.os, "fstat", attacked_fstat)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=manifest_path,
            expected_sha256=snapshot.manifest.sha256,
            resolved=resolved,
            catalog=catalog,
        )
    assert captured.value.reason == reason


def test_materialize_rejects_member_cas_identity_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, catalog, artifact_store, runtime_assets, snapshot, _ = _snapshot_and_materialize(
        tmp_path
    )
    del resolved, catalog
    manifest = json.loads(artifact_store.resolve(snapshot.manifest).path.read_bytes())
    digest = manifest["assets"][0]["files"][0]["sha256"]
    member = artifact_store.root / "sha256" / digest[:2] / digest
    original_fstat = runtime_assets_module.os.fstat
    calls = 0

    def attacked_fstat(descriptor: int):
        nonlocal calls
        value = original_fstat(descriptor)
        if _fd_path(descriptor) == member:
            calls += 1
            if calls == 2:
                return _changed_stat(value, st_mtime_ns=value.st_mtime_ns + 1)
        return value

    monkeypatch.setattr(runtime_assets_module.os, "fstat", attacked_fstat)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(snapshot.manifest, tmp_path / "raced-inputs")
    assert captured.value.reason == "artifact_race"
    assert not list(tmp_path.glob(".raced-inputs.*"))


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("root_listdir", "runtime_asset_root_unreadable"),
        ("identity_listdir", "runtime_asset_tree_race"),
        ("directory_identity", "runtime_asset_tree_race"),
        ("file_nonregular_after_open", "runtime_asset_file_nonregular"),
        ("file_size_before_read", "runtime_asset_tree_drift"),
        ("file_identity", "runtime_asset_file_race"),
    ],
)
def test_verify_rejects_runtime_tree_boundary_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    asset_root = next(path for path in materialized.root.iterdir() if path.is_dir())
    file = next(path for path in asset_root.rglob("*") if path.is_file())
    if attack in {"root_listdir", "identity_listdir"}:
        original_listdir = runtime_assets_module.os.listdir
        target_directory = materialized.root if attack == "root_listdir" else asset_root

        def attacked_listdir(path: int | str | bytes | os.PathLike[str]):
            if isinstance(path, int) and _fd_path(path) == target_directory:
                raise PermissionError("injected verification listing failure")
            return original_listdir(path)

        monkeypatch.setattr(runtime_assets_module.os, "listdir", attacked_listdir)
    else:
        original_fstat = runtime_assets_module.os.fstat
        calls = 0
        target = asset_root if attack == "directory_identity" else file

        def attacked_fstat(descriptor: int):
            nonlocal calls
            value = original_fstat(descriptor)
            if _fd_path(descriptor) == target:
                calls += 1
                if attack == "file_nonregular_after_open":
                    return _changed_stat(value, st_mode=stat.S_IFIFO | 0o600)
                if attack == "file_size_before_read":
                    return _changed_stat(value, st_size=value.st_size + 1)
                if calls == 2:
                    return _changed_stat(value, st_ctime_ns=value.st_ctime_ns + 1)
            return value

        monkeypatch.setattr(runtime_assets_module.os, "fstat", attacked_fstat)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=snapshot.manifest.sha256,
            resolved=resolved,
            catalog=catalog,
        )
    assert captured.value.reason == reason


def test_verify_rejects_file_growth_after_the_open_size_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, catalog, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)
    asset_root = next(path for path in materialized.root.iterdir() if path.is_dir())
    target = next(path for path in asset_root.rglob("*") if path.is_file())
    original_read = runtime_assets_module.os.read
    injected = False

    def attacked_read(descriptor: int, size: int) -> bytes:
        nonlocal injected
        payload = original_read(descriptor, size)
        if not injected and _fd_path(descriptor) == target and payload:
            injected = True
            return payload + b"x"
        return payload

    monkeypatch.setattr(runtime_assets_module.os, "read", attacked_read)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=snapshot.manifest.sha256,
            resolved=resolved,
            catalog=catalog,
        )
    assert captured.value.reason == "runtime_asset_tree_drift"


def test_materialize_cleanup_tolerates_disappearing_staging_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, runtime_assets, snapshot, _ = _snapshot_and_materialize(tmp_path)

    def remove_staging_then_fail(_store, _ref, destination: Path) -> None:
        staging = next(path for path in destination.parents if path.parent == tmp_path)
        runtime_assets_module.shutil.rmtree(staging)
        raise RuntimeAssetSnapshotError("injected_copy_failure")

    monkeypatch.setattr(runtime_assets_module, "_copy_cas_ref", remove_staging_then_fail)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(snapshot.manifest, tmp_path / "removed")
    assert captured.value.reason == "injected_copy_failure"
    assert not list(tmp_path.glob(".removed.*"))


def test_materialize_cleanup_ignores_symlinks_and_chmod_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, runtime_assets, snapshot, _ = _snapshot_and_materialize(tmp_path)

    def leave_symlink_then_fail(_store, _ref, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        (destination.parent / "cleanup-link").symlink_to(tmp_path, target_is_directory=True)
        raise RuntimeAssetSnapshotError("injected_copy_failure")

    original_chmod = Path.chmod

    def attacked_chmod(path: Path, mode: int, *args, **kwargs) -> None:
        if path.name == "objects" or (
            path.parent == tmp_path and path.name.startswith(".cleanup.")
        ):
            raise PermissionError("injected cleanup chmod failure")
        original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(runtime_assets_module, "_copy_cas_ref", leave_symlink_then_fail)
    monkeypatch.setattr(Path, "chmod", attacked_chmod)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets.materialize(snapshot.manifest, tmp_path / "cleanup")
    assert captured.value.reason == "injected_copy_failure"
    assert not list(tmp_path.glob(".cleanup.*"))


@pytest.mark.parametrize(
    ("constant", "limit", "reason"),
    [
        ("RUNTIME_ASSET_MAX_ASSETS", 1, "asset_count_limit"),
        ("RUNTIME_ASSET_MAX_DIRECTORIES", 1, "directory_count_limit"),
        ("RUNTIME_ASSET_MAX_FILES", 1, "file_count_limit"),
        ("RUNTIME_ASSET_MAX_SINGLE_FILE_BYTES", 1, "single_file_bytes_limit"),
        ("RUNTIME_ASSET_MAX_TOTAL_BYTES", 1, "total_bytes_limit"),
    ],
)
def test_snapshot_resource_limits_fail_before_unbounded_cas_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    limit: int,
    reason: str,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    monkeypatch.setattr(runtime_assets_module, constant, limit)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )
    assert captured.value.reason == reason


@pytest.mark.parametrize(
    ("constant", "reason"),
    [
        ("RUNTIME_ASSET_MAX_ASSETS", "asset_count_limit"),
        ("RUNTIME_ASSET_MAX_DIRECTORIES", "directory_count_limit"),
        ("RUNTIME_ASSET_MAX_FILES", "file_count_limit"),
        ("RUNTIME_ASSET_MAX_SINGLE_FILE_BYTES", "single_file_bytes_limit"),
        ("RUNTIME_ASSET_MAX_TOTAL_BYTES", "total_bytes_limit"),
    ],
)
def test_manifest_limits_are_rechecked_by_materialize_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    reason: str,
) -> None:
    resolved, catalog, _, runtime_assets, snapshot, materialized = _snapshot_and_materialize(
        tmp_path
    )
    monkeypatch.setattr(runtime_assets_module, constant, 1)

    with pytest.raises(RuntimeAssetSnapshotError) as materialize_error:
        runtime_assets.materialize(snapshot.manifest, tmp_path / "limited-inputs")
    assert materialize_error.value.reason == reason
    with pytest.raises(RuntimeAssetSnapshotError) as verify_error:
        verify_runtime_asset_snapshot(
            root=materialized.root,
            manifest_path=materialized.manifest_path,
            expected_sha256=snapshot.manifest.sha256,
            resolved=resolved,
            catalog=catalog,
        )
    assert verify_error.value.reason == reason


def _urdf_selection(
    tmp_path: Path,
) -> tuple[ResolvedSceneSpec, AssetCatalog, Path, Path, Path]:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    model = next(value for value in entry.models if value.model_id == item.model_id)
    model_root = Path(entry.asset_path) / "selected-urdf-model"
    model_root.mkdir()
    metadata = model_root / "model_data.json"
    metadata.write_text('{"scale":[1,1,1]}\n', encoding="utf-8")
    mobility = model_root / "mobility.urdf"
    mobility.write_text('<robot name="fixture"><link name="base"/></robot>\n', encoding="utf-8")
    selected_model = model.model_copy(
        update={
            "model_path": str(model_root),
            "metadata_path": str(metadata),
            "visual_path": None,
            "collision_path": None,
            "urdf_path": str(mobility),
        }
    )
    catalog = _replace_entry(
        catalog,
        item.asset_id,
        load_type="urdf",
        models=(selected_model,),
    )
    resolved = _replace_object(
        resolved,
        item.object_id,
        load_type="urdf",
        source_files=(str(metadata), str(mobility)),
    ).model_copy(update={"asset_catalog_sha256": catalog.digest()})
    return resolved, catalog, allowed_root, model_root, mobility


@pytest.mark.parametrize(
    ("phase", "exit_code"),
    [
        ("preflight", RUNTIME_ASSET_PREFLIGHT_EXIT_CODE),
        ("postflight", RUNTIME_ASSET_DRIFT_EXIT_CODE),
    ],
)
def test_worker_error_has_a_closed_phase_and_reserved_exit_code(
    phase: str,
    exit_code: int,
) -> None:
    underlying = RuntimeAssetSnapshotError("fixture_reason", "fixture message")
    error = RuntimeAssetWorkerError(phase, underlying)

    assert error.phase == phase
    assert error.exit_code == exit_code
    assert error.reason == "fixture_reason"
    assert str(error) == "fixture message"

    with pytest.raises(ValueError, match="phase"):
        RuntimeAssetWorkerError("unknown", underlying)


def test_rigid_snapshot_can_be_verified_without_reopening_the_catalog(tmp_path: Path) -> None:
    resolved, _, _, _, snapshot, materialized = _snapshot_and_materialize(tmp_path)

    verified = verify_runtime_asset_snapshot(
        root=materialized.root,
        manifest_path=materialized.manifest_path,
        expected_sha256=snapshot.manifest.sha256,
        resolved=resolved,
        catalog=None,
    )

    assert set(verified.object_roots) == {item.object_id for item in resolved.objects}


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("entry_duplicate", "catalog_entry_duplicate"),
        ("entry_missing", "catalog_entry_missing"),
        ("model_missing", "catalog_model_missing"),
        ("source_mismatch", "source_files_mismatch"),
    ],
)
def test_catalog_loader_root_reconstruction_rejects_ambiguous_inputs(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, _, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    if attack == "entry_duplicate":
        catalog = catalog.model_copy(update={"entries": (*catalog.entries, entry)})
    elif attack == "entry_missing":
        catalog = catalog.model_copy(
            update={"entries": tuple(value for value in catalog.entries if value != entry)}
        )
    elif attack == "model_missing":
        catalog = _replace_entry(catalog, item.asset_id, models=())
    else:
        resolved = _replace_object(
            resolved,
            item.object_id,
            source_files=item.source_files[:-1],
        )

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._catalog_loader_roots(resolved, catalog)

    assert captured.value.reason == reason


def test_catalog_loader_roots_include_generated_provenance(tmp_path: Path) -> None:
    resolved, catalog, _, _ = _asset_fixture(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    provenance = Path(entry.asset_path) / "generation_provenance.json"
    provenance.write_text('{"generator":"fixture"}\n', encoding="utf-8")
    resolved = _replace_object(
        resolved,
        item.object_id,
        generation_metadata_path=str(provenance),
        source_files=(*item.source_files, str(provenance)),
    )

    roots = runtime_assets_module._catalog_loader_roots(resolved, catalog)

    assert "generation_provenance.json" in roots[item.asset_id]


def test_snapshot_rejects_urdf_outside_the_selected_model_root(tmp_path: Path) -> None:
    resolved, catalog, allowed_root, model_root, mobility = _urdf_selection(tmp_path)
    item = resolved.objects[0]
    entry = next(value for value in catalog.entries if value.asset_id == item.asset_id)
    model = entry.models[0]
    alternate = model_root.parent / "alternate.urdf"
    alternate.write_bytes(mobility.read_bytes())
    catalog = _replace_model(
        catalog,
        item.asset_id,
        item.model_id,
        urdf_path=str(alternate),
    )
    resolved = _replace_object(
        resolved,
        item.object_id,
        source_files=(model.metadata_path, str(alternate)),
    ).model_copy(update={"asset_catalog_sha256": catalog.digest()})

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(LocalArtifactStore(tmp_path / "cas")).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )

    assert captured.value.reason == "runtime_urdf_unsupported"


def test_loader_graph_is_cycle_safe_and_accepts_embedded_gltf_data() -> None:
    records = {
        "mesh.obj": {"path": "mesh.obj", "payload": b"mtllib material.mtl material.mtl\n"},
        "material.mtl": {"path": "material.mtl", "payload": b"map_Kd mesh.obj\n"},
        "embedded.gltf": {
            "path": "embedded.gltf",
            "payload": (
                b'{"asset":{"version":"2.0"},'
                b'"buffers":[{"uri":"data:application/octet-stream;base64,AA=="}],'
                b'"images":[{"uri":"data:image/png;base64,AA=="}]}'
            ),
        },
    }

    runtime_assets_module._validate_loader_reference_graph(
        asset_id="fixture",
        required_files=frozenset({"mesh.obj", "embedded.gltf"}),
        records=records,
        read_payload=lambda record: record["payload"],
    )

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._validate_loader_reference_graph(
            asset_id="fixture",
            required_files=("missing.obj",),
            records={},
            read_payload=lambda record: record["payload"],
        )
    assert captured.value.reason == "loader_reference_missing"


def _glb_payload(document: dict, binary: bytes | None = None) -> bytes:
    json_payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_payload += b" " * (-len(json_payload) % 4)
    chunks = len(json_payload).to_bytes(4, "little") + b"JSON" + json_payload
    if binary is not None:
        binary += b"\x00" * (-len(binary) % 4)
        chunks += len(binary).to_bytes(4, "little") + b"BIN\x00" + binary
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(chunks)).to_bytes(4, "little") + chunks


def _raw_glb(*chunks: tuple[bytes, bytes], version: int = 2) -> bytes:
    body = b"".join(
        len(payload).to_bytes(4, "little") + chunk_type + payload for chunk_type, payload in chunks
    )
    return b"glTF" + version.to_bytes(4, "little") + (12 + len(body)).to_bytes(4, "little") + body


def _glb_with_truncated_trailing_header() -> bytes:
    payload = _raw_glb((b"JSON", b"{}  ")) + b"tail"
    return payload[:8] + len(payload).to_bytes(4, "little") + payload[12:]


@pytest.mark.parametrize(
    ("path", "payload", "reason"),
    [
        (
            "mesh.glb",
            _glb_payload({"asset": {"version": "2.0"}, "buffers": [{"uri": "outside.bin"}]}),
            "loader_reference_missing",
        ),
        (
            "mesh.dae",
            b"<COLLADA><library_images><image><init_from>outside.png</init_from>"
            b"</image></library_images></COLLADA>",
            "loader_reference_missing",
        ),
        ("mesh.fbx", b"opaque loader document", "loader_document_unsupported"),
    ],
)
def test_loader_graph_rejects_unbound_binary_xml_and_opaque_dependencies(
    path: str,
    payload: bytes,
    reason: str,
) -> None:
    records = {path: {"path": path, "payload": payload}}

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._validate_loader_reference_graph(
            asset_id="fixture",
            required_files=(path,),
            records=records,
            read_payload=lambda record: record["payload"],
        )

    assert captured.value.reason == reason


def test_loader_graph_accepts_closed_glb_and_collada_dependencies() -> None:
    records = {
        "mesh.glb": {
            "path": "mesh.glb",
            "payload": _glb_payload(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [{"byteLength": 4}],
                    "images": [{"bufferView": 0, "mimeType": "image/png"}],
                },
                b"data",
            ),
        },
        "scene.dae": {
            "path": "scene.dae",
            "payload": (
                b"<COLLADA><library_images><image><init_from>texture.png</init_from>"
                b'</image></library_images><instance_geometry url="other.dae#mesh"/></COLLADA>'
            ),
        },
        "texture.png": {"path": "texture.png", "payload": b"png"},
        "other.dae": {"path": "other.dae", "payload": b"<COLLADA/>"},
    }

    runtime_assets_module._validate_loader_reference_graph(
        asset_id="fixture",
        required_files=("mesh.glb", "scene.dae"),
        records=records,
        read_payload=lambda record: record["payload"],
    )
    assert (
        runtime_assets_module._loader_references(
            ".glb",
            _raw_glb((b"JSON", b"{}  "), (b"EXT0", b"data")),
        )
        == ()
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        _raw_glb((b"JSON", b"{}  "), version=1),
        b"glTF" + (2).to_bytes(4, "little") + (999).to_bytes(4, "little") + b"12345678",
        _glb_with_truncated_trailing_header(),
        _raw_glb((b"JSON", b"{}  ")) + b"tail",
        _raw_glb((b"JSON", b"{")),
        _raw_glb((b"BIN\x00", b"data")),
        _raw_glb((b"JSON", b"{}  "), (b"JSON", b"{}  ")),
        _raw_glb((b"JSON", b"{}  "), (b"BIN\x00", b"data"), (b"BIN\x00", b"more")),
        _raw_glb((b"JSON", b"")),
        _glb_payload({"asset": {"version": "2.0"}, "buffers": [{}]}),
        _glb_payload(
            {"asset": {"version": "2.0"}, "buffers": [{}, {}]},
            b"data",
        ),
        _glb_payload({"asset": {"version": "2.0"}, "images": [{}]}),
    ],
)
def test_glb_container_attacks_fail_closed(payload: bytes) -> None:
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._loader_references(".glb", payload)

    assert captured.value.reason == "loader_document_invalid"


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        (".obj", b"\xff"),
        (".urdf", b'<!DOCTYPE robot SYSTEM "outside"><robot/>'),
        (".obj", b"mtllib\n"),
        (".mtl", b"# ignored\nmap_Kd\n"),
        (".dae", b'<!DOCTYPE COLLADA SYSTEM "outside"><COLLADA/>'),
        (".dae", b"<COLLADA><image><init_from> </init_from></image></COLLADA>"),
        (".gltf", b"[]"),
        (".gltf", b'{"buffers":{}}'),
        (".gltf", b'{"buffers":[1]}'),
        (".gltf", b'{"buffers":[{}]}'),
        (".gltf", b'{"images":[{"uri":1}]}'),
        (".gltf", b'{"images":[{}]}'),
        (".gltf", b"{"),
    ],
)
def test_loader_documents_fail_closed_on_malformed_dependency_syntax(
    suffix: str,
    payload: bytes,
) -> None:
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._loader_references(suffix, payload)

    assert captured.value.reason == "loader_document_invalid"


@pytest.mark.parametrize(
    "reference",
    ["", "bad\\path.obj", "bad%2fpath.obj", "https://invalid/mesh.obj", "#fragment"],
)
def test_loader_reference_normalization_rejects_encoded_or_ambiguous_paths(
    reference: str,
) -> None:
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._normalize_loader_reference("mesh.obj", reference)

    assert captured.value.reason == "loader_reference_unsafe"


def test_loader_reference_normalization_handles_dot_and_rejects_an_empty_result() -> None:
    assert (
        runtime_assets_module._normalize_loader_reference("models/mesh.obj", "./material.mtl")
        == "models/material.mtl"
    )
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._normalize_loader_reference("mesh.obj", ".")
    assert captured.value.reason == "loader_reference_unsafe"


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("catalog_none", "asset_catalog_required"),
        ("entry_missing", "catalog_entry_missing"),
        ("model_missing", "catalog_model_missing"),
        ("urdf_path", "runtime_urdf_unsupported"),
        ("model_root_missing", "catalog_model_root_missing"),
        ("model_root_file", "catalog_model_root_missing"),
        ("urdf_missing", "runtime_urdf_unsupported"),
        ("urdf_directory", "runtime_urdf_unsupported"),
    ],
)
def test_verified_urdf_object_roots_fail_closed_after_tree_verification(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    resolved, catalog, _, model_root, mobility = _urdf_selection(tmp_path)
    item = resolved.objects[0]
    root = Path(catalog.objects_root)
    if attack == "catalog_none":
        attacked_catalog = None
    elif attack == "entry_missing":
        attacked_catalog = catalog.model_copy(
            update={
                "entries": tuple(
                    value for value in catalog.entries if value.asset_id != item.asset_id
                )
            }
        )
    elif attack == "model_missing":
        attacked_catalog = _replace_entry(catalog, item.asset_id, models=())
    elif attack == "urdf_path":
        attacked_catalog = _replace_model(
            catalog,
            item.asset_id,
            item.model_id,
            urdf_path=str(model_root.parent / "alternate.urdf"),
        )
    else:
        attacked_catalog = catalog
        if attack == "model_root_missing":
            runtime_assets_module.shutil.rmtree(model_root)
        elif attack == "model_root_file":
            runtime_assets_module.shutil.rmtree(model_root)
            model_root.write_bytes(b"not a directory")
        elif attack == "urdf_missing":
            mobility.unlink()
        else:
            mobility.unlink()
            mobility.mkdir()

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._verified_object_roots(
            root,
            resolved=resolved,
            catalog=attacked_catalog,
        )

    assert captured.value.reason == reason


def test_cas_layout_rejects_invalid_components_and_creation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    store.root.mkdir()
    with pytest.raises(RuntimeAssetSnapshotError) as invalid:
        with runtime_assets_module._open_cas_directory(store, ".."):
            pass
    assert invalid.value.reason == "cas_layout_unsafe"

    original_mkdir = runtime_assets_module.os.mkdir

    def attacked_mkdir(path, mode=0o777, *, dir_fd=None):
        if str(path) == "sha256" and dir_fd is not None:
            raise PermissionError("injected CAS namespace failure")
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(runtime_assets_module.os, "mkdir", attacked_mkdir)
    with pytest.raises(RuntimeAssetSnapshotError) as unavailable:
        with runtime_assets_module._open_cas_directory(store, "sha256"):
            pass
    assert unavailable.value.reason == "cas_layout_unsafe"


def test_materialize_rejects_member_growth_after_the_size_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, artifact_store, _, snapshot, _ = _snapshot_and_materialize(tmp_path)
    ref = snapshot.members[0]
    source = artifact_store.root / "sha256" / ref.sha256[:2] / ref.sha256
    original_read = runtime_assets_module.os.read
    injected = False

    def attacked_read(descriptor: int, size: int) -> bytes:
        nonlocal injected
        payload = original_read(descriptor, size)
        if not injected and _fd_path(descriptor) == source and payload:
            injected = True
            return payload + b"x"
        return payload

    monkeypatch.setattr(runtime_assets_module.os, "read", attacked_read)
    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._copy_cas_ref(
            artifact_store,
            ref,
            tmp_path / "grown-copy",
        )
    assert captured.value.reason == "artifact_identity_mismatch"


@pytest.mark.parametrize("attack", ["content", "symlink", "growth", "short", "identity"])
def test_snapshot_rejects_existing_cas_member_attacks_without_unbounded_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    resolved, catalog, allowed_root, _ = _asset_fixture(tmp_path)
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    model = catalog.entries[0].models[0]
    assert model.collision_path is not None
    payload = Path(model.collision_path).read_bytes()
    digest = _sha256(payload)
    member = artifact_store.root / "sha256" / digest[:2] / digest
    member.parent.mkdir(parents=True)
    if attack == "symlink":
        target = tmp_path / "existing-target"
        target.write_bytes(payload)
        member.symlink_to(target)
    else:
        member.write_bytes(b"x" * len(payload) if attack == "content" else payload)
        identity = (member.stat().st_dev, member.stat().st_ino)
        if attack in {"growth", "short"}:
            original_read = runtime_assets_module.os.read
            injected = False

            def attacked_read(descriptor: int, size: int) -> bytes:
                nonlocal injected
                chunk = original_read(descriptor, size)
                info = os.fstat(descriptor)
                if not injected and (info.st_dev, info.st_ino) == identity:
                    injected = True
                    return chunk + b"x" if attack == "growth" else b""
                return chunk

            monkeypatch.setattr(runtime_assets_module.os, "read", attacked_read)
        elif attack == "identity":
            original_fstat = runtime_assets_module.os.fstat
            calls = 0

            def attacked_fstat(descriptor: int):
                nonlocal calls
                value = original_fstat(descriptor)
                if (value.st_dev, value.st_ino) == identity:
                    calls += 1
                    if calls == 2:
                        return _changed_stat(value, st_ctime_ns=value.st_ctime_ns + 1)
                return value

            monkeypatch.setattr(runtime_assets_module.os, "fstat", attacked_fstat)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        RuntimeAssetStore(artifact_store).snapshot(
            resolved=resolved,
            catalog=catalog,
            allowed_roots=(allowed_root,),
        )
    assert captured.value.reason == "cas_object_corrupt"


@pytest.mark.parametrize("attack", ["mkdir", "lstat"])
def test_cas_root_creation_and_identity_failures_are_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    if attack == "mkdir":
        original_mkdir = Path.mkdir

        def attacked_mkdir(path: Path, *args, **kwargs) -> None:
            if path == store.root:
                raise PermissionError("injected root creation failure")
            original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", attacked_mkdir)
    else:
        store.root.mkdir()
        original_lstat = Path.lstat

        def attacked_lstat(path: Path, *args, **kwargs):
            if path == store.root:
                raise PermissionError("injected root identity failure")
            return original_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", attacked_lstat)

    with pytest.raises(RuntimeAssetSnapshotError) as captured:
        runtime_assets_module._cas_root(store)
    assert captured.value.reason == "cas_root_unsafe"


def test_cas_temporary_names_retry_collisions_and_bound_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    incoming_fd = os.open(incoming, os.O_RDONLY | os.O_DIRECTORY)
    try:
        (incoming / ".fixture.same").write_bytes(b"occupied")
        original_token_hex = runtime_assets_module.secrets.token_hex
        tokens = iter(("same", "unique"))
        monkeypatch.setattr(runtime_assets_module.secrets, "token_hex", lambda _size: next(tokens))
        descriptor, name = runtime_assets_module._create_cas_temporary(
            incoming_fd,
            prefix=".fixture.",
        )
        os.close(descriptor)
        os.unlink(name, dir_fd=incoming_fd)
        assert name == ".fixture.unique"
        monkeypatch.setattr(runtime_assets_module.secrets, "token_hex", original_token_hex)

        original_open = runtime_assets_module.os.open
        with monkeypatch.context() as scoped:

            def denied_open(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd == incoming_fd:
                    raise PermissionError("injected temporary create failure")
                return original_open(path, flags, mode, dir_fd=dir_fd)

            scoped.setattr(runtime_assets_module.os, "open", denied_open)
            with pytest.raises(RuntimeAssetSnapshotError) as denied:
                runtime_assets_module._create_cas_temporary(incoming_fd, prefix=".denied.")
            assert denied.value.reason == "cas_layout_unsafe"

        with monkeypatch.context() as scoped:

            def colliding_open(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd == incoming_fd:
                    raise FileExistsError("injected bounded collisions")
                return original_open(path, flags, mode, dir_fd=dir_fd)

            scoped.setattr(runtime_assets_module.os, "open", colliding_open)
            with pytest.raises(RuntimeAssetSnapshotError) as exhausted:
                runtime_assets_module._create_cas_temporary(incoming_fd, prefix=".exhausted.")
            assert exhausted.value.reason == "cas_layout_unsafe"
    finally:
        os.close(incoming_fd)


def test_cas_install_and_cleanup_failures_are_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = tmp_path / "incoming"
    shard = tmp_path / "shard"
    incoming.mkdir()
    shard.mkdir()
    incoming_fd = os.open(incoming, os.O_RDONLY | os.O_DIRECTORY)
    shard_fd = os.open(shard, os.O_RDONLY | os.O_DIRECTORY)
    try:
        temporary_fd, temporary_name = runtime_assets_module._create_cas_temporary(
            incoming_fd,
            prefix=".identity.",
        )
        os.write(temporary_fd, b"payload")
        original_stat = runtime_assets_module.os.stat

        def attacked_stat(path, *args, **kwargs):
            value = original_stat(path, *args, **kwargs)
            if kwargs.get("dir_fd") == shard_fd:
                return _changed_stat(value, st_ino=value.st_ino + 1)
            return value

        with monkeypatch.context() as scoped:
            scoped.setattr(runtime_assets_module.os, "stat", attacked_stat)
            with pytest.raises(RuntimeAssetSnapshotError) as raced:
                runtime_assets_module._install_cas_temporary(
                    incoming_fd=incoming_fd,
                    temporary_fd=temporary_fd,
                    temporary_name=temporary_name,
                    shard_fd=shard_fd,
                    digest="a" * 64,
                )
            assert raced.value.reason == "cas_write_race"
        os.close(temporary_fd)

        failed_fd, failed_name = runtime_assets_module._create_cas_temporary(
            incoming_fd,
            prefix=".failure.",
        )
        with monkeypatch.context() as scoped:
            scoped.setattr(
                runtime_assets_module.os,
                "fchmod",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("injected")),
            )
            with pytest.raises(RuntimeAssetSnapshotError) as failed:
                runtime_assets_module._install_cas_temporary(
                    incoming_fd=incoming_fd,
                    temporary_fd=failed_fd,
                    temporary_name=failed_name,
                    shard_fd=shard_fd,
                    digest="b" * 64,
                )
            assert failed.value.reason == "cas_write_failed"
        os.close(failed_fd)
        os.unlink(failed_name, dir_fd=incoming_fd)

        cleanup_name = ".cleanup"
        (incoming / cleanup_name).write_bytes(b"payload")
        with monkeypatch.context() as scoped:
            scoped.setattr(
                runtime_assets_module.os,
                "unlink",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("injected")),
            )
            with pytest.raises(RuntimeAssetSnapshotError) as cleanup:
                runtime_assets_module._unlink_cas_temporary(incoming_fd, cleanup_name)
            assert cleanup.value.reason == "cas_cleanup_failed"
        os.unlink(cleanup_name, dir_fd=incoming_fd)
    finally:
        os.close(incoming_fd)
        os.close(shard_fd)

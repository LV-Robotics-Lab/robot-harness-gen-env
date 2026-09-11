from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

import self_improving.harness as harness
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas.x2env_compile_evidence import (
    AssetModelSubjectV1,
    AssetRepresentationV1,
    asset_loader_closure_sha256,
    canonical_evidence_sha256,
)

try:
    from self_improving.harness.legacy_genesis_asset_staging import (
        LegacyGenesisAssetSourceBinding,
        LegacyGenesisAssetStageError,
        StagedLegacyGenesisAssetRepresentation,
        stage_legacy_genesis_asset_representation,
    )
except ImportError:
    LegacyGenesisAssetSourceBinding = None  # type: ignore[assignment,misc]
    LegacyGenesisAssetStageError = None  # type: ignore[assignment,misc]
    StagedLegacyGenesisAssetRepresentation = None  # type: ignore[assignment,misc]
    stage_legacy_genesis_asset_representation = None  # type: ignore[assignment]


_ARCHIVE_ROOT = Path(
    "/home/jingxiang/gujie/gen-env/data/genesis_history/"
    "output_before_retest_20260908_011810/场景图物理_mouse_001/"
    "02_scene/assets/iter_0"
)
_MANIFEST_SHA256 = "a7d20cc64d171f1a720fdeefe0d38130a4d85eef3b90b9300241bc790592f21c"
_ROOT_LOGICAL_PATH = "urdf/bnjzzp.urdf"
_MEMBERS = (
    (
        "physics.json",
        179,
        "0533a99f1e29f3e51c4237316735d42924319d75ae9333e461561a5b8ed5d2fc",
        "metadata",
    ),
    (
        "urdf/bnjzzp.urdf",
        3057,
        "6a3a9d6a830de945e21ec87c76c5409d6e802a418fcee78e8d43a8b6498ae9bf",
        "urdf",
    ),
    (
        "urdf/material/pink_mouse_bnjzzp_link_textured_mesh_Kd.png",
        1_503_582,
        "4da8ddbc1dd0baa74472d583eed7ff2872249dbeb80ca003903c262fcc753dec",
        "visual",
    ),
    (
        "urdf/material/pink_mouse_bnjzzp_link_textured_mesh_Pm.png",
        128_056,
        "566bc42e66955fdbdca2bee60702102f6625e4197f322f796561a1a1d366f6c1",
        "visual",
    ),
    (
        "urdf/material/pink_mouse_bnjzzp_link_textured_mesh_Pr.png",
        784_489,
        "68e280cd7da6079d4ca41e1e38351d4887762330dc7bedef8bd135e731600f2e",
        "visual",
    ),
    (
        "urdf/shape/collision/pink_mouse_bnjzzp_link_textured_mesh_collision_0.obj",
        3471,
        "7c27b162b0fd53f25a20e98927404cfbae29b7d9263f05d239a697897b1d5161",
        "collision",
    ),
    (
        "urdf/shape/collision/pink_mouse_bnjzzp_link_textured_mesh_collision_1.obj",
        3404,
        "ad7175a9aad51981dc8dc06cc651ad34a94b405d58f0d3d3c34070ed867801a7",
        "collision",
    ),
    (
        "urdf/shape/collision/pink_mouse_bnjzzp_link_textured_mesh_collision_2.obj",
        3332,
        "0dbd17c511e1d13c31b31ab8862abb29406313621e1cdaff2e9860dd220dfc8c",
        "collision",
    ),
    (
        "urdf/shape/collision/pink_mouse_bnjzzp_link_textured_mesh_collision_3.obj",
        3383,
        "0a302b1197da97c524fde576cb4716174be8d1c3ff84e72baad9d610ab6ef71d",
        "collision",
    ),
    (
        "urdf/shape/collision/pink_mouse_bnjzzp_link_textured_mesh_collision_4.obj",
        3466,
        "44330c8f59e708b133b36071d581939202f9491286c857f049f01752df31fa8e",
        "collision",
    ),
    (
        "urdf/shape/collision/pink_mouse_bnjzzp_link_textured_mesh_collision_5.obj",
        3403,
        "7fdb17256045ee0804d209559a7564d45c3d16ec47b7700c9a907f325b23fe45",
        "collision",
    ),
    (
        "urdf/shape/collision/pink_mouse_bnjzzp_link_textured_mesh_collision_6.obj",
        3404,
        "e36e60e9ca4d3bacaf34761e2b555471308b93178ba1255ffd61a2724fd00287",
        "collision",
    ),
    (
        "urdf/shape/collision/pink_mouse_bnjzzp_link_textured_mesh_collision_7.obj",
        3417,
        "6346c9cb2f5e143c75dbe918918b323431a0fa411374fa2865ac84ccfdfb6ff0",
        "collision",
    ),
    (
        "urdf/shape/visual/pink_mouse_bnjzzp_link_textured_mesh.mtl",
        375,
        "385f3a32d796ed8449e062334842505fd7585a6a1dca76a6f82d1831fbc56f6e",
        "visual",
    ),
    (
        "urdf/shape/visual/pink_mouse_bnjzzp_link_textured_mesh.obj",
        2_879_314,
        "814dc3fae2b15433b21e517f3ebf4bd19d1cebc5cf03d2820c1096a6c08b90ac",
        "visual",
    ),
)


pytestmark = pytest.mark.skipif(
    not _ARCHIVE_ROOT.is_dir(),
    reason="audited Gujie legacy mouse archive is not installed",
)


def _require_api() -> None:
    assert LegacyGenesisAssetSourceBinding is not None, (
        "legacy Genesis representation staging seam is absent"
    )
    assert LegacyGenesisAssetStageError is not None
    assert StagedLegacyGenesisAssetRepresentation is not None
    assert stage_legacy_genesis_asset_representation is not None
    assert harness.LegacyGenesisAssetSourceBinding is LegacyGenesisAssetSourceBinding
    assert harness.LegacyGenesisAssetStageError is LegacyGenesisAssetStageError
    assert harness.StagedLegacyGenesisAssetRepresentation is StagedLegacyGenesisAssetRepresentation
    assert (
        harness.stage_legacy_genesis_asset_representation
        is stage_legacy_genesis_asset_representation
    )


def _subject(source_record_sha256: str = _MANIFEST_SHA256) -> AssetModelSubjectV1:
    return AssetModelSubjectV1(
        namespace="gujie_genenv_legacy",
        asset_key="simfoundry_pink_mouse_7915827ae4309660",
        ledger_asset_id="legacy_mouse_001",
        model_id=0,
        backend="genesis",
        source_record_sha256=source_record_sha256,
    )


def _binding(root: Path, *, expected_sha256: str = _MANIFEST_SHA256):
    assert LegacyGenesisAssetSourceBinding is not None
    return LegacyGenesisAssetSourceBinding(
        root=root,
        manifest_relative_path="asset.json",
        expected_manifest_sha256=expected_sha256,
    )


def _stage(store: LocalArtifactStore, root: Path, *, expected_sha256: str = _MANIFEST_SHA256):
    assert stage_legacy_genesis_asset_representation is not None
    return stage_legacy_genesis_asset_representation(
        artifact_store=store,
        source=_binding(root, expected_sha256=expected_sha256),
        subject=_subject(expected_sha256),
    )


def _cas_manifest(store: LocalArtifactStore) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            str(path.relative_to(store.root)),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(store.root.rglob("*"))
        if path.is_file()
    )


def _copy_source(tmp_path: Path, name: str = "source") -> Path:
    target = tmp_path / name
    shutil.copytree(_ARCHIVE_ROOT, target)
    return target


def _rewrite_manifest(root: Path, mutate: Any) -> str:
    manifest = root / "asset.json"
    value = json.loads(manifest.read_bytes())
    mutate(value)
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_real_legacy_mouse_bytes_stage_a_portable_unqualified_representation(
    tmp_path: Path,
) -> None:
    _require_api()
    assert hashlib.sha256((_ARCHIVE_ROOT / "asset.json").read_bytes()).hexdigest() == (
        _MANIFEST_SHA256
    )
    assert tuple(item[0] for item in _MEMBERS) == tuple(sorted(item[0] for item in _MEMBERS))
    store = LocalArtifactStore(tmp_path / "fresh-cas")
    assert _cas_manifest(store) == ()

    first = _stage(store, _ARCHIVE_ROOT)

    assert isinstance(first, StagedLegacyGenesisAssetRepresentation)
    assert tuple(field.name for field in fields(first)) == ("representation", "representation_ref")
    representation = first.representation
    assert type(representation) is AssetRepresentationV1
    assert representation.schema_version == "harness.asset_representation.v1"
    assert representation.subject == _subject()
    assert representation.representation_kind == "urdf"
    assert representation.root_logical_path == _ROOT_LOGICAL_PATH
    assert representation.loader_scale == (1.0, 1.0, 1.0)
    assert len(representation.members) == 15
    assert (
        tuple(
            (member.logical_path, member.artifact.bytes, member.artifact.sha256, member.role)
            for member in representation.members
        )
        == _MEMBERS
    )
    assert representation.collision_member_paths == tuple(
        item[0] for item in _MEMBERS if item[3] == "collision"
    )
    assert representation.visual_member_paths == tuple(
        item[0] for item in _MEMBERS if item[3] == "visual"
    )
    assert representation.loader_closure_sha256 == asset_loader_closure_sha256(
        representation.subject,
        representation.root_logical_path,
        representation.members,
    )
    assert representation.representation_sha256 == canonical_evidence_sha256(
        representation,
        "representation_sha256",
    )
    assert (
        AssetRepresentationV1.model_validate_json(
            store.resolve(first.representation_ref).path.read_bytes()
        )
        == representation
    )
    assert first.representation_ref.schema_version == "harness.asset_representation.v1"
    assert len(_cas_manifest(store)) == 16
    rendered = json.dumps(representation.model_dump(mode="json"), sort_keys=True)
    assert str(_ARCHIVE_ROOT) not in rendered
    assert not {
        "runtime_lock",
        "qualification",
        "qualified",
        "status",
        "pass",
        "mode",
        "permissions",
        "simulator_executed",
    }.intersection(representation.model_fields_set)

    after_first = _cas_manifest(store)
    assert _stage(store, _ARCHIVE_ROOT) == first
    assert _cas_manifest(store) == after_first

    relocated = _copy_source(tmp_path, "relocated")
    for path in relocated.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    relocated_store = LocalArtifactStore(tmp_path / "relocated-fresh-cas")
    assert _stage(relocated_store, relocated) == first
    assert _cas_manifest(relocated_store) == after_first


@pytest.mark.parametrize(
    ("attack", "expected_code"),
    (
        ("absolute_entrypoint", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("absolute_member", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("manifest_digest", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("wrong_schema", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("unsorted_members", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("duplicate_member", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("declared_hash", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("member_hash", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("missing_member", "HARN_LEGACY_ASSET_SOURCE_UNAVAILABLE"),
        ("extra_member", "HARN_LEGACY_ASSET_CLOSURE_MISMATCH"),
        ("file_symlink", "HARN_LEGACY_ASSET_SOURCE_UNAVAILABLE"),
        ("directory_symlink", "HARN_LEGACY_ASSET_SOURCE_UNAVAILABLE"),
        ("root_symlink", "HARN_LEGACY_ASSET_SOURCE_UNAVAILABLE"),
    ),
)
def test_legacy_stage_rejects_nonportable_or_incomplete_source_before_cas_write(
    tmp_path: Path,
    attack: str,
    expected_code: str,
) -> None:
    _require_api()
    root = _copy_source(tmp_path)
    expected_manifest_sha256 = _MANIFEST_SHA256
    member = root / _ROOT_LOGICAL_PATH
    if attack == "absolute_entrypoint":
        expected_manifest_sha256 = _rewrite_manifest(
            root,
            lambda value: value.__setitem__("entrypoint", str(member)),
        )
    elif attack == "absolute_member":
        expected_manifest_sha256 = _rewrite_manifest(
            root,
            lambda value: value["files"][0].__setitem__("path", str(root / "physics.json")),
        )
    elif attack == "manifest_digest":
        (root / "asset.json").write_bytes((root / "asset.json").read_bytes() + b" ")
    elif attack == "wrong_schema":
        expected_manifest_sha256 = _rewrite_manifest(
            root,
            lambda value: value.__setitem__("schema_version", "genenv.other_asset.v1"),
        )
    elif attack == "unsorted_members":
        expected_manifest_sha256 = _rewrite_manifest(
            root,
            lambda value: value["files"].reverse(),
        )
    elif attack == "duplicate_member":
        expected_manifest_sha256 = _rewrite_manifest(
            root,
            lambda value: value["files"].append(dict(value["files"][0])),
        )
    elif attack == "declared_hash":
        expected_manifest_sha256 = _rewrite_manifest(
            root,
            lambda value: value["files"][1].__setitem__("sha256", "0" * 64),
        )
    elif attack == "member_hash":
        member.write_bytes(member.read_bytes() + b"\n")
    elif attack == "missing_member":
        member.unlink()
    elif attack == "extra_member":
        (root / "unexpected.bin").write_bytes(b"extra")
    elif attack == "file_symlink":
        backing = tmp_path / "backing.urdf"
        shutil.copyfile(member, backing)
        member.unlink()
        member.symlink_to(backing)
    elif attack == "directory_symlink":
        material = root / "urdf/material"
        backing = tmp_path / "material"
        shutil.copytree(material, backing)
        shutil.rmtree(material)
        material.symlink_to(backing, target_is_directory=True)
    else:
        actual = root
        root = tmp_path / "source-link"
        root.symlink_to(actual, target_is_directory=True)

    store = LocalArtifactStore(tmp_path / "fresh-cas")
    assert LegacyGenesisAssetStageError is not None
    with pytest.raises(LegacyGenesisAssetStageError) as captured:
        _stage(store, root, expected_sha256=expected_manifest_sha256)

    assert captured.value.code == expected_code
    assert _cas_manifest(store) == ()


def test_legacy_stage_rejects_absolute_manifest_locator_and_wrong_exact_types(
    tmp_path: Path,
) -> None:
    _require_api()
    store = LocalArtifactStore(tmp_path / "fresh-cas")
    assert LegacyGenesisAssetSourceBinding is not None
    assert stage_legacy_genesis_asset_representation is not None
    assert LegacyGenesisAssetStageError is not None
    with pytest.raises((TypeError, ValueError), match="manifest_relative_path|relative"):
        LegacyGenesisAssetSourceBinding(
            root=_ARCHIVE_ROOT,
            manifest_relative_path=str(_ARCHIVE_ROOT / "asset.json"),
            expected_manifest_sha256=_MANIFEST_SHA256,
        )
    with pytest.raises(TypeError, match="exact LocalArtifactStore"):
        stage_legacy_genesis_asset_representation(
            artifact_store=object(),
            source=_binding(_ARCHIVE_ROOT),
            subject=_subject(),
        )
    with pytest.raises(TypeError, match="exact AssetModelSubjectV1"):
        stage_legacy_genesis_asset_representation(
            artifact_store=store,
            source=_binding(_ARCHIVE_ROOT),
            subject=object(),
        )
    assert _cas_manifest(store) == ()

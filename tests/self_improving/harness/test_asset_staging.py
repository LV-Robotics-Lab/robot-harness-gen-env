"""Public S5 tests for exact-byte asset debt staging."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import self_improving.harness.asset_stage_verification as stage_verification
from self_improving.harness import (
    AssetRepairApplication,
    AssetStageError,
    AssetStageRequest,
    LocalAssetSourceSnapshotBinding,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.asset_stage_verification import (
    AssetStageVerificationError,
    materialize_verified_asset_stage,
    verify_asset_stage_result_authority,
)
from self_improving.harness.schemas.asset_repair import AssetRepairPlan, AssetRepairPlanRequest
from self_improving.harness.schemas.asset_staging import (
    AssetStageResult,
    StagedAssetMember,
    canonical_sha256,
    loader_closure_sha256,
)
from self_improving.harness.schemas.common import ArtifactRef

TRACER_INVENTORY = Path(__file__).parents[2] / "fixtures" / "asset_repair_tracer_inventory.json"
TRACER_SOURCE_SNAPSHOT = (
    Path(__file__).parents[2] / "fixtures" / "asset_repair_tracer_source_snapshot.json"
)
REAL_ROBOTWIN_ASSET_ROOT = os.environ.get("ROBOTWIN_ASSET_ROOT")


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _glb(
    *,
    external_buffer: str | None = None,
    binary: bytes | None = None,
    repeat_external_reference: bool = False,
) -> bytes:
    document: dict[str, object] = {"asset": {"version": "2.0"}}
    if external_buffer is not None:
        document["buffers"] = [{"byteLength": 4, "uri": external_buffer}]
        if repeat_external_reference:
            document["images"] = [{"uri": external_buffer}]
    elif binary is not None:
        document["buffers"] = [{"byteLength": len(binary)}]
    json_chunk = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)
    chunks = [struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk]
    if binary is not None:
        padded_binary = binary + b"\x00" * (-len(binary) % 4)
        chunks.append(struct.pack("<II", len(padded_binary), 0x004E4942) + padded_binary)
    body = b"".join(chunks)
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _put_json(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    schema_version: str,
    value: object,
):
    source = tmp_path / f"{name}.json"
    source.write_bytes(_canonical_bytes(value))
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _inventory_payload(
    files: dict[str, bytes],
    *,
    plate_logical_path: str = "objects/003_plate/visual/base0.glb",
) -> dict[str, object]:
    entries = []
    for asset_id, logical_path in (
        ("robotwin_003_plate", plate_logical_path),
        ("robotwin_071_can", "objects/071_can/visual/base0.glb"),
    ):
        payload = files[logical_path]
        entries.append(
            {
                "asset_id": asset_id,
                "ledger_bytes": 1,
                "ledger_path": f"ledgers/{asset_id}.json",
                "ledger_sha256": "a" * 64 if asset_id.endswith("plate") else "b" * 64,
                "recovery_probes": [
                    {
                        "availability": "available",
                        "declared_bytes": len(payload),
                        "declared_sha256": _sha256(payload),
                        "logical_path": logical_path,
                        "model_id": 0,
                        "observed_bytes": len(payload),
                        "observed_sha256": _sha256(payload),
                        "role": "visual",
                    }
                ],
                "source_cohort": "upstream",
                "violation_counts": [{"code": "missing", "count": 1}],
            }
        )
    return {
        "byte_probe_revision": None,
        "byte_probe_source": "local_unversioned_robotwin_assets",
        "entries": entries,
        "inventory_id": "p6_exact_stage_test",
        "schema_version": "harness.asset_debt_inventory.v1",
        "scope": "structural_tracer",
        "source_ledger_commit": "c" * 40,
        "source_population_ledger_count": 162,
        "source_population_primary_probe_count": 1101,
        "source_population_violation_count": 4082,
    }


@dataclass(frozen=True)
class _StageCase:
    root: Path
    files: dict[str, bytes]
    store: LocalArtifactStore
    inventory_ref: ArtifactRef
    plan: AssetRepairPlan
    plan_ref: ArtifactRef
    source_manifest: dict[str, object]
    source_manifest_ref: ArtifactRef

    def application(self, *, root: Path | None = None) -> AssetRepairApplication:
        return AssetRepairApplication(
            artifact_store=self.store,
            trusted_inventory_refs=(self.inventory_ref,),
            source_snapshots=(
                LocalAssetSourceSnapshotBinding(
                    manifest_ref=self.source_manifest_ref,
                    root=self.root if root is None else root,
                ),
            ),
        )

    def request(self) -> AssetStageRequest:
        return AssetStageRequest(
            schema_version="harness.asset_stage_request.v1",
            inventory_ref=self.inventory_ref,
            repair_plan_ref=self.plan_ref,
            source_snapshot_manifest_ref=self.source_manifest_ref,
        )


def _stage_case(
    tmp_path: Path,
    *,
    plate_reference: str = "mesh.bin",
    include_mesh: bool = True,
    plate_payload: bytes | None = None,
    plate_logical_path: str = "objects/003_plate/visual/base0.glb",
    repeat_external_reference: bool = False,
) -> _StageCase:
    source_root = tmp_path / "source"
    files = {
        plate_logical_path: (
            _glb(
                external_buffer=plate_reference,
                repeat_external_reference=repeat_external_reference,
            )
            if plate_payload is None
            else plate_payload
        ),
        "objects/071_can/visual/base0.glb": _glb(binary=b"can!"),
        "objects/003_plate/model_data0.json": _canonical_bytes(
            {"scale": [0.025, 0.025, 0.025]}
        ),
        "objects/071_can/model_data0.json": _canonical_bytes(
            {"scale": [0.05, 0.05, 0.05]}
        ),
    }
    if include_mesh:
        files["objects/003_plate/visual/mesh.bin"] = b"mesh"
    for logical_path, payload in files.items():
        _write(source_root / logical_path, payload)
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = _put_json(
        store,
        tmp_path,
        name="inventory",
        schema_version="harness.asset_debt_inventory.v1",
        value=_inventory_payload(files, plate_logical_path=plate_logical_path),
    )
    plan = AssetRepairApplication(
        artifact_store=store,
        trusted_inventory_refs=(inventory_ref,),
    ).plan(
        AssetRepairPlanRequest(
            schema_version="harness.asset_repair_plan_request.v1",
            inventory_ref=inventory_ref,
            selected_asset_ids=("robotwin_003_plate", "robotwin_071_can"),
        )
    )
    plan_ref = _put_json(
        store,
        tmp_path,
        name="repair_plan",
        schema_version="harness.asset_repair_plan.v1",
        value=plan.model_dump(mode="json"),
    )
    source_manifest = {
        "assets": [
            {
                "asset_id": asset_id,
                "logical_root": logical_root,
                "members": [
                    {
                        "bytes": len(files[path]),
                        "logical_path": path,
                        "sha256": _sha256(files[path]),
                    }
                    for path in sorted(files)
                    if path.startswith(logical_root + "/")
                ],
                "model_sidecars": [
                    {
                        "logical_path": f"{logical_root}/model_data0.json",
                        "model_id": 0,
                        "scale": (
                            [0.025, 0.025, 0.025]
                            if asset_id == "robotwin_003_plate"
                            else [0.05, 0.05, 0.05]
                        ),
                    }
                ],
            }
            for asset_id, logical_root in (
                ("robotwin_003_plate", "objects/003_plate"),
                ("robotwin_071_can", "objects/071_can"),
            )
        ],
        "capture_method": "exact_byte_manifest.v1",
        "inventory_ref": inventory_ref.model_dump(mode="json"),
        "schema_version": "harness.asset_source_snapshot_manifest.v1",
        "snapshot_id": "p6_exact_stage_test_snapshot",
        "source_kind": "local_unversioned_robotwin_assets",
        "source_revision": None,
    }
    source_manifest_ref = _put_json(
        store,
        tmp_path,
        name="source_snapshot",
        schema_version="harness.asset_source_snapshot_manifest.v1",
        value=source_manifest,
    )
    return _StageCase(
        root=source_root,
        files=files,
        store=store,
        inventory_ref=inventory_ref,
        plan=plan,
        plan_ref=plan_ref,
        source_manifest=source_manifest,
        source_manifest_ref=source_manifest_ref,
    )


def _with_manifest(
    case: _StageCase,
    tmp_path: Path,
    manifest: dict[str, object],
    *,
    encoded: bytes | None = None,
) -> _StageCase:
    source = tmp_path / f"source-snapshot-{len(tuple(case.store.root.rglob('*')))}.json"
    source.write_bytes(_canonical_bytes(manifest) if encoded is None else encoded)
    manifest_ref = case.store.put_file(
        source,
        name="source_snapshot",
        media_type="application/json",
        schema_version="harness.asset_source_snapshot_manifest.v1",
    )
    return replace(
        case,
        source_manifest=manifest,
        source_manifest_ref=manifest_ref,
    )


def _with_plan(
    case: _StageCase,
    tmp_path: Path,
    plan: dict[str, object],
    *,
    encoded: bytes | None = None,
) -> _StageCase:
    source = tmp_path / f"repair-plan-{len(tuple(case.store.root.rglob('*')))}.json"
    source.write_bytes(_canonical_bytes(plan) if encoded is None else encoded)
    plan_ref = case.store.put_file(
        source,
        name="repair_plan",
        media_type="application/json",
        schema_version="harness.asset_repair_plan.v1",
    )
    return replace(case, plan_ref=plan_ref)


def test_stage_includes_the_canonical_model_sidecar_in_each_loader_closure(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)

    result = case.application().stage(case.request())

    assert result.staged_member_count == 5
    assert result.assets[0].loader_closures[0].member_logical_paths == (
        "objects/003_plate/model_data0.json",
        "objects/003_plate/visual/base0.glb",
        "objects/003_plate/visual/mesh.bin",
    )
    assert result.assets[1].loader_closures[0].member_logical_paths == (
        "objects/071_can/model_data0.json",
        "objects/071_can/visual/base0.glb",
    )
    sidecars = [
        member
        for asset in result.assets
        for member in asset.members
        if member.logical_path.endswith(".json")
    ]
    assert all(member.artifact_ref.media_type == "application/json" for member in sidecars)
    assert all(member.artifact_ref.schema_version is None for member in sidecars)


@pytest.mark.parametrize(
    ("attack", "code", "message"),
    [
        ("undeclared", "HARN_INPUT_SCHEMA_INVALID", "model_sidecars"),
        ("duplicate_sidecars", "HARN_INPUT_SCHEMA_INVALID", "sorted and unique"),
        ("not_a_member", "HARN_INPUT_SCHEMA_INVALID", "sidecar must be a member"),
        ("wrong_path_model", "HARN_INPUT_SCHEMA_INVALID", "canonical model_data path"),
        ("wrong_plan_model", "HARN_ASSET_SOURCE_MANIFEST_MISMATCH", "model selection"),
        ("scale", "HARN_ASSET_MODEL_SIDECAR_INVALID", "scale does not match"),
        ("bad_json", "HARN_ASSET_MODEL_SIDECAR_INVALID", "invalid model sidecar"),
        ("duplicate_scale", "HARN_ASSET_MODEL_SIDECAR_INVALID", "model_sidecar_invalid"),
        ("nan", "HARN_ASSET_MODEL_SIDECAR_INVALID", "model_sidecar_invalid"),
        ("infinity", "HARN_ASSET_MODEL_SIDECAR_INVALID", "model_sidecar_invalid"),
        ("overflow", "HARN_ASSET_MODEL_SIDECAR_INVALID", "model_sidecar_scale_invalid"),
        ("zero", "HARN_ASSET_MODEL_SIDECAR_INVALID", "model_sidecar_scale_invalid"),
        ("negative", "HARN_ASSET_MODEL_SIDECAR_INVALID", "model_sidecar_scale_invalid"),
        ("bool", "HARN_ASSET_MODEL_SIDECAR_INVALID", "model_sidecar_scale_invalid"),
        ("swapped_sidecar", "HARN_ASSET_MODEL_SIDECAR_INVALID", "scale does not match"),
        (
            "json_model_id",
            "HARN_ASSET_MODEL_SIDECAR_INVALID",
            "model_sidecar_model_mismatch",
        ),
    ],
)
def test_stage_rejects_missing_or_inconsistent_model_sidecar_contracts(
    tmp_path: Path,
    attack: str,
    code: str,
    message: str,
) -> None:
    case = _stage_case(tmp_path)
    manifest = deepcopy(case.source_manifest)
    plate = manifest["assets"][0]
    sidecar = plate["model_sidecars"][0]
    path = sidecar["logical_path"]
    if attack == "undeclared":
        del plate["model_sidecars"]
    elif attack == "duplicate_sidecars":
        plate["model_sidecars"].append(deepcopy(sidecar))
    elif attack == "not_a_member":
        plate["members"] = [member for member in plate["members"] if member["logical_path"] != path]
    elif attack == "wrong_path_model":
        sidecar["model_id"] = 1
    elif attack == "wrong_plan_model":
        new_path = "objects/003_plate/model_data1.json"
        payload = case.files[path]
        _write(case.root / new_path, payload)
        plate["members"] = [member for member in plate["members"] if member["logical_path"] != path]
        plate["members"].append(
            {"bytes": len(payload), "logical_path": new_path, "sha256": _sha256(payload)}
        )
        plate["members"].sort(key=lambda member: member["logical_path"])
        sidecar["logical_path"] = new_path
        sidecar["model_id"] = 1
    elif attack == "scale":
        sidecar["scale"] = [0.5, 0.5, 0.5]
    else:
        payloads = {
            "bad_json": b'{"scale":',
            "duplicate_scale": b'{"scale":[0.025,0.025,0.025],"scale":[0.05,0.05,0.05]}',
            "nan": b'{"scale":[NaN,0.025,0.025]}',
            "infinity": b'{"scale":[Infinity,0.025,0.025]}',
            "overflow": b'{"scale":[1e999,0.025,0.025]}',
            "zero": b'{"scale":[0,0.025,0.025]}',
            "negative": b'{"scale":[-0.025,0.025,0.025]}',
            "bool": b'{"scale":[true,0.025,0.025]}',
            "json_model_id": _canonical_bytes(
                {"model_id": 1, "scale": [0.025, 0.025, 0.025]}
            ),
            "swapped_sidecar": case.files["objects/071_can/model_data0.json"],
        }
        payload = payloads[attack]
        _write(case.root / path, payload)
        member = next(member for member in plate["members"] if member["logical_path"] == path)
        member.update(bytes=len(payload), sha256=_sha256(payload))
    case = _with_manifest(case, tmp_path, manifest)

    with pytest.raises(AssetStageError, match=message) as caught:
        case.application().stage(case.request())

    assert caught.value.code == code


@pytest.mark.parametrize(
    ("attack", "code"),
    [
        ("missing", "HARN_ASSET_SOURCE_UNAVAILABLE"),
        ("changed", "HARN_DEPENDENCY_DRIFT"),
        ("symlink", "HARN_ASSET_SOURCE_UNSAFE"),
    ],
)
def test_stage_fails_closed_when_model_sidecar_bytes_are_unavailable_or_drift(
    tmp_path: Path,
    attack: str,
    code: str,
) -> None:
    case = _stage_case(tmp_path)
    sidecar = case.root / "objects/003_plate/model_data0.json"
    if attack == "missing":
        sidecar.unlink()
    elif attack == "changed":
        payload = sidecar.read_bytes()
        sidecar.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    else:
        outside = tmp_path / "outside-model-data.json"
        outside.write_bytes(sidecar.read_bytes())
        sidecar.unlink()
        sidecar.symlink_to(outside)

    with pytest.raises(AssetStageError) as caught:
        case.application().stage(case.request())

    assert caught.value.code == code


def test_stage_copies_exact_bytes_and_enumerates_each_loader_closure(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)

    result = case.application().stage(case.request())

    assert result.schema_version == "harness.asset_stage_result.v1"
    assert result.inventory_ref == case.inventory_ref
    assert result.repair_plan_ref == case.plan_ref
    assert result.source_snapshot_manifest_ref == case.source_manifest_ref
    assert result.selected_asset_ids == ("robotwin_003_plate", "robotwin_071_can")
    assert result.staged_asset_count == 2
    assert result.staged_member_count == 5
    assert result.staged_total_bytes == sum(map(len, case.files.values()))
    assert result.exact_source_bytes_staged is True
    assert result.loader_closure_enumerated is True
    assert result.simulator_executed is False
    assert result.runtime_qualification_executed is False
    assert result.promotion_executed is False
    assert len(result.stage_binding_sha256) == 64
    plate, can = result.assets
    assert plate.loader_closures[0].member_logical_paths == (
        "objects/003_plate/model_data0.json",
        "objects/003_plate/visual/base0.glb",
        "objects/003_plate/visual/mesh.bin",
    )
    assert can.loader_closures[0].member_logical_paths == (
        "objects/071_can/model_data0.json",
        "objects/071_can/visual/base0.glb",
    )
    for asset in result.assets:
        for member in asset.members:
            resolved = case.store.resolve(member.artifact_ref)
            assert resolved.path.read_bytes() == case.files[member.logical_path]


def test_stage_requires_an_explicit_source_snapshot_binding(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    application = AssetRepairApplication(
        artifact_store=case.store,
        trusted_inventory_refs=(case.inventory_ref,),
    )

    with pytest.raises(AssetStageError, match="no explicit local-root binding") as caught:
        application.stage(case.request())

    assert caught.value.code == "HARN_UNTRUSTED_ASSET_SOURCE_SNAPSHOT"


@pytest.mark.parametrize(
    "source_snapshots",
    [
        [],
        (object(),),
        (LocalAssetSourceSnapshotBinding(manifest_ref=object(), root=Path("/tmp")),),
        (
            LocalAssetSourceSnapshotBinding(
                manifest_ref=ArtifactRef(
                    name="manifest",
                    uri="artifact://sha256/" + "a" * 64,
                    media_type="application/json",
                    sha256="a" * 64,
                    bytes=1,
                    schema_version="harness.asset_source_snapshot_manifest.v1",
                ),
                root="/tmp",  # type: ignore[arg-type]
            ),
        ),
    ],
)
def test_application_rejects_nonexact_source_snapshot_bindings(
    tmp_path: Path,
    source_snapshots: object,
) -> None:
    case = _stage_case(tmp_path)

    with pytest.raises(TypeError):
        AssetRepairApplication(
            artifact_store=case.store,
            trusted_inventory_refs=(case.inventory_ref,),
            source_snapshots=source_snapshots,  # type: ignore[arg-type]
        )


def test_application_rejects_duplicate_source_snapshot_bindings(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    binding = LocalAssetSourceSnapshotBinding(
        manifest_ref=case.source_manifest_ref,
        root=case.root,
    )

    with pytest.raises(TypeError, match="must be unique"):
        AssetRepairApplication(
            artifact_store=case.store,
            trusted_inventory_refs=(case.inventory_ref,),
            source_snapshots=(binding, binding),
        )


@pytest.mark.parametrize(
    ("field", "changes", "message"),
    [
        ("inventory_ref", {"uri": "file:///inventory.json"}, "content-addressed"),
        (
            "inventory_ref",
            {"uri": "artifact://sha256/" + "f" * 64},
            "content-addressed",
        ),
        ("repair_plan_ref", {"media_type": "text/plain"}, "media_type"),
        (
            "source_snapshot_manifest_ref",
            {"schema_version": "harness.other.v1"},
            "schema_version",
        ),
    ],
)
def test_stage_request_rejects_unbound_cas_references(
    tmp_path: Path,
    field: str,
    changes: dict[str, str],
    message: str,
) -> None:
    case = _stage_case(tmp_path)
    payload = case.request().model_dump(mode="json")
    payload[field].update(changes)

    with pytest.raises(ValidationError, match=message):
        AssetStageRequest.model_validate(payload)


def test_stage_rejects_an_unavailable_repair_plan(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    case.store.resolve(case.plan_ref).path.unlink()

    with pytest.raises(AssetStageError, match="repair plan is unavailable") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ARTIFACT_UNAVAILABLE"


@pytest.mark.parametrize("encoding", ["pretty", "duplicate_key"])
def test_stage_requires_strict_canonical_repair_plan_bytes(
    tmp_path: Path,
    encoding: str,
) -> None:
    case = _stage_case(tmp_path)
    payload = case.plan.model_dump(mode="json")
    if encoding == "pretty":
        encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    else:
        canonical = _canonical_bytes(payload)
        encoded = canonical[:-2] + b',"schema_version":"harness.asset_repair_plan.v1"}\n'
    case = _with_plan(case, tmp_path, payload, encoded=encoded)

    with pytest.raises(AssetStageError, match="strict canonical JSON") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_INPUT_SCHEMA_INVALID"


def test_stage_rebuilds_the_plan_from_the_trusted_inventory(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    forged = case.plan.model_dump(mode="json")
    forged["source_population_ledger_count"] += 1
    forged["source_population_violation_count"] += 1
    forged["source_population_primary_probe_count"] += 1
    case = _with_plan(case, tmp_path, forged)

    with pytest.raises(AssetStageError, match="rebuilt from trusted inventory") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_REPAIR_PLAN_MISMATCH"


def test_stage_rejects_a_plan_bound_to_another_inventory(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    forged = case.plan.model_dump(mode="json")
    forged["inventory_ref"]["sha256"] = "f" * 64
    forged["inventory_ref"]["uri"] = "artifact://sha256/" + "f" * 64
    case = _with_plan(case, tmp_path, forged)

    with pytest.raises(AssetStageError, match="bound to another inventory") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_REPAIR_PLAN_MISMATCH"


def test_stage_rejects_an_unavailable_source_manifest(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    case.store.resolve(case.source_manifest_ref).path.unlink()

    with pytest.raises(AssetStageError, match="source snapshot manifest is unavailable") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ARTIFACT_UNAVAILABLE"


@pytest.mark.parametrize("encoding", ["pretty", "duplicate_key"])
def test_stage_requires_strict_canonical_source_manifest_bytes(
    tmp_path: Path,
    encoding: str,
) -> None:
    case = _stage_case(tmp_path)
    if encoding == "pretty":
        encoded = (json.dumps(case.source_manifest, indent=2, sort_keys=True) + "\n").encode()
    else:
        canonical = _canonical_bytes(case.source_manifest)
        encoded = canonical[:-2] + b',"source_revision":null}\n'
    case = _with_manifest(case, tmp_path, case.source_manifest, encoded=encoded)

    with pytest.raises(AssetStageError, match="strict canonical JSON") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_INPUT_SCHEMA_INVALID"


@pytest.mark.parametrize(
    ("attack", "message"),
    [
        ("duplicate_asset", "assets must be sorted and unique"),
        ("unsorted_asset", "assets must be sorted and unique"),
        ("duplicate_member", "members must be sorted and unique"),
        ("outside_root", "stay beneath logical_root"),
        ("global_duplicate", "globally unique"),
    ],
)
def test_stage_rejects_ambiguous_or_escaping_source_manifests(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    case = _stage_case(tmp_path)
    manifest = deepcopy(case.source_manifest)
    assets = manifest["assets"]
    if attack == "duplicate_asset":
        assets[1] = deepcopy(assets[0])
    elif attack == "unsorted_asset":
        assets.reverse()
    elif attack == "duplicate_member":
        assets[0]["members"].append(deepcopy(assets[0]["members"][0]))
    elif attack == "outside_root":
        assets[0]["members"][0]["logical_path"] = "objects/002_other/base0.glb"
    else:
        assets[1]["logical_root"] = "objects/003_plate"
        assets[1]["members"] = [deepcopy(assets[0]["members"][0])]
        assets[1]["model_sidecars"] = deepcopy(assets[0]["model_sidecars"])
    case = _with_manifest(case, tmp_path, manifest)

    with pytest.raises(AssetStageError, match=message) as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_INPUT_SCHEMA_INVALID"


def test_stage_rejects_a_source_manifest_bound_to_another_inventory(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    manifest = deepcopy(case.source_manifest)
    manifest["inventory_ref"]["sha256"] = "f" * 64
    manifest["inventory_ref"]["uri"] = "artifact://sha256/" + "f" * 64
    case = _with_manifest(case, tmp_path, manifest)

    with pytest.raises(AssetStageError, match="bound to another inventory") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_SOURCE_MANIFEST_MISMATCH"


def test_stage_requires_the_source_manifest_to_equal_the_plan_selection(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    manifest = deepcopy(case.source_manifest)
    manifest["assets"] = manifest["assets"][:1]
    case = _with_manifest(case, tmp_path, manifest)

    with pytest.raises(AssetStageError, match="must equal the repair plan selection") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_SOURCE_MANIFEST_MISMATCH"


@pytest.mark.parametrize("attack", ["missing_primary", "changed_primary_identity"])
def test_stage_binds_each_primary_loader_to_its_observed_inventory_identity(
    tmp_path: Path,
    attack: str,
) -> None:
    case = _stage_case(tmp_path)
    manifest = deepcopy(case.source_manifest)
    if attack == "missing_primary":
        manifest["assets"][0]["members"] = [
            member
            for member in manifest["assets"][0]["members"]
            if member["logical_path"] != "objects/003_plate/visual/base0.glb"
        ]
        message = "omits primary loader file"
    else:
        primary = next(
            member
            for member in manifest["assets"][0]["members"]
            if member["logical_path"] == "objects/003_plate/visual/base0.glb"
        )
        primary["sha256"] = "f" * 64
        message = "changes primary identity"
    case = _with_manifest(case, tmp_path, manifest)

    with pytest.raises(AssetStageError, match=message) as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_SOURCE_MANIFEST_MISMATCH"


def test_stage_rejects_a_nonexact_recovery_disposition(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    inventory = _inventory_payload(case.files)
    inventory["entries"][0]["recovery_probes"][0]["observed_bytes"] += 1
    inventory_ref = _put_json(
        case.store,
        tmp_path,
        name="ineligible_inventory",
        schema_version="harness.asset_debt_inventory.v1",
        value=inventory,
    )
    plan = AssetRepairApplication(
        artifact_store=case.store,
        trusted_inventory_refs=(inventory_ref,),
    ).plan(
        AssetRepairPlanRequest(
            schema_version="harness.asset_repair_plan_request.v1",
            inventory_ref=inventory_ref,
            selected_asset_ids=("robotwin_003_plate", "robotwin_071_can"),
        )
    )
    plan_ref = _put_json(
        case.store,
        tmp_path,
        name="ineligible_plan",
        schema_version="harness.asset_repair_plan.v1",
        value=plan.model_dump(mode="json"),
    )
    manifest = deepcopy(case.source_manifest)
    manifest["inventory_ref"] = inventory_ref.model_dump(mode="json")
    case = replace(case, inventory_ref=inventory_ref, plan=plan, plan_ref=plan_ref)
    case = _with_manifest(case, tmp_path, manifest)

    with pytest.raises(AssetStageError, match="not an exact observed identity") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_STAGE_INELIGIBLE"


@pytest.mark.parametrize(
    ("attack", "code", "message"),
    [
        ("missing", "HARN_ASSET_SOURCE_UNAVAILABLE", "unavailable"),
        ("digest", "HARN_DEPENDENCY_DRIFT", "digest changed"),
        ("size", "HARN_DEPENDENCY_DRIFT", "byte count changed"),
        ("file_symlink", "HARN_ASSET_SOURCE_UNSAFE", "cannot be read"),
        ("directory_symlink", "HARN_ASSET_SOURCE_UNSAFE", "cannot be read"),
    ],
)
def test_stage_fails_closed_when_source_members_are_missing_changed_or_symlinked(
    tmp_path: Path,
    attack: str,
    code: str,
    message: str,
) -> None:
    case = _stage_case(tmp_path)
    member = case.root / "objects/003_plate/visual/mesh.bin"
    if attack == "missing":
        member.unlink()
    elif attack == "digest":
        member.write_bytes(b"MESH")
    elif attack == "size":
        member.write_bytes(b"too-long")
    elif attack == "file_symlink":
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"mesh")
        member.unlink()
        member.symlink_to(outside)
    else:
        visual = member.parent
        real_visual = visual.with_name("real-visual")
        visual.rename(real_visual)
        visual.symlink_to(real_visual, target_is_directory=True)

    with pytest.raises(AssetStageError, match=message) as caught:
        case.application().stage(case.request())

    assert caught.value.code == code


def test_stage_rejects_a_nonregular_source_member(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    member = case.root / "objects/003_plate/visual/mesh.bin"
    member.unlink()
    member.mkdir()

    with pytest.raises(AssetStageError, match="not a regular file") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_SOURCE_UNSAFE"


def test_stage_detects_a_source_change_during_its_single_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _stage_case(tmp_path)
    original_fstat = os.fstat
    changed = False

    def change_after_first_snapshot(file_descriptor: int):
        nonlocal changed
        result = original_fstat(file_descriptor)
        if not changed:
            changed = True
            source = Path(os.readlink(f"/proc/self/fd/{file_descriptor}"))
            os.utime(source, ns=(result.st_atime_ns, result.st_mtime_ns + 1_000_000_000))
        return result

    monkeypatch.setattr(os, "fstat", change_after_first_snapshot)

    with pytest.raises(AssetStageError, match="changed during capture") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_DEPENDENCY_DRIFT"


def test_stage_reports_source_read_failure_without_blaming_the_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _stage_case(tmp_path)

    def unreadable(_file_descriptor: int, _byte_count: int) -> bytes:
        raise OSError(5, "source read failed")

    monkeypatch.setattr(os, "read", unreadable)

    with pytest.raises(AssetStageError, match="source snapshot member cannot be read") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("attack", ["missing", "symlink"])
def test_stage_fails_closed_when_the_bound_source_root_is_unavailable_or_a_symlink(
    tmp_path: Path,
    attack: str,
) -> None:
    case = _stage_case(tmp_path)
    if attack == "missing":
        root = tmp_path / "does-not-exist"
        expected = "HARN_ASSET_SOURCE_UNAVAILABLE"
    else:
        actual = tmp_path / "actual-source"
        case.root.rename(actual)
        case.root.symlink_to(actual, target_is_directory=True)
        root = case.root
        expected = "HARN_ASSET_SOURCE_UNSAFE"

    with pytest.raises(AssetStageError) as caught:
        case.application(root=root).stage(case.request())

    assert caught.value.code == expected


def test_stage_rejects_a_missing_loader_dependency_even_if_the_source_file_exists(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    manifest = deepcopy(case.source_manifest)
    manifest["assets"][0]["members"] = [
        member
        for member in manifest["assets"][0]["members"]
        if member["logical_path"] != "objects/003_plate/visual/mesh.bin"
    ]
    case = _with_manifest(case, tmp_path, manifest)

    with pytest.raises(AssetStageError, match="dependency is absent") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_LOADER_CLOSURE_INVALID"


def test_stage_rejects_unreferenced_source_manifest_members(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    extra_path = "objects/003_plate/visual/unreferenced.bin"
    extra_bytes = b"extra"
    _write(case.root / extra_path, extra_bytes)
    manifest = deepcopy(case.source_manifest)
    manifest["assets"][0]["members"].append(
        {
            "bytes": len(extra_bytes),
            "logical_path": extra_path,
            "sha256": _sha256(extra_bytes),
        }
    )
    case = _with_manifest(case, tmp_path, manifest)

    with pytest.raises(AssetStageError, match="unreferenced members") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_LOADER_CLOSURE_INVALID"


@pytest.mark.parametrize("reference", ["../../../outside.bin", "https://example.com/a.bin"])
def test_stage_rejects_unsafe_loader_references(tmp_path: Path, reference: str) -> None:
    case = _stage_case(tmp_path, plate_reference=reference)

    with pytest.raises(AssetStageError, match="loader reference") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_LOADER_CLOSURE_INVALID"


def test_stage_rejects_an_invalid_glb_loader_document(tmp_path: Path) -> None:
    case = _stage_case(tmp_path, include_mesh=False, plate_payload=b"not-a-glb")

    with pytest.raises(AssetStageError, match="loader document is invalid") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_LOADER_CLOSURE_INVALID"


def test_stage_rejects_a_non_glb_primary_loader(tmp_path: Path) -> None:
    case = _stage_case(
        tmp_path,
        include_mesh=False,
        plate_payload=b"not-a-loader",
        plate_logical_path="objects/003_plate/visual/base0.bin",
    )

    with pytest.raises(AssetStageError, match="requires a GLB loader root") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_LOADER_CLOSURE_INVALID"


def test_stage_deduplicates_repeated_loader_references(tmp_path: Path) -> None:
    case = _stage_case(tmp_path, repeat_external_reference=True)

    result = case.application().stage(case.request())

    assert result.assets[0].loader_closures[0].member_logical_paths == (
        "objects/003_plate/model_data0.json",
        "objects/003_plate/visual/base0.glb",
        "objects/003_plate/visual/mesh.bin",
    )


def test_stage_enumeration_terminates_on_a_loader_reference_cycle(tmp_path: Path) -> None:
    case = _stage_case(tmp_path, plate_reference="base0.glb", include_mesh=False)

    result = case.application().stage(case.request())

    assert result.assets[0].loader_closures[0].member_logical_paths == (
        "objects/003_plate/model_data0.json",
        "objects/003_plate/visual/base0.glb",
    )


def test_stage_treats_an_embedded_data_uri_as_part_of_the_glb(tmp_path: Path) -> None:
    case = _stage_case(
        tmp_path,
        plate_reference="data:application/octet-stream;base64,bWVzaA==",
        include_mesh=False,
    )

    result = case.application().stage(case.request())

    assert result.assets[0].loader_closures[0].member_logical_paths == (
        "objects/003_plate/model_data0.json",
        "objects/003_plate/visual/base0.glb",
    )


def test_stage_failure_does_not_publish_a_partial_success_result(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    can_sha256 = _sha256(case.files["objects/071_can/visual/base0.glb"])
    corrupt = case.store.root / "sha256" / can_sha256[:2] / can_sha256
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"corrupt")

    with pytest.raises(AssetStageError, match="failed to admit exact source bytes") as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_STAGE_WRITE_FAILED"
    corrupt.unlink()
    result = case.application().stage(case.request())
    assert result.staged_member_count == 5


@pytest.mark.parametrize("write_errno", [13, 28])
def test_stage_reports_destination_write_failure_without_blaming_the_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_errno: int,
) -> None:
    case = _stage_case(tmp_path)

    def no_space(_file_descriptor: int) -> None:
        raise OSError(write_errno, "destination write refused")

    monkeypatch.setattr(os, "fsync", no_space)

    with pytest.raises(AssetStageError) as caught:
        case.application().stage(case.request())

    assert caught.value.code == "HARN_ASSET_STAGE_WRITE_FAILED"
    assert "destination" in str(caught.value)


def _result_payload(tmp_path: Path) -> dict[str, object]:
    case = _stage_case(tmp_path)
    return case.application().stage(case.request()).model_dump(mode="json")


def _rehash_asset(asset: dict[str, object]) -> None:
    asset["asset_closure_sha256"] = canonical_sha256(
        {key: value for key, value in asset.items() if key != "asset_closure_sha256"}
    )


def _rehash_result(result: dict[str, object]) -> None:
    result["stage_binding_sha256"] = canonical_sha256(
        {key: value for key, value in result.items() if key != "stage_binding_sha256"}
    )


def _rehash_loader_closure(asset: dict[str, object], closure: dict[str, object]) -> None:
    members = {member["logical_path"]: member for member in asset["members"]}
    closure["closure_sha256"] = loader_closure_sha256(
        tuple(
            StagedAssetMember.model_validate(members[path])
            for path in closure["member_logical_paths"]
        )
    )


def _verify_result(case: _StageCase, result: dict[str, object]):
    payload = _canonical_bytes(result)
    return verify_asset_stage_result_authority(
        artifact_store=case.store,
        result_payload=payload,
        expected_result_sha256=_sha256(payload),
        expected_stage_binding_sha256=result["stage_binding_sha256"],
        expected_inventory_sha256=case.inventory_ref.sha256,
        expected_repair_plan_sha256=case.plan_ref.sha256,
        expected_source_snapshot_manifest_sha256=case.source_manifest_ref.sha256,
    )


def _verifier_arguments(
    case: _StageCase,
    result: dict[str, object],
) -> dict[str, object]:
    payload = _canonical_bytes(result)
    return {
        "artifact_store": case.store,
        "result_payload": payload,
        "expected_result_sha256": _sha256(payload),
        "expected_stage_binding_sha256": result["stage_binding_sha256"],
        "expected_inventory_sha256": case.inventory_ref.sha256,
        "expected_repair_plan_sha256": case.plan_ref.sha256,
        "expected_source_snapshot_manifest_sha256": case.source_manifest_ref.sha256,
    }


def _rebind_plate_loader_authority(
    case: _StageCase,
    tmp_path: Path,
    *,
    loader_payload: bytes,
    keep_declared_mesh: bool,
) -> tuple[_StageCase, dict[str, object]]:
    result = case.application().stage(case.request()).model_dump(mode="json")
    root_logical_path = "objects/003_plate/visual/base0.glb"
    mesh_logical_path = "objects/003_plate/visual/mesh.bin"
    source = tmp_path / f"forged-loader-{_sha256(loader_payload)[:12]}.glb"
    source.write_bytes(loader_payload)
    root_ref = case.store.put_file(
        source,
        name="forged-plate-loader.glb",
        media_type="model/gltf-binary",
        schema_version=None,
    )

    rebound_files = dict(case.files)
    rebound_files[root_logical_path] = loader_payload
    if not keep_declared_mesh:
        rebound_files.pop(mesh_logical_path)
    inventory = _inventory_payload(rebound_files)
    inventory_ref = _put_json(
        case.store,
        tmp_path,
        name=f"forged-inventory-{_sha256(loader_payload)[:12]}",
        schema_version="harness.asset_debt_inventory.v1",
        value=inventory,
    )
    plan = AssetRepairApplication(
        artifact_store=case.store,
        trusted_inventory_refs=(inventory_ref,),
    ).plan(
        AssetRepairPlanRequest(
            schema_version="harness.asset_repair_plan_request.v1",
            inventory_ref=inventory_ref,
            selected_asset_ids=("robotwin_003_plate", "robotwin_071_can"),
        )
    )
    plan_ref = _put_json(
        case.store,
        tmp_path,
        name=f"forged-plan-{_sha256(loader_payload)[:12]}",
        schema_version="harness.asset_repair_plan.v1",
        value=plan.model_dump(mode="json"),
    )
    manifest = deepcopy(case.source_manifest)
    manifest["inventory_ref"] = inventory_ref.model_dump(mode="json")
    manifest_asset = manifest["assets"][0]
    manifest_root = next(
        member
        for member in manifest_asset["members"]
        if member["logical_path"] == root_logical_path
    )
    manifest_root.update(sha256=root_ref.sha256, bytes=root_ref.bytes)
    if not keep_declared_mesh:
        manifest_asset["members"] = [
            member
            for member in manifest_asset["members"]
            if member["logical_path"] != mesh_logical_path
        ]
    manifest_ref = _put_json(
        case.store,
        tmp_path,
        name=f"forged-manifest-{_sha256(loader_payload)[:12]}",
        schema_version="harness.asset_source_snapshot_manifest.v1",
        value=manifest,
    )

    result["inventory_ref"] = inventory_ref.model_dump(mode="json")
    result["repair_plan_ref"] = plan_ref.model_dump(mode="json")
    result["source_snapshot_manifest_ref"] = manifest_ref.model_dump(mode="json")
    result_asset = result["assets"][0]
    result_root = next(
        member
        for member in result_asset["members"]
        if member["logical_path"] == root_logical_path
    )
    result["staged_total_bytes"] += root_ref.bytes - result_root["source_bytes"]
    result_root.update(
        source_sha256=root_ref.sha256,
        source_bytes=root_ref.bytes,
        artifact_ref=root_ref.model_dump(mode="json"),
    )
    closure = result_asset["loader_closures"][0]
    if not keep_declared_mesh:
        mesh = next(
            member
            for member in result_asset["members"]
            if member["logical_path"] == mesh_logical_path
        )
        result_asset["members"].remove(mesh)
        closure["member_logical_paths"].remove(mesh_logical_path)
        result["staged_member_count"] -= 1
        result["staged_total_bytes"] -= mesh["source_bytes"]
    _rehash_loader_closure(result_asset, closure)
    _rehash_asset(result_asset)
    _rehash_result(result)
    return (
        replace(
            case,
            files=rebound_files,
            inventory_ref=inventory_ref,
            plan=plan,
            plan_ref=plan_ref,
            source_manifest=manifest,
            source_manifest_ref=manifest_ref,
        ),
        result,
    )


def test_loader_closure_digest_commits_member_content_identity(tmp_path: Path) -> None:
    result = _result_payload(tmp_path)
    plate = result["assets"][0]
    can = result["assets"][1]
    plate_root = next(
        member
        for member in plate["members"]
        if member["logical_path"] == plate["loader_closures"][0]["root_logical_path"]
    )
    can_root = next(member for member in can["members"] if member["logical_path"].endswith(".glb"))
    plate_root.update(
        source_sha256=can_root["source_sha256"],
        source_bytes=can_root["source_bytes"],
        artifact_ref=deepcopy(can_root["artifact_ref"]),
    )
    _rehash_asset(plate)
    _rehash_result(result)

    with pytest.raises(ValidationError, match="closure digest is inconsistent"):
        AssetStageResult.model_validate(result)


@pytest.mark.parametrize("members", [[], (object(),)])
def test_loader_closure_digest_requires_exact_typed_member_tuple(members: object) -> None:
    with pytest.raises(TypeError, match="exact StagedAssetMember tuple"):
        loader_closure_sha256(members)


@pytest.mark.parametrize(
    ("argument", "replacement"),
    [
        ("artifact_store", object()),
        ("result_payload", bytearray(b"not exact bytes")),
    ],
)
def test_stage_authority_verifier_requires_exact_dependency_types(
    tmp_path: Path,
    argument: str,
    replacement: object,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    arguments = _verifier_arguments(case, result)
    arguments[argument] = replacement

    with pytest.raises(TypeError, match="must be"):
        verify_asset_stage_result_authority(**arguments)


@pytest.mark.parametrize("invalid_digest", [None, "a" * 63, "A" * 64])
def test_stage_authority_verifier_requires_lowercase_sha256_trust_inputs(
    tmp_path: Path,
    invalid_digest: object,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    arguments = _verifier_arguments(case, result)
    arguments["expected_stage_binding_sha256"] = invalid_digest

    with pytest.raises(TypeError, match="lowercase SHA-256"):
        verify_asset_stage_result_authority(**arguments)


@pytest.mark.parametrize(
    "trust_input",
    [
        "expected_result_sha256",
        "expected_stage_binding_sha256",
        "expected_inventory_sha256",
        "expected_repair_plan_sha256",
        "expected_source_snapshot_manifest_sha256",
    ],
)
def test_stage_authority_verifier_rejects_each_explicit_trust_mismatch(
    tmp_path: Path,
    trust_input: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    arguments = _verifier_arguments(case, result)
    arguments[trust_input] = "0" * 64

    with pytest.raises(AssetStageVerificationError) as caught:
        verify_asset_stage_result_authority(**arguments)

    assert caught.value.code == "HARN_ASSET_STAGE_TRUST_MISMATCH"


@pytest.mark.parametrize("encoding", ["invalid", "noncanonical"])
def test_stage_authority_verifier_requires_strict_canonical_result_bytes(
    tmp_path: Path,
    encoding: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    arguments = _verifier_arguments(case, result)
    payload = (
        b'{"not":"an asset stage result"}\n'
        if encoding == "invalid"
        else arguments["result_payload"].replace(b'{"assets"', b'{ "assets"', 1)
    )
    arguments["result_payload"] = payload
    arguments["expected_result_sha256"] = _sha256(payload)

    with pytest.raises(AssetStageVerificationError) as caught:
        verify_asset_stage_result_authority(**arguments)

    assert caught.value.code == "HARN_ASSET_STAGE_RESULT_INVALID"


@pytest.mark.parametrize("attack", ["asset_id", "ledger", "closure", "counts"])
def test_stage_authority_verifier_rejects_rehashed_cross_contract_forgery(
    tmp_path: Path,
    attack: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    asset = result["assets"][0]
    if attack == "asset_id":
        asset["asset_id"] = "robotwin_004_plate"
        result["selected_asset_ids"][0] = "robotwin_004_plate"
    elif attack == "ledger":
        asset["source_ledger_sha256"] = "f" * 64
    elif attack == "closure":
        asset["loader_closures"][0]["loader_scale"] = [0.5, 0.5, 0.5]
    else:
        removed = next(
            member
            for member in asset["members"]
            if member["logical_path"].endswith("mesh.bin")
        )
        asset["members"].remove(removed)
        closure = asset["loader_closures"][0]
        closure["member_logical_paths"].remove(removed["logical_path"])
        _rehash_loader_closure(asset, closure)
        result["staged_member_count"] -= 1
        result["staged_total_bytes"] -= removed["source_bytes"]
    _rehash_asset(asset)
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="authority"):
        _verify_result(case, result)


def test_stage_authority_verifier_rejects_a_plan_not_derived_from_inventory(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    plan = case.plan.model_dump(mode="json")
    plan["source_population_ledger_count"] += 1
    case = _with_plan(case, tmp_path, plan)
    result["repair_plan_ref"] = case.plan_ref.model_dump(mode="json")
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="repair plan"):
        _verify_result(case, result)


def test_stage_authority_verifier_rejects_plan_bound_to_another_inventory(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    other_inventory = _inventory_payload(case.files)
    other_inventory["inventory_id"] = "other_inventory"
    other_inventory_ref = _put_json(
        case.store,
        tmp_path,
        name="other_inventory",
        schema_version="harness.asset_debt_inventory.v1",
        value=other_inventory,
    )
    plan = case.plan.model_dump(mode="json")
    plan["inventory_ref"] = other_inventory_ref.model_dump(mode="json")
    case = _with_plan(case, tmp_path, plan)
    result["repair_plan_ref"] = case.plan_ref.model_dump(mode="json")
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="another inventory"):
        _verify_result(case, result)


def test_stage_authority_verifier_rejects_manifest_bound_to_another_inventory(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    other_inventory = _inventory_payload(case.files)
    other_inventory["inventory_id"] = "other_inventory"
    other_inventory_ref = _put_json(
        case.store,
        tmp_path,
        name="other_inventory",
        schema_version="harness.asset_debt_inventory.v1",
        value=other_inventory,
    )
    manifest = deepcopy(case.source_manifest)
    manifest["inventory_ref"] = other_inventory_ref.model_dump(mode="json")
    case = _with_manifest(case, tmp_path, manifest)
    result["source_snapshot_manifest_ref"] = case.source_manifest_ref.model_dump(mode="json")
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="another inventory"):
        _verify_result(case, result)


def test_stage_authority_verifier_rejects_manifest_selection_omission(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    manifest = deepcopy(case.source_manifest)
    manifest["assets"] = manifest["assets"][:1]
    case = _with_manifest(case, tmp_path, manifest)
    result["source_snapshot_manifest_ref"] = case.source_manifest_ref.model_dump(mode="json")
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="selection differs"):
        _verify_result(case, result)


def test_stage_authority_verifier_rejects_loader_role_not_in_the_plan(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    asset = result["assets"][0]
    asset["loader_closures"][0]["role"] = "collision"
    _rehash_asset(asset)
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="loader closures differ"):
        _verify_result(case, result)


@pytest.mark.parametrize(
    "sidecar_payload",
    [
        b'{"scale":',
        _canonical_bytes({"scale": [0.5, 0.5, 0.5]}),
    ],
)
def test_stage_authority_verifier_reparses_exact_cas_sidecar_semantics(
    tmp_path: Path,
    sidecar_payload: bytes,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    manifest = deepcopy(case.source_manifest)
    logical_path = "objects/003_plate/model_data0.json"
    sidecar_path = tmp_path / "forged-model-data0.json"
    sidecar_path.write_bytes(sidecar_payload)
    sidecar_ref = case.store.put_file(
        sidecar_path,
        name="forged-model-data0.json",
        media_type="application/json",
        schema_version=None,
    )
    manifest_member = next(
        member
        for member in manifest["assets"][0]["members"]
        if member["logical_path"] == logical_path
    )
    manifest_member.update(sha256=sidecar_ref.sha256, bytes=sidecar_ref.bytes)
    case = _with_manifest(case, tmp_path, manifest)
    result["source_snapshot_manifest_ref"] = case.source_manifest_ref.model_dump(mode="json")
    asset = result["assets"][0]
    result_member = next(
        member for member in asset["members"] if member["logical_path"] == logical_path
    )
    original_bytes = result_member["source_bytes"]
    result_member.update(
        source_sha256=sidecar_ref.sha256,
        source_bytes=sidecar_ref.bytes,
        artifact_ref=sidecar_ref.model_dump(mode="json"),
    )
    result["staged_total_bytes"] += sidecar_ref.bytes - original_bytes
    _rehash_loader_closure(asset, asset["loader_closures"][0])
    _rehash_asset(asset)
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="sidecar"):
        _verify_result(case, result)


@pytest.mark.parametrize(
    ("loader_payload", "keep_declared_mesh", "expected_message"),
    [
        (_glb(external_buffer="missing.bin"), False, "absent"),
        (_glb(binary=b"mesh"), True, "differs"),
        (_glb(external_buffer="../../../../escape.bin"), False, "unsafe"),
        (_glb(external_buffer="../../escape.bin"), False, "unsafe"),
        (b"not a glb loader document", False, "invalid"),
    ],
)
def test_stage_authority_verifier_recomputes_exact_cas_glb_loader_closure(
    tmp_path: Path,
    loader_payload: bytes,
    keep_declared_mesh: bool,
    expected_message: str,
) -> None:
    case = _stage_case(tmp_path)
    case, result = _rebind_plate_loader_authority(
        case,
        tmp_path,
        loader_payload=loader_payload,
        keep_declared_mesh=keep_declared_mesh,
    )

    with pytest.raises(AssetStageVerificationError, match=expected_message):
        _verify_result(case, result)


@pytest.mark.parametrize(
    "loader_payload",
    [
        _glb(external_buffer="base0.glb"),
        _glb(external_buffer="data:application/octet-stream;base64,bWVzaA=="),
    ],
)
def test_stage_authority_verifier_safely_closes_cycles_and_embedded_data_uris(
    tmp_path: Path,
    loader_payload: bytes,
) -> None:
    case = _stage_case(tmp_path)
    case, result = _rebind_plate_loader_authority(
        case,
        tmp_path,
        loader_payload=loader_payload,
        keep_declared_mesh=False,
    )

    authority = _verify_result(case, result)

    assert authority.result.assets[0].loader_closures[0].member_logical_paths == (
        "objects/003_plate/model_data0.json",
        "objects/003_plate/visual/base0.glb",
    )


def test_stage_authority_verifier_deduplicates_repeated_exact_cas_references(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path, repeat_external_reference=True)
    result = case.application().stage(case.request()).model_dump(mode="json")

    authority = _verify_result(case, result)

    assert authority.result.assets[0].loader_closures[0].member_logical_paths == (
        "objects/003_plate/model_data0.json",
        "objects/003_plate/visual/base0.glb",
        "objects/003_plate/visual/mesh.bin",
    )


@pytest.mark.parametrize(
    ("attack", "expected_code"),
    [
        ("missing", "HARN_ASSET_STAGE_ARTIFACT_UNAVAILABLE"),
        ("changed", "HARN_ASSET_STAGE_AUTHORITY_MISMATCH"),
    ],
)
def test_stage_authority_verifier_rechecks_cas_during_loader_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
    expected_code: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request())
    root_member = next(
        member
        for member in result.assets[0].members
        if member.logical_path == "objects/003_plate/visual/base0.glb"
    )
    cas_path = case.store.resolve(root_member.artifact_ref).path
    saved_path = cas_path.with_name(cas_path.name + ".saved")
    real_open = stage_verification.os.open
    matching_opens = 0

    def change_before_second_open(path: object, *args: object, **kwargs: object) -> int:
        nonlocal matching_opens
        if path == root_member.source_sha256:
            matching_opens += 1
            if matching_opens == 2:
                cas_path.rename(saved_path)
                if attack == "changed":
                    cas_path.write_bytes(b"forged after the first exact CAS attestation")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(stage_verification.os, "open", change_before_second_open)

    with pytest.raises(AssetStageVerificationError) as caught:
        _verify_result(case, result.model_dump(mode="json"))

    assert matching_opens == 2
    assert caught.value.code == expected_code


def test_stage_authority_verifier_requires_a_sidecar_for_each_planned_model(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    manifest = deepcopy(case.source_manifest)
    logical_path = "objects/003_plate/model_data1.json"
    payload = _canonical_bytes({"model_id": 1, "scale": [0.025, 0.025, 0.025]})
    source = tmp_path / "model_data1.json"
    source.write_bytes(payload)
    ref = case.store.put_file(
        source,
        name="model_data1.json",
        media_type="application/json",
        schema_version=None,
    )
    manifest_asset = manifest["assets"][0]
    manifest_asset["members"].append(
        {"logical_path": logical_path, "sha256": ref.sha256, "bytes": ref.bytes}
    )
    manifest_asset["members"].sort(key=lambda value: value["logical_path"])
    manifest_asset["model_sidecars"] = [
        {
            "model_id": 1,
            "logical_path": logical_path,
            "scale": [0.025, 0.025, 0.025],
        }
    ]
    case = _with_manifest(case, tmp_path, manifest)
    result["source_snapshot_manifest_ref"] = case.source_manifest_ref.model_dump(mode="json")
    asset = result["assets"][0]
    asset["members"].append(
        {
            "logical_path": logical_path,
            "source_sha256": ref.sha256,
            "source_bytes": ref.bytes,
            "artifact_ref": ref.model_dump(mode="json"),
        }
    )
    asset["members"].sort(key=lambda value: value["logical_path"])
    closure = asset["loader_closures"][0]
    closure["member_logical_paths"].append(logical_path)
    closure["member_logical_paths"].sort()
    _rehash_loader_closure(asset, closure)
    _rehash_asset(asset)
    result["staged_member_count"] += 1
    result["staged_total_bytes"] += ref.bytes
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="absent"):
        _verify_result(case, result)


def test_stage_authority_verifier_cross_checks_loader_root_identity(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    manifest = deepcopy(case.source_manifest)
    plate_asset = result["assets"][0]
    can_asset = result["assets"][1]
    plate_root_path = plate_asset["loader_closures"][0]["root_logical_path"]
    plate_root = next(
        member for member in plate_asset["members"] if member["logical_path"] == plate_root_path
    )
    can_root = next(
        member for member in can_asset["members"] if member["logical_path"].endswith(".glb")
    )
    original_bytes = plate_root["source_bytes"]
    plate_root.update(
        source_sha256=can_root["source_sha256"],
        source_bytes=can_root["source_bytes"],
        artifact_ref=deepcopy(can_root["artifact_ref"]),
    )
    manifest_root = next(
        member
        for member in manifest["assets"][0]["members"]
        if member["logical_path"] == plate_root_path
    )
    manifest_root.update(sha256=can_root["source_sha256"], bytes=can_root["source_bytes"])
    case = _with_manifest(case, tmp_path, manifest)
    result["source_snapshot_manifest_ref"] = case.source_manifest_ref.model_dump(mode="json")
    result["staged_total_bytes"] += can_root["source_bytes"] - original_bytes
    _rehash_loader_closure(plate_asset, plate_asset["loader_closures"][0])
    _rehash_asset(plate_asset)
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="loader root identity"):
        _verify_result(case, result)


@pytest.mark.parametrize(
    ("attack", "expected_code"),
    [
        ("missing", "HARN_ASSET_STAGE_ARTIFACT_UNAVAILABLE"),
        ("changed", "HARN_ASSET_STAGE_AUTHORITY_MISMATCH"),
    ],
)
def test_stage_authority_verifier_rehashes_every_cas_member(
    tmp_path: Path,
    attack: str,
    expected_code: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request())
    member = next(
        value
        for value in result.assets[0].members
        if value.logical_path.endswith("mesh.bin")
    )
    cas_path = case.store.resolve(member.artifact_ref).path
    cas_path.unlink()
    if attack == "changed":
        cas_path.write_bytes(b"mash")

    with pytest.raises(AssetStageVerificationError) as caught:
        _verify_result(case, result.model_dump(mode="json"))

    assert caught.value.code == expected_code


def test_stage_authority_verifier_rejects_unavailable_canonical_input(
    tmp_path: Path,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    inventory_path = case.store.resolve(case.inventory_ref).path
    inventory_path.unlink()

    with pytest.raises(AssetStageVerificationError) as caught:
        _verify_result(case, result)

    assert caught.value.code == "HARN_ASSET_STAGE_ARTIFACT_UNAVAILABLE"


def test_stage_authority_verifier_rejects_input_ref_size_forgery(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    result["inventory_ref"]["bytes"] += 1
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError) as caught:
        _verify_result(case, result)

    assert caught.value.code == "HARN_ASSET_STAGE_ARTIFACT_UNAVAILABLE"


@pytest.mark.parametrize("unsafe_path", ["/tmp/escaped.glb", "objects/../escaped.glb"])
def test_stage_authority_verifier_rejects_unsafe_result_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request()).model_dump(mode="json")
    result["assets"][0]["members"][0]["logical_path"] = unsafe_path
    _rehash_result(result)

    with pytest.raises(AssetStageVerificationError, match="invalid"):
        _verify_result(case, result)


@pytest.mark.parametrize("attack", ["symlink", "rename"])
def test_materializer_rechecks_cas_after_authority_precheck(
    tmp_path: Path,
    attack: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request())
    authority = _verify_result(case, result.model_dump(mode="json"))
    member = result.assets[0].members[0]
    cas_path = case.store.resolve(member.artifact_ref).path
    saved = cas_path.with_name(cas_path.name + ".saved")
    cas_path.rename(saved)
    if attack == "symlink":
        cas_path.symlink_to(saved)
    else:
        cas_path.write_bytes(b"forged after authority verification")

    with pytest.raises(AssetStageVerificationError, match="materialization"):
        materialize_verified_asset_stage(
            artifact_store=case.store,
            authority=authority,
            destination_root=tmp_path / "runtime-assets",
        )


def test_rooted_attestor_streams_one_stable_regular_file(tmp_path: Path) -> None:
    payload = b"a" * (1024 * 1024 + 7)
    _write(tmp_path / "nested/member.bin", payload)

    observed, read_attestation = stage_verification.read_rooted_regular_file(
        root=tmp_path,
        logical_path="nested/member.bin",
    )
    hash_attestation = stage_verification.attest_rooted_regular_file(
        root=tmp_path,
        logical_path="nested/member.bin",
    )

    assert observed == payload
    assert read_attestation.sha256 == _sha256(payload)
    assert hash_attestation.sha256 == read_attestation.sha256
    assert hash_attestation.bytes == len(payload)


@pytest.mark.parametrize(
    "unsafe_path",
    [None, "", ".", "/absolute", "C:/windows", "a\\b", "a/../b", "a//b", "a\n/b"],
)
def test_rooted_attestor_rejects_nonportable_logical_paths(
    tmp_path: Path,
    unsafe_path: object,
) -> None:
    with pytest.raises(AssetStageVerificationError, match="logical path|unsafe"):
        stage_verification.attest_rooted_regular_file(
            root=tmp_path,
            logical_path=unsafe_path,
        )


def test_rooted_attestor_requires_a_path_root(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="root must be a Path"):
        stage_verification.attest_rooted_regular_file(
            root=os.fspath(tmp_path),
            logical_path="member.bin",
        )


@pytest.mark.parametrize("attack", ["missing", "file_symlink", "directory", "fifo"])
def test_rooted_attestor_rejects_unavailable_or_unsafe_targets(
    tmp_path: Path,
    attack: str,
) -> None:
    logical_path = "tree/member.bin"
    if attack == "file_symlink":
        _write(tmp_path / "outside.bin", b"outside")
        (tmp_path / "tree").mkdir()
        (tmp_path / logical_path).symlink_to(tmp_path / "outside.bin")
    elif attack == "directory":
        (tmp_path / logical_path).mkdir(parents=True)
    elif attack == "fifo":
        (tmp_path / "tree").mkdir()
        os.mkfifo(tmp_path / logical_path)

    with pytest.raises(AssetStageVerificationError) as caught:
        stage_verification.attest_rooted_regular_file(
            root=tmp_path,
            logical_path=logical_path,
        )

    assert caught.value.code.startswith("HARN_ASSET_STAGE_ATTESTATION_")


@pytest.mark.parametrize("attack", ["signature", "short_read"])
def test_rooted_attestor_rejects_in_read_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    _write(tmp_path / "member.bin", b"stable bytes")
    if attack == "signature":
        real_fstat = os.fstat
        calls = 0

        def drifting_fstat(file_descriptor: int):
            nonlocal calls
            calls += 1
            status = real_fstat(file_descriptor)
            if calls == 2:
                return SimpleNamespace(
                    st_mode=status.st_mode,
                    st_dev=status.st_dev,
                    st_ino=status.st_ino,
                    st_size=status.st_size,
                    st_mtime_ns=status.st_mtime_ns + 1,
                    st_ctime_ns=status.st_ctime_ns,
                )
            return status

        monkeypatch.setattr(stage_verification.os, "fstat", drifting_fstat)
    else:
        monkeypatch.setattr(stage_verification.os, "read", lambda _fd, _size: b"")

    with pytest.raises(AssetStageVerificationError) as caught:
        stage_verification.read_rooted_regular_file(
            root=tmp_path,
            logical_path="member.bin",
        )

    assert caught.value.code == "HARN_ASSET_STAGE_ATTESTATION_DRIFT"


def test_materializer_copies_every_member_into_a_new_contained_root(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request())
    authority = _verify_result(case, result.model_dump(mode="json"))
    destination = tmp_path / "runtime-assets"

    materialized = materialize_verified_asset_stage(
        artifact_store=case.store,
        authority=authority,
        destination_root=destination,
    )

    assert tuple(value.logical_path for value in materialized) == tuple(
        member.logical_path for asset in result.assets for member in asset.members
    )
    for member in materialized:
        assert (destination / member.logical_path).read_bytes() == case.files[member.logical_path]


@pytest.mark.parametrize(
    ("argument", "replacement"),
    [
        ("artifact_store", object()),
        ("authority", object()),
        ("destination_root", "not-a-path"),
    ],
)
def test_materializer_requires_exact_authority_dependencies(
    tmp_path: Path,
    argument: str,
    replacement: object,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request())
    authority = _verify_result(case, result.model_dump(mode="json"))
    arguments = {
        "artifact_store": case.store,
        "authority": authority,
        "destination_root": tmp_path / "runtime-assets",
    }
    arguments[argument] = replacement

    with pytest.raises(TypeError, match="must be"):
        materialize_verified_asset_stage(**arguments)


def test_materializer_rejects_an_existing_destination_root(tmp_path: Path) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request())
    authority = _verify_result(case, result.model_dump(mode="json"))
    destination = tmp_path / "runtime-assets"
    destination.mkdir()

    with pytest.raises(AssetStageVerificationError) as caught:
        materialize_verified_asset_stage(
            artifact_store=case.store,
            authority=authority,
            destination_root=destination,
        )

    assert caught.value.code == "HARN_ASSET_STAGE_MATERIALIZATION_FAILED"


@pytest.mark.parametrize("unsafe_path", ["/absolute/member.glb", "objects/../member.glb"])
def test_materializer_independently_rejects_unsafe_member_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request())
    authority = _verify_result(case, result.model_dump(mode="json"))
    asset = result.assets[0]
    forged_member = asset.members[0].model_copy(update={"logical_path": unsafe_path})
    forged_asset = asset.model_copy(update={"members": (forged_member, *asset.members[1:])})
    forged_result = result.model_copy(update={"assets": (forged_asset, *result.assets[1:])})
    forged_authority = replace(authority, result=forged_result)

    with pytest.raises(AssetStageVerificationError, match="unsafe"):
        materialize_verified_asset_stage(
            artifact_store=case.store,
            authority=forged_authority,
            destination_root=tmp_path / "runtime-assets",
        )


@pytest.mark.parametrize(
    "attack",
    [
        "directory",
        "short_write",
        "short_write_cleanup_failure",
        "source_drift",
        "source_drift_cleanup_failure",
    ],
)
def test_materializer_rejects_nonregular_partial_or_drifting_cas_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    case = _stage_case(tmp_path)
    result = case.application().stage(case.request())
    authority = _verify_result(case, result.model_dump(mode="json"))
    member = result.assets[0].members[0]
    cas_path = case.store.resolve(member.artifact_ref).path
    if attack == "directory":
        saved = cas_path.with_name(cas_path.name + ".saved")
        cas_path.rename(saved)
        cas_path.mkdir()
    elif attack.startswith("short_write"):
        monkeypatch.setattr(stage_verification.os, "write", lambda _fd, _payload: 0)
    else:
        real_fstat = os.fstat
        source_fd: int | None = None

        def drifting_fstat(file_descriptor: int):
            nonlocal source_fd
            status = real_fstat(file_descriptor)
            if source_fd is None:
                source_fd = file_descriptor
                return status
            if file_descriptor == source_fd:
                return SimpleNamespace(
                    st_mode=status.st_mode,
                    st_dev=status.st_dev,
                    st_ino=status.st_ino,
                    st_size=status.st_size,
                    st_mtime_ns=status.st_mtime_ns + 1,
                    st_ctime_ns=status.st_ctime_ns,
                )
            return status

        monkeypatch.setattr(stage_verification.os, "fstat", drifting_fstat)
    if attack.endswith("cleanup_failure"):
        def refuse_cleanup(*_args: object, **_kwargs: object) -> None:
            raise OSError("cleanup refused")

        monkeypatch.setattr(
            stage_verification.os,
            "unlink",
            refuse_cleanup,
        )

    with pytest.raises(AssetStageVerificationError) as caught:
        materialize_verified_asset_stage(
            artifact_store=case.store,
            authority=authority,
            destination_root=tmp_path / "runtime-assets",
        )

    assert caught.value.code == "HARN_ASSET_STAGE_MATERIALIZATION_FAILED"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"uri": "file:///member.glb"}, "exact source content"),
        ({"uri": "artifact://sha256/" + "f" * 64}, "exact source content"),
        ({"sha256": "f" * 64, "uri": "artifact://sha256/" + "f" * 64}, "exact source"),
        ({"bytes": 999}, "exact source content"),
        ({"media_type": "application/octet-stream"}, "exact source content"),
        ({"schema_version": "harness.other.v1"}, "exact source content"),
    ],
)
def test_public_stage_result_rejects_member_identity_forgery(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    result = _result_payload(tmp_path)
    member = result["assets"][0]["members"][0]
    member["artifact_ref"].update(changes)

    with pytest.raises(ValidationError, match=message):
        AssetStageResult.model_validate(result)


@pytest.mark.parametrize(
    ("attack", "message"),
    [
        ("duplicate_member", "sorted and unique"),
        ("missing_root", "contain its root"),
        ("wrong_sidecar", "sidecar must match"),
        ("missing_sidecar", "contain its model sidecar"),
        ("digest", "digest is inconsistent"),
    ],
)
def test_public_stage_result_rejects_loader_closure_forgery(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    result = _result_payload(tmp_path)
    closure = result["assets"][0]["loader_closures"][0]
    if attack == "duplicate_member":
        closure["member_logical_paths"].append(closure["member_logical_paths"][0])
    elif attack == "missing_root":
        closure["member_logical_paths"] = [
            path
            for path in closure["member_logical_paths"]
            if path != closure["root_logical_path"]
        ]
    elif attack == "wrong_sidecar":
        closure["model_sidecar_logical_path"] = "objects/003_plate/model_data1.json"
    elif attack == "missing_sidecar":
        closure["member_logical_paths"] = [
            path
            for path in closure["member_logical_paths"]
            if path != closure["model_sidecar_logical_path"]
        ]
    else:
        closure["closure_sha256"] = "f" * 64

    with pytest.raises(ValidationError, match=message):
        AssetStageResult.model_validate(result)


@pytest.mark.parametrize(
    ("attack", "message"),
    [
        ("member_order", "members must be sorted and unique"),
        ("closure_order", "loader closures must be sorted and unique"),
        ("closure_union", "must equal the enumerated loader closure"),
        ("asset_digest", "asset closure digest is inconsistent"),
    ],
)
def test_public_stage_result_rejects_asset_closure_forgery(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    result = _result_payload(tmp_path)
    asset = result["assets"][0]
    if attack == "member_order":
        asset["members"].reverse()
    elif attack == "closure_order":
        duplicate = deepcopy(asset["loader_closures"][0])
        asset["loader_closures"].append(duplicate)
    elif attack == "closure_union":
        closure = asset["loader_closures"][0]
        closure["member_logical_paths"] = closure["member_logical_paths"][:2]
        _rehash_loader_closure(asset, closure)
    else:
        asset["asset_closure_sha256"] = "f" * 64

    with pytest.raises(ValidationError, match=message):
        AssetStageResult.model_validate(result)


@pytest.mark.parametrize(
    ("attack", "message"),
    [
        ("asset_order", "canonical selection"),
        ("selection", "canonical selection"),
        ("asset_count", "aggregate binding"),
        ("member_count", "aggregate binding"),
        ("total_bytes", "aggregate binding"),
        ("stage_digest", "digest is inconsistent"),
    ],
)
def test_public_stage_result_rejects_top_level_forgery(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    result = _result_payload(tmp_path)
    if attack == "asset_order":
        result["assets"].reverse()
    elif attack == "selection":
        result["selected_asset_ids"] = result["selected_asset_ids"][:1]
    elif attack == "asset_count":
        result["staged_asset_count"] += 1
    elif attack == "member_count":
        result["staged_member_count"] += 1
    elif attack == "total_bytes":
        result["staged_total_bytes"] += 1
    else:
        result["stage_binding_sha256"] = "f" * 64

    with pytest.raises(ValidationError, match=message):
        AssetStageResult.model_validate(result)


@pytest.mark.skipif(
    REAL_ROBOTWIN_ASSET_ROOT is None,
    reason="set ROBOTWIN_ASSET_ROOT to opt into the real 21-member staging tracer",
)
def test_real_plate_and_can_source_snapshot_stages_exact_loader_closures(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    inventory_ref = store.put_file(
        TRACER_INVENTORY,
        name="asset_repair_tracer_inventory.json",
        media_type="application/json",
        schema_version="harness.asset_debt_inventory.v1",
    )
    assert inventory_ref.sha256 == (
        "d19dc71323a7c94de129f5217efc158a9076fd70c762dece6b7c7395825d4afa"
    )
    source_manifest_ref = store.put_file(
        TRACER_SOURCE_SNAPSHOT,
        name="asset_repair_tracer_source_snapshot.json",
        media_type="application/json",
        schema_version="harness.asset_source_snapshot_manifest.v1",
    )
    assert source_manifest_ref.sha256 == (
        "f463dbe36b8d31881a2a326bbff87f662aafea754502d261e8b908d0c2e5d388"
    )
    planner = AssetRepairApplication(
        artifact_store=store,
        trusted_inventory_refs=(inventory_ref,),
    )
    plan = planner.plan(
        AssetRepairPlanRequest(
            schema_version="harness.asset_repair_plan_request.v1",
            inventory_ref=inventory_ref,
            selected_asset_ids=("robotwin_003_plate", "robotwin_071_can"),
        )
    )
    plan_source = tmp_path / "asset_repair_plan.json"
    plan_source.write_bytes(_canonical_bytes(plan.model_dump(mode="json")))
    plan_ref = store.put_file(
        plan_source,
        name="asset_repair_plan.json",
        media_type="application/json",
        schema_version="harness.asset_repair_plan.v1",
    )
    application = AssetRepairApplication(
        artifact_store=store,
        trusted_inventory_refs=(inventory_ref,),
        source_snapshots=(
            LocalAssetSourceSnapshotBinding(
                manifest_ref=source_manifest_ref,
                root=Path(REAL_ROBOTWIN_ASSET_ROOT),
            ),
        ),
    )

    result = application.stage(
        AssetStageRequest(
            schema_version="harness.asset_stage_request.v1",
            inventory_ref=inventory_ref,
            repair_plan_ref=plan_ref,
            source_snapshot_manifest_ref=source_manifest_ref,
        )
    )

    assert result.staged_asset_count == 2
    assert result.staged_member_count == 21
    assert result.staged_total_bytes == 57_290_434
    assert result.stage_binding_sha256 == (
        "fcf73ec7851e42654a2264d818bfcc60d091ad3669429ecee7f5b7ccd7e0eb89"
    )
    assert all(
        closure.member_logical_paths
        == tuple(sorted((closure.root_logical_path, closure.model_sidecar_logical_path)))
        for asset in result.assets
        for closure in asset.loader_closures
    )
    assert all(
        member.artifact_ref.media_type == "application/json"
        and member.artifact_ref.schema_version is None
        for asset in result.assets
        for member in asset.members
        if member.logical_path.endswith(".json")
    )
    assert result.simulator_executed is False
    assert result.runtime_qualification_executed is False
    assert result.promotion_executed is False
    assert result.authoritative_ledger_writes_performed is False
    source_root = Path(REAL_ROBOTWIN_ASSET_ROOT)
    for asset in result.assets:
        for member in asset.members:
            source_payload = (source_root / member.logical_path).read_bytes()
            assert len(source_payload) == member.source_bytes
            assert _sha256(source_payload) == member.source_sha256
            assert store.resolve(member.artifact_ref).path.read_bytes() == source_payload
    (tmp_path / "asset_stage_result.json").write_bytes(
        _canonical_bytes(result.model_dump(mode="json"))
    )

"""Public contracts for exact-byte asset debt staging."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import Field, GetJsonSchemaHandler, model_validator

from .asset_repair import AssetId, LogicalPath, RepresentationRole
from .base import HarnessModel, PositiveInt, Sha256, public_schema_config
from .common import ArtifactRef

ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID = "harness.asset_source_snapshot_manifest.v1"
ASSET_STAGE_REQUEST_SCHEMA_ID = "harness.asset_stage_request.v1"
ASSET_STAGE_RESULT_SCHEMA_ID = "harness.asset_stage_result.v1"

_CAS_URI = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
_CAS_URI_PATTERN = r"^artifact://sha256/[0-9a-f]{64}$"
_CAS_URI_LENGTH = len("artifact://sha256/") + 64


def _require_json_ref(ref: ArtifactRef, *, field: str, schema_version: str) -> None:
    match = _CAS_URI.fullmatch(ref.uri)
    if match is None or match.group(1) != ref.sha256:
        raise ValueError(f"{field} must use its content-addressed artifact URI")
    if ref.media_type != "application/json":
        raise ValueError(f"{field} must use media_type='application/json'")
    if ref.schema_version != schema_version:
        raise ValueError(f"{field} must have schema_version={schema_version}")


def _express_json_ref_contracts(
    schema: dict[str, Any],
    contracts: dict[str, str],
) -> dict[str, Any]:
    for field, schema_version in contracts.items():
        field_schema = schema["properties"][field]
        artifact_ref = field_schema.pop("$ref")
        field_schema["allOf"] = [
            {"$ref": artifact_ref},
            {
                "properties": {
                    "uri": {
                        "maxLength": _CAS_URI_LENGTH,
                        "minLength": _CAS_URI_LENGTH,
                        "pattern": _CAS_URI_PATTERN,
                    },
                    "media_type": {"const": "application/json"},
                    "schema_version": {"const": schema_version},
                },
                "type": "object",
            },
        ]
    return schema


def _canonical_sha256(value: object) -> str:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AssetSourceSnapshotMember(HarnessModel):
    logical_path: LogicalPath
    sha256: Sha256
    bytes: PositiveInt


class AssetSourceSnapshotAsset(HarnessModel):
    asset_id: AssetId
    logical_root: LogicalPath
    members: tuple[AssetSourceSnapshotMember, ...] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def members_are_an_exact_canonical_tree(self) -> "AssetSourceSnapshotAsset":
        paths = tuple(member.logical_path for member in self.members)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("source snapshot members must be sorted and unique")
        prefix = self.logical_root + "/"
        if any(not path.startswith(prefix) for path in paths):
            raise ValueError("source snapshot members must stay beneath logical_root")
        return self


class AssetSourceSnapshotManifest(HarnessModel):
    model_config = public_schema_config(ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID)

    schema_version: Literal["harness.asset_source_snapshot_manifest.v1"]
    snapshot_id: AssetId
    inventory_ref: ArtifactRef
    source_kind: Literal["local_unversioned_robotwin_assets"]
    source_revision: Literal[None]
    capture_method: Literal["exact_byte_manifest.v1"]
    assets: tuple[AssetSourceSnapshotAsset, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def manifest_is_canonical_and_bound(self) -> "AssetSourceSnapshotManifest":
        _require_json_ref(
            self.inventory_ref,
            field="inventory_ref",
            schema_version="harness.asset_debt_inventory.v1",
        )
        asset_ids = tuple(asset.asset_id for asset in self.assets)
        if asset_ids != tuple(sorted(set(asset_ids))):
            raise ValueError("source snapshot assets must be sorted and unique")
        all_paths = tuple(
            member.logical_path for asset in self.assets for member in asset.members
        )
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("source snapshot member paths must be globally unique")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        return _express_json_ref_contracts(
            handler(core_schema),
            {"inventory_ref": "harness.asset_debt_inventory.v1"},
        )


class AssetStageRequest(HarnessModel):
    model_config = public_schema_config(ASSET_STAGE_REQUEST_SCHEMA_ID)

    schema_version: Literal["harness.asset_stage_request.v1"]
    inventory_ref: ArtifactRef
    repair_plan_ref: ArtifactRef
    source_snapshot_manifest_ref: ArtifactRef

    @model_validator(mode="after")
    def references_are_content_bound(self) -> "AssetStageRequest":
        for field, ref, schema_version in (
            ("inventory_ref", self.inventory_ref, "harness.asset_debt_inventory.v1"),
            ("repair_plan_ref", self.repair_plan_ref, "harness.asset_repair_plan.v1"),
            (
                "source_snapshot_manifest_ref",
                self.source_snapshot_manifest_ref,
                ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID,
            ),
        ):
            _require_json_ref(ref, field=field, schema_version=schema_version)
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        return _express_json_ref_contracts(
            handler(core_schema),
            {
                "inventory_ref": "harness.asset_debt_inventory.v1",
                "repair_plan_ref": "harness.asset_repair_plan.v1",
                "source_snapshot_manifest_ref": ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID,
            },
        )


class StagedAssetMember(HarnessModel):
    logical_path: LogicalPath
    source_sha256: Sha256
    source_bytes: PositiveInt
    artifact_ref: ArtifactRef

    @model_validator(mode="after")
    def artifact_is_the_exact_source_content(self) -> "StagedAssetMember":
        match = _CAS_URI.fullmatch(self.artifact_ref.uri)
        if (
            match is None
            or match.group(1) != self.artifact_ref.sha256
            or self.artifact_ref.sha256 != self.source_sha256
            or self.artifact_ref.bytes != self.source_bytes
            or self.artifact_ref.schema_version is not None
        ):
            raise ValueError("staged artifact must be the exact source content in CAS")
        return self


class AssetLoaderClosure(HarnessModel):
    model_id: int = Field(strict=True, ge=0)
    role: RepresentationRole
    root_logical_path: LogicalPath
    member_logical_paths: tuple[LogicalPath, ...] = Field(min_length=1, max_length=4096)
    closure_sha256: Sha256

    @model_validator(mode="after")
    def closure_is_canonical_and_hash_bound(self) -> "AssetLoaderClosure":
        if self.member_logical_paths != tuple(sorted(set(self.member_logical_paths))):
            raise ValueError("loader closure members must be sorted and unique")
        if self.root_logical_path not in self.member_logical_paths:
            raise ValueError("loader closure must contain its root")
        if self.closure_sha256 != _canonical_sha256(list(self.member_logical_paths)):
            raise ValueError("loader closure digest is inconsistent")
        return self


class StagedAsset(HarnessModel):
    asset_id: AssetId
    source_ledger_sha256: Sha256
    members: tuple[StagedAssetMember, ...] = Field(min_length=1, max_length=4096)
    loader_closures: tuple[AssetLoaderClosure, ...] = Field(min_length=1, max_length=4096)
    asset_closure_sha256: Sha256

    @model_validator(mode="after")
    def asset_closure_is_exact_and_hash_bound(self) -> "StagedAsset":
        member_paths = tuple(member.logical_path for member in self.members)
        if member_paths != tuple(sorted(set(member_paths))):
            raise ValueError("staged asset members must be sorted and unique")
        closure_ids = tuple(
            (closure.model_id, closure.role.value, closure.root_logical_path)
            for closure in self.loader_closures
        )
        if closure_ids != tuple(sorted(set(closure_ids))):
            raise ValueError("loader closures must be sorted and unique")
        closure_members = {
            path for closure in self.loader_closures for path in closure.member_logical_paths
        }
        if closure_members != set(member_paths):
            raise ValueError("staged members must equal the enumerated loader closure")
        digest_payload = self.model_dump(mode="json", exclude={"asset_closure_sha256"})
        if self.asset_closure_sha256 != _canonical_sha256(digest_payload):
            raise ValueError("asset closure digest is inconsistent")
        return self


class AssetStageResult(HarnessModel):
    model_config = public_schema_config(ASSET_STAGE_RESULT_SCHEMA_ID)

    schema_version: Literal["harness.asset_stage_result.v1"]
    inventory_ref: ArtifactRef
    repair_plan_ref: ArtifactRef
    source_snapshot_manifest_ref: ArtifactRef
    selected_asset_ids: tuple[AssetId, ...] = Field(min_length=1, max_length=256)
    staged_asset_count: PositiveInt
    staged_member_count: PositiveInt
    staged_total_bytes: PositiveInt
    assets: tuple[StagedAsset, ...] = Field(min_length=1, max_length=256)
    stage_binding_sha256: Sha256
    exact_source_bytes_staged: Literal[True]
    loader_closure_enumerated: Literal[True]
    simulator_executed: Literal[False]
    runtime_qualification_executed: Literal[False]
    promotion_executed: Literal[False]
    authoritative_ledger_writes_performed: Literal[False]

    @model_validator(mode="after")
    def result_is_exact_and_hash_bound(self) -> "AssetStageResult":
        for field, ref, schema_version in (
            ("inventory_ref", self.inventory_ref, "harness.asset_debt_inventory.v1"),
            ("repair_plan_ref", self.repair_plan_ref, "harness.asset_repair_plan.v1"),
            (
                "source_snapshot_manifest_ref",
                self.source_snapshot_manifest_ref,
                ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID,
            ),
        ):
            _require_json_ref(ref, field=field, schema_version=schema_version)
        asset_ids = tuple(asset.asset_id for asset in self.assets)
        if asset_ids != tuple(sorted(set(asset_ids))) or asset_ids != self.selected_asset_ids:
            raise ValueError("staged assets must equal the canonical selection")
        if (
            self.staged_asset_count != len(self.assets)
            or self.staged_member_count != sum(len(asset.members) for asset in self.assets)
            or self.staged_total_bytes
            != sum(member.source_bytes for asset in self.assets for member in asset.members)
        ):
            raise ValueError("stage result aggregate binding is inconsistent")
        digest_payload = self.model_dump(mode="json", exclude={"stage_binding_sha256"})
        if self.stage_binding_sha256 != _canonical_sha256(digest_payload):
            raise ValueError("stage result digest is inconsistent")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        return _express_json_ref_contracts(
            handler(core_schema),
            {
                "inventory_ref": "harness.asset_debt_inventory.v1",
                "repair_plan_ref": "harness.asset_repair_plan.v1",
                "source_snapshot_manifest_ref": ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID,
            },
        )


def canonical_sha256(value: object) -> str:
    """Return the contract's canonical JSON digest for assembled stage records."""

    return _canonical_sha256(value)


__all__ = [
    "ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID",
    "ASSET_STAGE_REQUEST_SCHEMA_ID",
    "ASSET_STAGE_RESULT_SCHEMA_ID",
    "AssetSourceSnapshotManifest",
    "AssetStageRequest",
    "AssetStageResult",
]

"""Public contracts for read-only asset debt planning."""

from __future__ import annotations

import re
from collections import Counter
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, GetJsonSchemaHandler, StrictBool, model_validator

from .base import HarnessModel, NonNegativeInt, PositiveInt, Sha256, public_schema_config
from .common import ArtifactRef

ASSET_DEBT_INVENTORY_SCHEMA_ID = "harness.asset_debt_inventory.v1"
ASSET_REPAIR_PLAN_REQUEST_SCHEMA_ID = "harness.asset_repair_plan_request.v1"
ASSET_REPAIR_PLAN_SCHEMA_ID = "harness.asset_repair_plan.v1"

_CAS_URI = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
_CAS_URI_PATTERN = r"^artifact://sha256/[0-9a-f]{64}$"
_CAS_URI_LENGTH = len("artifact://sha256/") + 64
_PORTABLE_PATH_SCHEMA_PATTERN = (
    r"^(?!/)(?![A-Za-z]:)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*\\).+$"
)

AssetId = Annotated[
    str,
    Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,127}$"),
]
GitCommit = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]


def _portable_posix_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        value == "."
        or value.startswith("/")
        or "\\" in value
        or PureWindowsPath(value).drive != ""
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError("path must be a portable POSIX-relative path")
    return value


def _require_inventory_ref(ref: ArtifactRef) -> None:
    match = _CAS_URI.fullmatch(ref.uri)
    if match is None or match.group(1) != ref.sha256:
        raise ValueError("inventory_ref must use its content-addressed artifact URI")
    if ref.media_type != "application/json":
        raise ValueError("inventory_ref must use media_type='application/json'")
    if ref.schema_version != ASSET_DEBT_INVENTORY_SCHEMA_ID:
        raise ValueError(
            f"inventory_ref must have schema_version={ASSET_DEBT_INVENTORY_SCHEMA_ID}"
        )


def _express_inventory_ref_contract(schema: dict[str, Any]) -> dict[str, Any]:
    field_schema = schema["properties"]["inventory_ref"]
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
                "schema_version": {"const": ASSET_DEBT_INVENTORY_SCHEMA_ID},
            },
            "type": "object",
        },
    ]
    return schema


LogicalPath = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=1024,
        json_schema_extra={"pattern": _PORTABLE_PATH_SCHEMA_PATTERN},
    ),
    AfterValidator(_portable_posix_path),
]
ViolationCode = Annotated[
    str,
    Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$"),
]


class InventoryScope(str, Enum):
    STRUCTURAL_TRACER = "structural_tracer"
    FULL_BASELINE = "full_baseline"


class SourceCohort(str, Enum):
    UPSTREAM = "upstream"
    HISTORICAL_ASSET_LIBRARY = "historical_asset_library"


class RepresentationRole(str, Enum):
    COLLISION = "collision"
    VISUAL = "visual"


class ProbeAvailability(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"


class RepairDisposition(str, Enum):
    OBSERVED_DIGEST_MATCH = "observed_primary_digest_match"
    OBSERVED_DIGEST_MATCH_REMEASURE_SIZE = (
        "observed_primary_digest_match_remeasure_size"
    )
    HASH_MISMATCH_NEW_IDENTITY_OR_RETIRE = "hash_mismatch_new_identity_or_retire"
    SOURCE_MISSING_NEW_IDENTITY_OR_RETIRE = "source_missing_new_identity_or_retire"


class AssetRepairNextStep(str, Enum):
    STAGE_AND_REHASH = "stage_and_rehash_observed_primary"
    STAGE_AND_REHASH_REMEASURE_SIZE = (
        "stage_and_rehash_observed_primary_remeasure_size"
    )
    BUILD_NEW_IDENTITY_OR_RETIRE = "build_new_identity_or_retire_asset"


_REQUIRED_FOLLOWUP = (
    "stage_and_rehash_bytes",
    "enumerate_loader_closure",
    "qualify_collision_provenance",
    "qualify_genesis_runtime",
    "qualify_settle",
)


class DebtViolationCount(HarnessModel):
    code: ViolationCode
    count: PositiveInt


class RepresentationRecoveryProbe(HarnessModel):
    model_id: NonNegativeInt
    role: RepresentationRole
    logical_path: LogicalPath
    declared_sha256: Sha256
    declared_bytes: PositiveInt
    availability: ProbeAvailability
    observed_sha256: Sha256 | None
    observed_bytes: PositiveInt | None

    @model_validator(mode="after")
    def observed_identity_matches_availability(self) -> "RepresentationRecoveryProbe":
        observed = (self.observed_sha256, self.observed_bytes)
        if self.availability == ProbeAvailability.AVAILABLE and None in observed:
            raise ValueError("available probes require observed identity")
        if self.availability == ProbeAvailability.MISSING and observed != (None, None):
            raise ValueError("missing probes must omit observed identity")
        return self


class AssetDebtEntry(HarnessModel):
    asset_id: AssetId
    source_cohort: SourceCohort
    ledger_path: LogicalPath
    ledger_sha256: Sha256
    ledger_bytes: PositiveInt
    violation_counts: tuple[DebtViolationCount, ...] = Field(min_length=1)
    recovery_probes: tuple[RepresentationRecoveryProbe, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def details_are_canonical(self) -> "AssetDebtEntry":
        violation_codes = tuple(item.code for item in self.violation_counts)
        if violation_codes != tuple(sorted(set(violation_codes))):
            raise ValueError("violation_counts must be sorted and unique by code")
        probe_ids = tuple(
            (probe.model_id, probe.role.value, probe.logical_path)
            for probe in self.recovery_probes
        )
        if probe_ids != tuple(sorted(set(probe_ids))):
            raise ValueError("recovery_probes must be sorted and unique by representation")
        return self


class AssetDebtInventory(HarnessModel):
    model_config = public_schema_config(ASSET_DEBT_INVENTORY_SCHEMA_ID)

    schema_version: Literal["harness.asset_debt_inventory.v1"]
    inventory_id: AssetId
    scope: InventoryScope
    source_ledger_commit: GitCommit
    byte_probe_source: Literal["local_unversioned_robotwin_assets"]
    byte_probe_revision: Literal[None]
    entries: tuple[AssetDebtEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def entries_are_canonical(self) -> "AssetDebtInventory":
        asset_ids = tuple(entry.asset_id for entry in self.entries)
        if asset_ids != tuple(sorted(set(asset_ids))):
            raise ValueError("entries must be sorted and unique by asset_id")
        return self


class AssetRepairPlanRequest(HarnessModel):
    model_config = public_schema_config(ASSET_REPAIR_PLAN_REQUEST_SCHEMA_ID)

    schema_version: Literal["harness.asset_repair_plan_request.v1"]
    inventory_ref: ArtifactRef
    selected_asset_ids: tuple[AssetId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def inventory_ref_is_bound(self) -> "AssetRepairPlanRequest":
        if self.selected_asset_ids != tuple(sorted(set(self.selected_asset_ids))):
            raise ValueError("selected_asset_ids must be sorted and unique")
        _require_inventory_ref(self.inventory_ref)
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        return _express_inventory_ref_contract(handler(core_schema))


class RepresentationRepairPlan(HarnessModel):
    model_id: NonNegativeInt
    role: RepresentationRole
    logical_path: LogicalPath
    declared_sha256: Sha256
    declared_bytes: PositiveInt
    observed_sha256: Sha256 | None
    observed_bytes: PositiveInt | None
    disposition: RepairDisposition

    @model_validator(mode="after")
    def disposition_is_derived_from_observed_identity(self) -> "RepresentationRepairPlan":
        observed = (self.observed_sha256, self.observed_bytes)
        if observed == (None, None):
            expected = RepairDisposition.SOURCE_MISSING_NEW_IDENTITY_OR_RETIRE
        elif None in observed:
            raise ValueError("representation plan requires both observed identity fields")
        elif self.observed_sha256 != self.declared_sha256:
            expected = RepairDisposition.HASH_MISMATCH_NEW_IDENTITY_OR_RETIRE
        elif self.observed_bytes != self.declared_bytes:
            expected = RepairDisposition.OBSERVED_DIGEST_MATCH_REMEASURE_SIZE
        else:
            expected = RepairDisposition.OBSERVED_DIGEST_MATCH
        if self.disposition != expected:
            raise ValueError("representation disposition must match observed identity")
        return self


class AssetRepairEntryPlan(HarnessModel):
    asset_id: AssetId
    ledger_sha256: Sha256
    violation_count: PositiveInt
    representations: tuple[RepresentationRepairPlan, ...] = Field(min_length=1)
    next_step: AssetRepairNextStep
    required_followup: tuple[
        Literal[
            "enumerate_loader_closure",
            "stage_and_rehash_bytes",
            "qualify_collision_provenance",
            "qualify_genesis_runtime",
            "qualify_settle",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def actions_are_derived_from_representations(self) -> "AssetRepairEntryPlan":
        representation_ids = tuple(
            (item.model_id, item.role.value, item.logical_path)
            for item in self.representations
        )
        if representation_ids != tuple(sorted(set(representation_ids))):
            raise ValueError("plan representations must be sorted and unique")
        if self.required_followup != _REQUIRED_FOLLOWUP:
            raise ValueError("required_followup must contain the exact safety gates")
        dispositions = {item.disposition for item in self.representations}
        if dispositions & {
            RepairDisposition.HASH_MISMATCH_NEW_IDENTITY_OR_RETIRE,
            RepairDisposition.SOURCE_MISSING_NEW_IDENTITY_OR_RETIRE,
        }:
            expected = AssetRepairNextStep.BUILD_NEW_IDENTITY_OR_RETIRE
        elif RepairDisposition.OBSERVED_DIGEST_MATCH_REMEASURE_SIZE in dispositions:
            expected = AssetRepairNextStep.STAGE_AND_REHASH_REMEASURE_SIZE
        else:
            expected = AssetRepairNextStep.STAGE_AND_REHASH
        if self.next_step != expected:
            raise ValueError("next_step must match representation dispositions")
        return self


class AssetRepairDispositionCounts(HarnessModel):
    observed_primary_digest_match: NonNegativeInt
    observed_primary_digest_match_remeasure_size: NonNegativeInt
    hash_mismatch_new_identity_or_retire: NonNegativeInt
    source_missing_new_identity_or_retire: NonNegativeInt


class AssetRepairPlan(HarnessModel):
    model_config = public_schema_config(ASSET_REPAIR_PLAN_SCHEMA_ID)

    schema_version: Literal["harness.asset_repair_plan.v1"]
    inventory_ref: ArtifactRef
    inventory_scope: InventoryScope
    selected_asset_ids: tuple[AssetId, ...] = Field(min_length=1)
    planned_ledger_count: PositiveInt
    planned_violation_count: PositiveInt
    planned_representation_count: PositiveInt
    disposition_counts: AssetRepairDispositionCounts
    items: tuple[AssetRepairEntryPlan, ...] = Field(min_length=1)
    full_baseline_evaluated: StrictBool
    probe_bytes_in_cas: Literal[False]
    runtime_qualification_executed: Literal[False]
    writes_performed: Literal[False]

    @model_validator(mode="after")
    def inventory_ref_is_bound(self) -> "AssetRepairPlan":
        _require_inventory_ref(self.inventory_ref)
        item_ids = tuple(item.asset_id for item in self.items)
        if item_ids != tuple(sorted(set(item_ids))) or item_ids != self.selected_asset_ids:
            raise ValueError("plan item/selection binding is inconsistent")
        if (
            self.planned_ledger_count != len(self.items)
            or self.planned_violation_count != sum(item.violation_count for item in self.items)
            or self.planned_representation_count
            != sum(len(item.representations) for item in self.items)
        ):
            raise ValueError("plan aggregate binding is inconsistent")
        observed_counts = Counter(
            representation.disposition.value
            for item in self.items
            for representation in item.representations
        )
        expected_counts = {
            field: observed_counts[field]
            for field in AssetRepairDispositionCounts.model_fields
        }
        if self.disposition_counts.model_dump() != expected_counts:
            raise ValueError("plan disposition-count binding is inconsistent")
        expected_full_baseline = self.inventory_scope == InventoryScope.FULL_BASELINE
        if self.full_baseline_evaluated is not expected_full_baseline:
            raise ValueError("full_baseline_evaluated must match inventory_scope")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        return _express_inventory_ref_contract(handler(core_schema))


__all__ = [
    "ASSET_DEBT_INVENTORY_SCHEMA_ID",
    "ASSET_REPAIR_PLAN_REQUEST_SCHEMA_ID",
    "ASSET_REPAIR_PLAN_SCHEMA_ID",
    "AssetDebtInventory",
    "AssetRepairPlan",
    "AssetRepairPlanRequest",
]

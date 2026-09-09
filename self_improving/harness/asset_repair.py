"""Read-only S5 application for classifying historical asset debt."""

from __future__ import annotations

import json
from collections import Counter

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .asset_staging import (
    LocalAssetSourceSnapshotBinding,
    stage_asset_repair,
    validate_source_snapshot_bindings,
)
from .schemas.asset_repair import (
    AssetDebtEntry,
    AssetDebtInventory,
    AssetRepairDispositionCounts,
    AssetRepairEntryPlan,
    AssetRepairPlan,
    AssetRepairPlanRequest,
    RepairDisposition,
    RepresentationRecoveryProbe,
    RepresentationRepairPlan,
)
from .schemas.asset_staging import AssetStageRequest, AssetStageResult
from .schemas.common import ArtifactRef

_REQUIRED_FOLLOWUP = (
    "stage_and_rehash_bytes",
    "enumerate_loader_closure",
    "qualify_collision_provenance",
    "qualify_genesis_runtime",
    "qualify_settle",
)


class AssetRepairError(ValueError):
    """A stable fail-closed error at the public S5 plan seam."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AssetRepairApplication:
    """Plan repairs from an immutable audit artifact without changing state."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        trusted_inventory_refs: tuple[ArtifactRef, ...],
        source_snapshots: tuple[LocalAssetSourceSnapshotBinding, ...] = (),
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        self._artifact_store = artifact_store
        self._trusted_inventory_refs = frozenset(trusted_inventory_refs)
        self._source_snapshots = validate_source_snapshot_bindings(source_snapshots)

    def plan(self, request: AssetRepairPlanRequest) -> AssetRepairPlan:
        """Classify the selected debt inventory; never stage or publish bytes."""

        if request.inventory_ref not in self._trusted_inventory_refs:
            raise AssetRepairError(
                "HARN_UNTRUSTED_ASSET_DEBT_INVENTORY",
                "inventory_ref is not in the trusted inventory set",
            )
        try:
            payload = self._artifact_store.resolve(request.inventory_ref).path.read_bytes()
        except ArtifactResolutionError as error:
            raise AssetRepairError(
                "HARN_ARTIFACT_UNAVAILABLE",
                f"asset debt inventory is unavailable: {error}",
            ) from error
        try:
            inventory = AssetDebtInventory.model_validate_json(payload)
        except (ValidationError, TypeError, ValueError) as error:
            raise AssetRepairError(
                "HARN_INPUT_SCHEMA_INVALID",
                f"asset debt inventory is invalid: {error}",
            ) from error
        if payload != _canonical_model_bytes(inventory):
            raise AssetRepairError(
                "HARN_INPUT_SCHEMA_INVALID",
                "asset debt inventory must use strict canonical JSON bytes",
            )
        selected = set(request.selected_asset_ids)
        missing = selected - {entry.asset_id for entry in inventory.entries}
        if missing:
            raise AssetRepairError(
                "HARN_ASSET_NOT_IN_INVENTORY",
                "assets not present in inventory: " + ", ".join(sorted(missing)),
            )
        entries = tuple(entry for entry in inventory.entries if entry.asset_id in selected)

        plans = tuple(self._plan_entry(entry) for entry in entries)
        dispositions = Counter(
            representation.disposition.value
            for item in plans
            for representation in item.representations
        )
        return AssetRepairPlan(
            schema_version="harness.asset_repair_plan.v1",
            inventory_ref=request.inventory_ref,
            inventory_scope=inventory.scope,
            source_population_ledger_count=inventory.source_population_ledger_count,
            source_population_violation_count=inventory.source_population_violation_count,
            source_population_primary_probe_count=(
                inventory.source_population_primary_probe_count
            ),
            inventory_ledger_count=len(inventory.entries),
            inventory_violation_count=sum(
                violation.count
                for entry in inventory.entries
                for violation in entry.violation_counts
            ),
            inventory_primary_probe_count=sum(
                len(entry.recovery_probes) for entry in inventory.entries
            ),
            selected_asset_ids=request.selected_asset_ids,
            planned_ledger_count=len(plans),
            planned_violation_count=sum(item.violation_count for item in plans),
            planned_representation_count=sum(len(item.representations) for item in plans),
            disposition_counts=AssetRepairDispositionCounts(
                observed_primary_digest_match=dispositions[
                    RepairDisposition.OBSERVED_DIGEST_MATCH.value
                ],
                observed_primary_digest_match_remeasure_size=dispositions[
                    RepairDisposition.OBSERVED_DIGEST_MATCH_REMEASURE_SIZE.value
                ],
                hash_mismatch_new_identity_or_retire=dispositions[
                    RepairDisposition.HASH_MISMATCH_NEW_IDENTITY_OR_RETIRE.value
                ],
                source_missing_new_identity_or_retire=dispositions[
                    RepairDisposition.SOURCE_MISSING_NEW_IDENTITY_OR_RETIRE.value
                ],
            ),
            items=plans,
            full_baseline_evaluated=(
                inventory.scope == "full_baseline"
                and len(entries) == len(inventory.entries)
            ),
            probe_bytes_in_cas=False,
            runtime_qualification_executed=False,
            writes_performed=False,
        )

    def stage(self, request: AssetStageRequest) -> AssetStageResult:
        """Copy an exact, manifest-bound loader closure into CAS without promotion."""

        return stage_asset_repair(
            artifact_store=self._artifact_store,
            source_snapshots=self._source_snapshots,
            request=request,
            rebuild_plan=self.plan,
        )

    def _plan_entry(self, entry: AssetDebtEntry) -> AssetRepairEntryPlan:
        representations = tuple(self._plan_representation(probe) for probe in entry.recovery_probes)
        if any(
            item.disposition
            in {
                RepairDisposition.HASH_MISMATCH_NEW_IDENTITY_OR_RETIRE,
                RepairDisposition.SOURCE_MISSING_NEW_IDENTITY_OR_RETIRE,
            }
            for item in representations
        ):
            next_step = "build_new_identity_or_retire_asset"
        elif any(
            item.disposition == RepairDisposition.OBSERVED_DIGEST_MATCH_REMEASURE_SIZE
            for item in representations
        ):
            next_step = "stage_and_rehash_observed_primary_remeasure_size"
        else:
            next_step = "stage_and_rehash_observed_primary"
        return AssetRepairEntryPlan(
            asset_id=entry.asset_id,
            ledger_sha256=entry.ledger_sha256,
            violation_count=sum(item.count for item in entry.violation_counts),
            representations=representations,
            next_step=next_step,
            required_followup=_REQUIRED_FOLLOWUP,
        )

    @staticmethod
    def _plan_representation(probe: RepresentationRecoveryProbe) -> RepresentationRepairPlan:
        if probe.availability == "available":
            if probe.observed_sha256 != probe.declared_sha256:
                disposition = RepairDisposition.HASH_MISMATCH_NEW_IDENTITY_OR_RETIRE
            elif probe.observed_bytes != probe.declared_bytes:
                disposition = RepairDisposition.OBSERVED_DIGEST_MATCH_REMEASURE_SIZE
            else:
                disposition = RepairDisposition.OBSERVED_DIGEST_MATCH
        else:
            disposition = RepairDisposition.SOURCE_MISSING_NEW_IDENTITY_OR_RETIRE
        return RepresentationRepairPlan(
            model_id=probe.model_id,
            role=probe.role,
            logical_path=probe.logical_path,
            declared_sha256=probe.declared_sha256,
            declared_bytes=probe.declared_bytes,
            observed_sha256=probe.observed_sha256,
            observed_bytes=probe.observed_bytes,
            disposition=disposition,
        )


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


__all__ = ["AssetRepairApplication", "AssetRepairError"]

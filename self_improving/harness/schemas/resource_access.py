"""Strict audit receipt for one authority-bound workflow resource delivery."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, AwareDatetime, BeforeValidator, model_validator

from .base import HarnessModel, NonNegativeInt, Sha256, public_schema_config
from .common import ArtifactRef
from .workflow import PrincipalId, RunSnapshot

RESOURCE_ACCESS_RECEIPT_SCHEMA_ID = "harness.resource_access_receipt.v1"
_RESOURCE_ACCESS_DOMAIN = "harness.resource_access_receipt.identity.v1"
_RESOURCE_ACCESS_REQUEST_DOMAIN = "harness.resource_access_request.identity.v1"
_WORKFLOW_ARTIFACT_URI = re.compile(
    r"^harness://workflows/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"/artifacts/([0-9a-f]{64})$"
)


def _require_exact_bool(value: object) -> object:
    if type(value) is not bool:
        raise ValueError("value must be an exact boolean")
    return value


ExactFalse = Annotated[Literal[False], BeforeValidator(_require_exact_bool)]


class ResourceAccessReceipt(HarnessModel):
    """Server-owned proof that exact verified bytes were placed in a response payload."""

    model_config = public_schema_config(RESOURCE_ACCESS_RECEIPT_SCHEMA_ID)

    schema_version: Literal["harness.resource_access_receipt.v1"]
    access_id: UUID4
    principal_id: PrincipalId
    workflow_run_id: UUID4
    resource_uri: str
    resource: ArtifactRef
    delivered_sha256: Sha256
    delivered_bytes: NonNegativeInt
    representation: Literal["text_utf8", "blob_base64"]
    observed_snapshot: RunSnapshot
    observed_snapshot_sha256: Sha256
    served_at: AwareDatetime
    delivery_scope: Literal["server_response_payload"]
    transport_acknowledged: ExactFalse
    model_understanding_claimed: ExactFalse
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def delivery_is_exact_and_bounded(self) -> "ResourceAccessReceipt":
        match = _WORKFLOW_ARTIFACT_URI.fullmatch(self.resource_uri)
        if match is None:
            raise ValueError("resource_uri must be a workflow-scoped artifact URI")
        if match.group(1) != str(self.workflow_run_id) or match.group(2) != self.resource.sha256:
            raise ValueError("resource_uri differs from workflow or resource identity")
        if self.delivered_sha256 != self.resource.sha256:
            raise ValueError("delivered_sha256 must equal resource identity")
        if self.delivered_bytes != self.resource.bytes:
            raise ValueError("delivered_bytes must equal resource size")
        if self.principal_id != self.observed_snapshot.principal_id:
            raise ValueError("principal differs from observed snapshot")
        if self.workflow_run_id != self.observed_snapshot.workflow_run_id:
            raise ValueError("workflow differs from observed snapshot")
        if self.observed_snapshot_sha256 != _snapshot_sha256(self.observed_snapshot):
            raise ValueError("observed_snapshot_sha256 does not match observed snapshot")
        expected_representation = (
            "text_utf8"
            if self.resource.media_type in {"application/json", "application/x-ndjson"}
            else "blob_base64"
        )
        if self.representation != expected_representation:
            raise ValueError("representation differs from resource media type")
        if self.served_at.utcoffset() != timezone.utc.utcoffset(self.served_at):
            raise ValueError("served_at must use UTC")
        if self.served_at < self.observed_snapshot.updated_at:
            raise ValueError("served_at predates the observed snapshot")
        if self.receipt_sha256 != resource_access_receipt_sha256(self):
            raise ValueError("receipt_sha256 does not match resource access identity")
        return self


def resource_access_receipt_sha256(receipt: ResourceAccessReceipt) -> str:
    payload = {
        "domain": _RESOURCE_ACCESS_DOMAIN,
        "receipt": receipt.model_dump(mode="json", exclude={"receipt_sha256"}),
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def resource_access_request_sha256(
    *,
    principal_id: str,
    workflow_run_id: UUID,
    resource_uri: str,
    resource: ArtifactRef,
    observed_snapshot_sha256: str,
) -> str:
    """Derive the stable authority-bound identity for one exact delivery request."""

    return hashlib.sha256(
        _canonical_bytes(
            {
                "domain": _RESOURCE_ACCESS_REQUEST_DOMAIN,
                "principal_id": principal_id,
                "workflow_run_id": str(workflow_run_id),
                "resource_uri": resource_uri,
                "resource": resource.model_dump(mode="json"),
                "observed_snapshot_sha256": observed_snapshot_sha256,
            }
        )
    ).hexdigest()


def _snapshot_sha256(snapshot: RunSnapshot) -> str:
    return hashlib.sha256(_canonical_bytes(snapshot.model_dump(mode="json"))).hexdigest()


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


__all__ = [
    "RESOURCE_ACCESS_RECEIPT_SCHEMA_ID",
    "ResourceAccessReceipt",
    "resource_access_receipt_sha256",
    "resource_access_request_sha256",
]

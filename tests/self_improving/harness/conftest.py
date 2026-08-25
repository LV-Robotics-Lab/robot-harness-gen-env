from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest

RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")
STARTED_AT = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
ENDED_AT = datetime(2026, 8, 17, 10, 1, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


@pytest.fixture
def run_id() -> UUID:
    return RUN_ID


@pytest.fixture
def started_at() -> datetime:
    return STARTED_AT


@pytest.fixture
def ended_at() -> datetime:
    return ENDED_AT


@pytest.fixture
def artifact_payload():
    def build(
        schema_version: str | None = "robotwin.asset_catalog.v1",
        *,
        name: str = "artifact",
        sha256: str = SHA_A,
        media_type: str = "application/json",
    ) -> dict[str, Any]:
        return {
            "name": name,
            "uri": f"file:///tmp/{name}",
            "media_type": media_type,
            "sha256": sha256,
            "bytes": 123,
            "schema_version": schema_version,
        }

    return build


@pytest.fixture
def blocker_payload(artifact_payload):
    def build(*, code: str = "T2E_VALIDATION_FAILED") -> dict[str, Any]:
        return {
            "code": code,
            "message": "validation did not pass",
            "stage": "validate",
            "retryable": False,
            "details": {"check": "support_contact"},
            "unknowns": [
                {"field": "contact", "reason": "missing", "source": "runtime"}
            ],
            "artifact_refs": [
                artifact_payload("robotwin.scene_validation.v1", name="report")
            ],
        }

    return build


@pytest.fixture
def environment_package_payload(artifact_payload):
    def build() -> dict[str, Any]:
        return {
            "package_id": SHA_B,
            "route_id": "text2env",
            "producer_skill_ref": "text2env.compile@1.0.0",
            "seed": 42,
            "scene_spec_sha256": SHA_C,
            "resolved_scene_sha256": SHA_B,
            "asset_catalog": artifact_payload(
                "robotwin.asset_catalog.v1",
                name="asset_catalog",
                sha256=SHA_A,
            ),
            "package_manifest": artifact_payload(
                "robotwin.generated_scene_package.v1",
                name="package_manifest",
                sha256=SHA_C,
            ),
        }

    return build

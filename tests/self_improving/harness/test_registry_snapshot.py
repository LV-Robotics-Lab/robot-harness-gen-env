"""Public contract tests for frozen exact qualified-Skill snapshots."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)


def _ref(digit: str, schema_version: str, *, media_type: str = "application/json") -> ArtifactRef:
    digest = digit * 64
    return ArtifactRef(
        name=f"{schema_version}.json",
        uri=f"artifact://sha256/{digest}",
        media_type=media_type,
        sha256=digest,
        bytes=1,
        schema_version=schema_version,
    )


def _entry(**changes) -> dict[str, object]:
    values: dict[str, object] = {
        "skill_ref": "text2env.compile@1.0.0",
        "mcp_tool_name": "text2env_compile_v1_0_0",
        "descriptor_ref": _ref("a", "harness.skill_descriptor.v1"),
        "qualification_ref": _ref("b", "harness.skill_qualification.v1"),
        "qualification_report_ref": _ref(
            "c",
            "harness.skill_qualification_report.v1",
        ),
    }
    values.update(changes)
    return values


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"descriptor_ref": _ref("a", "harness.other.v1")},
            "supported Skill descriptor schema",
        ),
        (
            {
                "descriptor_ref": _ref("a", "harness.skill_descriptor.v1").model_copy(
                    update={"uri": "artifact://sha256/" + "f" * 64}
                )
            },
            "content-addressed artifact URI",
        ),
        (
            {
                "qualification_ref": _ref(
                    "b",
                    "harness.skill_qualification.v1",
                    media_type="text/plain",
                )
            },
            "media_type='application/json'",
        ),
        (
            {"qualification_ref": _ref("b", "harness.other.v1")},
            "schema_version=harness.skill_qualification.v1",
        ),
        (
            {"qualification_report_ref": _ref("c", "harness.other.v1")},
            "schema_version=harness.skill_qualification_report.v1",
        ),
    ],
)
def test_qualified_skill_snapshot_rejects_unbound_artifact_refs(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        QualifiedSkillSnapshot.model_validate(_entry(**changes))


@pytest.mark.parametrize(
    ("qualified_skills", "message"),
    [
        (
            (
                _entry(
                    skill_ref="video2env.compile@1.0.0",
                    mcp_tool_name="video2env_compile_v1_0_0",
                    descriptor_ref=_ref("d", "harness.skill_descriptor.v1"),
                    qualification_ref=_ref("e", "harness.skill_qualification.v1"),
                ),
                _entry(),
            ),
            "sorted and unique by mcp_tool_name",
        ),
        (
            (
                _entry(),
                _entry(
                    skill_ref="video2env.compile@1.0.0",
                    descriptor_ref=_ref("d", "harness.skill_descriptor.v1"),
                    qualification_ref=_ref("e", "harness.skill_qualification.v1"),
                ),
            ),
            "sorted and unique by mcp_tool_name",
        ),
        (
            (
                _entry(),
                _entry(
                    mcp_tool_name="video2env_compile_v1_0_0",
                    descriptor_ref=_ref("d", "harness.skill_descriptor.v1"),
                    qualification_ref=_ref("e", "harness.skill_qualification.v1"),
                ),
            ),
            "skill_ref values must be unique",
        ),
    ],
)
def test_registry_snapshot_rejects_ambiguous_tool_catalogs(
    qualified_skills: tuple[dict[str, object], ...],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        RegistrySnapshot.model_validate(
            {
                "schema_version": "harness.registry_snapshot.v1",
                "qualified_skills": qualified_skills,
            }
        )


def test_registry_snapshot_can_honestly_expose_an_empty_qualified_catalog() -> None:
    snapshot = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=(),
    )

    assert snapshot.qualified_skills == ()

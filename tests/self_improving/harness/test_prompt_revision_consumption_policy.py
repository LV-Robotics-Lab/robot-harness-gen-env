"""Branch-complete checks for the prompt-revision consumption authority seam."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import anyio
import pytest
from mcp import Client

from self_improving.harness.prompt_revision_consumption import (
    verify_prompt_revision_compile_consumption,
)
from self_improving.harness.schemas.compile_attempt import InitialCompileAttemptV1
from self_improving.harness.schemas.golden_control import NoPromptFallback
from self_improving.harness.schemas.workflow import RunSnapshot
from self_improving.harness.schemas.x2env import (
    Image2EnvCompileV2Input,
    Video2EnvCompileV2Input,
)
from tests.self_improving.harness import test_prompt_revision_compile_consumption as base
from tests.self_improving.harness import test_x2env_contracts as x2env_base


def _prepared_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    live = base._live_case(tmp_path, monkeypatch)

    async def prepare():
        async with Client(live.server, raise_exceptions=True, cache=None) as client:
            prompt_snapshot = await base._submit_prompt_revision(client, live.case)
            parameters, prompt = base._compile_parameters(live.case, tmp_path, prompt_snapshot)
            snapshot = RunSnapshot.model_validate(await base._workflow_json(client, live.case))
            return (
                snapshot.receipt_head,
                prompt,
                snapshot,
                Image2EnvCompileV2Input.model_validate(parameters),
            )

    return anyio.run(prepare)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("workflow_run_id", UUID("71000000-0000-4000-8000-000000000099"), "authority"),
        ("principal_id", "foreign-principal", "authority"),
        ("committed_revision", 99, "authority"),
        ("resulting_state_sha256", "0" * 64, "authority"),
    ),
)
def test_prompt_revision_authority_drift_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    revision_ref, revision, snapshot, parameters = _prepared_case(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match=message):
        verify_prompt_revision_compile_consumption(
            prompt_revision_ref=revision_ref,
            prompt_revision=revision.model_copy(update={field: value}),
            snapshot=snapshot,
            skill_ref=base._COMPILE_SKILL,
            parameters=parameters,
        )


def test_prompt_revision_state_ref_drift_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision_ref, revision, snapshot, parameters = _prepared_case(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="authority"):
        verify_prompt_revision_compile_consumption(
            prompt_revision_ref=revision_ref,
            prompt_revision=revision.model_copy(update={"resulting_state": live_ref(parameters)}),
            snapshot=snapshot,
            skill_ref=base._COMPILE_SKILL,
            parameters=parameters,
        )


def live_ref(parameters: Image2EnvCompileV2Input):
    """Return a model-valid, intentionally unrelated ArtifactRef already present in the input."""

    return parameters.interpretation_proposal


@pytest.mark.parametrize(
    "case",
    ("head", "target", "unsupported", "initial", "outer_package", "video_consumer"),
)
def test_non_consuming_compile_shapes_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    revision_ref, revision, snapshot, parameters = _prepared_case(tmp_path, monkeypatch)
    effective_ref = revision_ref
    effective_skill = base._COMPILE_SKILL
    effective_parameters = parameters
    if case == "head":
        effective_ref = parameters.interpretation_proposal
    elif case == "target":
        effective_skill = "video2env.compile@1.0.0"
    elif case == "unsupported":
        effective_parameters = InitialCompileAttemptV1(
            schema_version="harness.initial_compile_attempt.v1",
            kind="initial",
        )
    elif case == "initial":
        effective_parameters = parameters.model_copy(
            update={
                "attempt": InitialCompileAttemptV1(
                    schema_version="harness.initial_compile_attempt.v1",
                    kind="initial",
                )
            }
        )
    elif case == "outer_package":
        effective_parameters = parameters.model_copy(
            update={"predecessor_package": parameters.interpretation_proposal}
        )
    elif case == "video_consumer":
        values = x2env_base.compile_input("video2env")
        values.update(
            {
                "schema_version": "harness.video2env_compile_input.v2",
                "attempt": parameters.attempt.model_dump(mode="json"),
                "predecessor_package": parameters.predecessor_package.model_dump(mode="json"),
            }
        )
        effective_parameters = Video2EnvCompileV2Input.model_validate(values)

    with pytest.raises(ValueError):
        verify_prompt_revision_compile_consumption(
            prompt_revision_ref=effective_ref,
            prompt_revision=revision,
            snapshot=snapshot,
            skill_ref=effective_skill,
            parameters=effective_parameters,
        )


def test_no_fallback_and_each_selected_frame_identity_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision_ref, revision, snapshot, parameters = _prepared_case(tmp_path, monkeypatch)
    common = {
        "prompt_revision_ref": revision_ref,
        "snapshot": snapshot,
        "skill_ref": base._COMPILE_SKILL,
    }
    verify_prompt_revision_compile_consumption(
        **common,
        prompt_revision=revision.model_copy(update={"fallback": NoPromptFallback(kind="none")}),
        parameters=parameters,
    )

    unrelated = parameters.interpretation_proposal
    variants = (
        parameters.source_media.model_copy(update={"source": unrelated}),
        parameters.source_media.model_copy(update={"canonical_image": unrelated}),
        parameters.source_media.model_copy(update={"source_media_sha256": "0" * 64}),
    )
    for source_media in variants:
        with pytest.raises(ValueError, match="selected fallback frame"):
            verify_prompt_revision_compile_consumption(
                **common,
                prompt_revision=revision,
                parameters=parameters.model_copy(update={"source_media": source_media}),
            )

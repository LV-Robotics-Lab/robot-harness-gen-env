"""Shared modified-scene execution consumes real saved evidence, never fake physics."""

from datetime import datetime, timezone

from self_improving.harness.limited_image_preview_execution import LimitedImagePreviewExecution
from tests.self_improving.harness.test_genesis_scene_execution_v3 import saved_run  # noqa: F401
from tests.self_improving.harness.test_genesis_scene_replay import replay  # noqa: F401
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401


def test_saved_runner_replay_observe_validate_uses_same_modified_scene(saved_run):  # noqa: F811
    from self_improving.harness.genesis_scene_execution_v3 import publish_scene_run_evidence_v3

    store, request, replay_ref, directory, _ = saved_run

    class SavedRunner:
        execution_authority = False

        def run(self, request_ref):
            assert request_ref == request
            return publish_scene_run_evidence_v3(
                store, request_ref=request_ref, replay_ref=replay_ref, output_dir=directory
            )

    execution = LimitedImagePreviewExecution(store, SavedRunner())
    result = execution.replay(request)
    assert result.request == request
    observed = execution.observe(result, now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert observed.image.media_type == "image/png"
    validated = execution.validate(result)
    assert validated.report.status == "passed"
    assert not validated.execution_authority

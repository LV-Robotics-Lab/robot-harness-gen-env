"""Limited observation projects existing native authority, never arbitrary applications."""

import pytest

from self_improving.harness.limited_observation_registry_projection import (
    issue_limited_observation_registry_projection,
)


def test_projection_rejects_arbitrary_application_before_reading_admission(tmp_path):
    with pytest.raises(TypeError, match="exact registered capture application"):
        issue_limited_observation_registry_projection(
            application=object(), admission=object(), replay_authority=object()
        )

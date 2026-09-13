"""Public completion audit with explicit external model/runtime doubles, not qualification."""

import pytest

from self_improving.harness.x2env.completion import materialize_completion
from tests.self_improving.harness.x2env.test_completion import completed_fixture


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "authorization",
        "missing_authorization",
        "unknowns",
        "assets",
        "model",
        "v2_geometry",
        "v2_fixed",
        "v2_unknown_indices",
        "v2_transport_response",
        "v2_transport_missing",
        "v2_transport_path",
        "v2_event_list",
        "v2_event_null",
        "v2_event_item_null",
        "v2_argv_nonstring",
    ],
)
def test_completion_reaudits_generated_design_controller_authority(tmp_path, fault):
    store, snapshot = completed_fixture(
        tmp_path,
        grounding=True,
        structural_grounding=True,
        generated_grounding=True,
        grounding_fault=fault,
    )
    result = materialize_completion(snapshot, store, tmp_path / "delivery")
    if fault is None:
        assert result.status == "materialized", result
    else:
        assert result.status == "failed" and "grounding" in result.error_code, result
        if fault.startswith("v2_event_"):
            assert result.error_code == "completion_grounding_transport_event_invalid"
        elif fault == "v2_argv_nonstring":
            assert result.error_code == "completion_grounding_transport_unbound"

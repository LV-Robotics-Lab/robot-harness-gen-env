"""Native input providers reject configuration outside the exact public seam."""

import pytest


def test_provider_requires_exact_golden_authority():
    from self_improving.harness.golden_native_inputs import NativeImageWorkflowInputs

    with pytest.raises(TypeError, match="exact"):
        NativeImageWorkflowInputs(
            harness=object(), store=object(), execution_catalog=object(),
            compile_template_ref=None, approved_runtime_manifest=None,
        )

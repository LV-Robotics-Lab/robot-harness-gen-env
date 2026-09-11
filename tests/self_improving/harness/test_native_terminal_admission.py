"""Durable native terminal admission observes actual public deep reads."""

import pytest


def test_terminal_admission_rejects_arbitrary_application():
    from self_improving.harness.native_terminal_admission import admit_native_terminal

    with pytest.raises(TypeError, match="exact"):
        admit_native_terminal(application=object(), artifact_store=object(), run_id=None,
                              qualification_admission=None, child_authority=object())

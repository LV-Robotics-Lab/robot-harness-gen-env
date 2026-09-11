"""Golden-owned glue for sealed native terminal admission roots."""


def test_public_native_terminal_resolver_surface_exists() -> None:
    from self_improving.harness.golden_native_terminal_resolver import (
        NativeTerminalEvidenceRoots,
        create_native_terminal_resolver,
    )

    assert NativeTerminalEvidenceRoots is not None
    assert callable(create_native_terminal_resolver)

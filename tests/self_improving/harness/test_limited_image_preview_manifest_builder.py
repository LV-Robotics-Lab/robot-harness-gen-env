from pathlib import Path

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.limited_image_preview_manifest_builder import (
    build_limited_image_preview_manifest,
)


def test_manifest_builder_requires_exact_shared_authorities(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    journal = SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")

    with pytest.raises(TypeError, match="exact shared Golden and CAS authorities"):
        build_limited_image_preview_manifest(
            artifact_store=object(),
            workflow_store=journal,
            snapshot=object(),
            input_record=object(),
            lookup=object(),
            project_id="preview-test",
            timing_samples=(),
        )
    with pytest.raises(TypeError, match="exact shared Golden and CAS authorities"):
        build_limited_image_preview_manifest(
            artifact_store=store,
            workflow_store=object(),
            snapshot=object(),
            input_record=object(),
            lookup=object(),
            project_id="preview-test",
            timing_samples=(),
        )

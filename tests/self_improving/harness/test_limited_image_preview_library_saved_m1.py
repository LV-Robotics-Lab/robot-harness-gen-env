"""Public preview-library checks over the saved real M1 Genesis evidence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_asset_qualification_application import (
    GenesisAssetQualificationApplication,
)
from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
from self_improving.harness.limited_image_preview_library import (
    admit_limited_image_preview,
    create_limited_image_preview_library,
)
from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.genesis_scene_replay import GenesisSceneSpecV1
from self_improving.harness.schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileRequestV1,
)


@pytest.fixture(scope="module")
def saved_m1_preview():
    configured = os.environ.get("LIMITED_IMAGE_PREVIEW_M1_CONFIG")
    if not configured:
        pytest.skip("requires the isolated saved M1 limited-preview fixture")
    config = json.loads(Path(configured).read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    assets = GenesisAssetQualificationApplication(
        Path(config["asset_state_path"]),
        store,
        ArtifactRef.model_validate(config["asset_manifest"]),
    )
    compiler = RevisedImage2EnvGenesisCompiler(
        store,
        Image2EnvGenesisCompiler(store, assets),
    )
    manifest_ref = ArtifactRef.model_validate(config["manifest"])
    verified = admit_limited_image_preview(
        artifact_store=store,
        compiler=compiler,
        manifest=manifest_ref,
    )
    return config, store, compiler, verified


def test_saved_m1_source_envelope_and_raw_image_are_both_bound(saved_m1_preview) -> None:
    _, store, _, verified = saved_m1_preview
    manifest = verified.manifest
    scene = GenesisSceneSpecV1.model_validate_json(
        store.resolve(manifest.initial_stage.scene).path.read_bytes()
    )
    request = Image2EnvGenesisCompileRequestV1.model_validate_json(
        store.resolve(manifest.initial_stage.compile_request).path.read_bytes()
    )

    assert scene.source == request.source_scene
    assert request.compile_input.source_media.source == manifest.raw_image
    assert scene.source != manifest.raw_image


def test_materialized_saved_m1_preview_contains_the_observed_genesis_image(
    saved_m1_preview,
    tmp_path: Path,
) -> None:
    config, store, compiler, verified = saved_m1_preview
    library = create_limited_image_preview_library(
        artifact_store=store,
        compiler=compiler,
        root=tmp_path / "library",
        project_id=verified.manifest.project_id,
    )
    published = library.publish(verified)

    restored = library.materialize(published.preview_id, tmp_path / "restored")

    expected = ArtifactRef.model_validate(config["fresh_image"])
    observation = restored / "stages/initial/observation.png"
    assert observation.stat().st_size == expected.bytes
    assert hashlib.sha256(observation.read_bytes()).hexdigest() == expected.sha256

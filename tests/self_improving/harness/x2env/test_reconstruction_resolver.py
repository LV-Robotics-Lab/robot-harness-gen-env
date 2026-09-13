"""Real Store/Registry/normalizer; explicit model subprocess, generator and renderer doubles."""

import hashlib
import json

import pytest

from self_improving.harness.x2env.adapters.reconstruction import ReconstructionResult
from self_improving.harness.x2env.codex import CodexBackend
from tests.self_improving.harness.x2env.test_codex import executable
from tests.self_improving.harness.x2env.test_resolver import VisualDouble, inputs
from tests.self_improving.harness.x2env.test_web_resolver import preview_double


@pytest.mark.parametrize("fault", [None, "license", "geometry", "visual", "image", "color"])
def test_reconstruction_requires_authorized_actual_geometry_and_visual_pass(tmp_path, fault):
    from self_improving.harness.x2env.reconstruction_resolver import (
        ReconstructionImage,
        ReconstructionResolver,
    )

    store, registry, _, scene, image = inputs(tmp_path)
    if fault == "color":
        payload = json.loads(store.read_artifact(scene))
        payload["entities"][0]["color"] = "pink"
        scene = store.write_artifact(json.dumps(payload).encode(), "application/json")
    answer = {
        "box_xyxy": [0.0, 0.0, 2.0, 2.0],
        "point_coords": [],
        "point_labels": [],
        "dimensions_m": [0.1, 0.1, 0.1],
        "mass_kg": 0.1,
        "friction": 0.6,
        "reason": "test double",
    }
    if fault == "color":
        answer.update(base_color=[1.0, 0.5, 0.75, 1.0], declared_color="pink")
    path = executable(tmp_path, answer)
    backend = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    )
    backend.assess_asset_candidates = VisualDouble(
        store, "mismatch" if fault == "visual" else "match"
    ).assess_asset_candidates

    def image_port(entity):
        if fault == "image":
            return None
        ref = store.write_artifact(
            json.dumps(
                {
                    "scene_ir": scene.model_dump(mode="json"),
                    "entity_id": entity.id,
                    "image_ref": image.model_dump(mode="json"),
                    "selection_basis": "explicit_test_input",
                }
            ).encode(),
            "application/json",
        )
        return ReconstructionImage(image=image, provenance=ref)

    class Adapter:
        def reconstruct(self, ref, **kwargs):
            assert ref == image and kwargs["seed"] == 17 and kwargs["timeout"] < 600
            out = kwargs["output_root"]
            out.mkdir()
            raw = (tmp_path / "box.glb").read_bytes()
            (out / "geometry.glb").write_bytes(raw)
            geom = store.write_artifact(raw, "model/gltf-binary")
            auth = store.write_artifact(
                json.dumps(
                    {
                        "input_sha256": "0" * 64 if fault == "license" else image.sha256,
                        "allow_derivative": True,
                        "output_spdx": "CC-BY-4.0",
                        "source_url": "https://example.org/fixture",
                        "attribution": "Fixture Author",
                    }
                ).encode(),
                "application/json",
            )
            if fault == "geometry":
                (out / "geometry.glb").write_bytes(b"changed")
            return ReconstructionResult(
                status="succeeded",
                geometry=geom,
                source_path=str(out / "geometry.glb"),
                receipt=auth,
                derivation_authorization=auth,
                registration_allowed=True,
            )

    def preview(version, *, timeout):
        assert timeout < 600
        return preview_double(store, image)(version)

    result = ReconstructionResolver(
        store, registry, backend, Adapter(), preview, image_port, seed=17
    ).resolve(
        scene,
        allowed_sources=("reconstruction",),
        allow_cousin=False,
        output_root=tmp_path / "resolve",
    )
    if fault and fault != "color":
        assert result.status == "blocked" and not result.resolved.assets
        receipt = json.loads(store.read_artifact(result.receipt))
        if fault != "image":
            assert receipt["records"][0]["generation"]["geometry"]
    else:
        assert result.status == "succeeded"
        version = registry.inspect(result.resolved.assets[0].version_sha256)
        assert (
            version.source.kind == "reconstruction"
            and version.license.attribution == "Fixture Author"
        )
        if fault == "color":
            normalization = json.loads(store.read_artifact(version.normalization_report))
            assert normalization["color_override_rgba"] == [1.0, 0.5, 0.75, 1.0]

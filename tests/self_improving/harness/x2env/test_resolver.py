"""Local resolver uses real registry; preview/model ports below are explicit unit doubles."""

import json
from io import BytesIO

import pytest
import trimesh
from PIL import Image

from self_improving.harness.x2env.asset_advisory import AssetVisualAssessment, VisualVerdict
from self_improving.harness.x2env.asset_preview import AssetPreviewProof
from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store


def inputs(tmp_path, *, extra_entity=False, dimensions=None):
    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)
    source = tmp_path / "box.glb"
    source.write_bytes(trimesh.creation.box().export(file_type="glb"))
    root = tmp_path / "normalized"
    report = normalize_mesh(
        source, root, dimensions_m=(0.1, 0.1, 0.1), up_axis="Z", mass_kg=0.1, friction=0.6
    )
    evidence = store.write_artifact(b"explicit test fixture", "text/plain")
    version = registry.register(
        "box",
        "box",
        root,
        report["entrypoint"],
        files=tuple(row["path"] for row in report["files"]),
        normalization_report=store.write_artifact(json.dumps(report).encode(), "application/json"),
        license=AssetLicense(
            spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=evidence
        ),
        source=AssetSource(kind="web", provider="fixture", source_ref="fixed", evidence=evidence),
        receipt=evidence,
    )
    provenance = {
        field: [{"source": "text", "input_sha256": "a" * 64, "kind": "explicit"}]
        for field in ("category", "color", "dimensions", "material", "pose", "articulation_state")
    }
    entities = [
        {
            "id": category,
            "category": category,
            "role": "foreground",
            "color": None,
            "material": None,
            "dimensions": dimensions,
            "articulation_state": None,
            "pose": {"frame": "world", "position": [0, 0, 0], "yaw_degrees": 0},
            "provenance": provenance,
        }
        for category in (["box", "mouse"] if extra_entity else ["box"])
    ]
    scene = store.write_artifact(
        json.dumps(
            {"revision": 0, "input_sha256": "a" * 64, "entities": entities, "relations": []}
        ).encode(),
        "application/json",
    )
    image = BytesIO()
    Image.new("RGB", (3, 3), "red").save(image, format="PNG")
    preview = store.write_artifact(image.getvalue(), "image/png")
    return store, registry, version, scene, preview


class VisualDouble:
    def __init__(self, store, verdict="match"):
        self.store, self.verdict, self.calls = store, verdict, []

    def assess_asset_candidates(self, candidates, **kwargs):
        self.calls.extend(candidates)
        ref = self.store.write_artifact(b"explicit advisory double", "text/plain")
        return AssetVisualAssessment(
            status="completed",
            verdicts=tuple(
                VisualVerdict(
                    candidate_id=c.candidate_id, preview=c.preview, verdict=self.verdict, detail=ref
                )
                for c in candidates
            ),
            receipt=ref,
            evidence=(ref,),
        )


def test_local_preview_receives_remaining_resolver_deadline(tmp_path):
    from self_improving.harness.x2env.resolver import LocalAssetResolver

    store, registry, version, scene, image = inputs(tmp_path)
    budgets = []

    def render(candidate, *, timeout):
        budgets.append(timeout)
        return preview_proof(store, candidate, image)

    result = LocalAssetResolver(store, registry, VisualDouble(store), render).resolve(
        scene,
        allowed_sources=("local",),
        allow_cousin=False,
        output_root=tmp_path / "resolve",
        timeout=30,
    )
    assert result.status == "succeeded"
    assert len(budgets) == 1 and 1 <= budgets[0] <= 30


def test_local_reuse_preserves_origin_and_binds_known_version_preview(tmp_path):
    from self_improving.harness.x2env.resolver import LocalAssetResolver

    store, registry, version, scene, preview = inputs(tmp_path)
    backend = VisualDouble(store)
    proof = preview_proof(store, version, preview)
    result = LocalAssetResolver(store, registry, backend, lambda v, **kwargs: proof).resolve(
        scene, allowed_sources=("local",), allow_cousin=False, output_root=tmp_path / "resolve"
    )
    assert result.status == "succeeded"
    assert result.resolved.assets[0].version_sha256 == version.version_sha256
    assert result.resolved.assets[0].acquisition_source == "local"
    assert registry.inspect(version.version_sha256).source.kind == "web"
    assert len(backend.calls) == 1
    assert backend.calls[0].candidate_id == version.version_sha256
    assert json.loads(store.read_artifact(result.receipt))["candidates"][0][
        "preview_proof"
    ] == proof.model_dump(mode="json")


def preview_proof(store, version, image):
    receipt = store.write_artifact(
        json.dumps(
            {
                "scope": "asset_preview_scope",
                "status": "passed",
                "version_sha256": version.version_sha256,
                "outputs": {"explicit-test.png": image.model_dump(mode="json")},
                "test_double": True,
            }
        ).encode(),
        "application/json",
    )
    return AssetPreviewProof(
        status="passed", version_sha256=version.version_sha256, image=image, receipt=receipt
    )


@pytest.mark.parametrize("fault", ["unbound_version", "bare_image", "missing_receipt"])
def test_unbound_render_proof_never_reaches_visual_backend(tmp_path, fault):
    from self_improving.harness.x2env.resolver import LocalAssetResolver

    store, registry, version, scene, image = inputs(tmp_path)
    proof = preview_proof(store, version, image)
    if fault == "unbound_version":
        proof = proof.model_copy(update={"version_sha256": "0" * 64})
    elif fault == "bare_image":
        proof = image
    else:
        proof = proof.model_copy(update={"receipt": image.model_copy(update={"sha256": "0" * 64})})
    backend = VisualDouble(store)
    result = LocalAssetResolver(store, registry, backend, lambda v, **kwargs: proof).resolve(
        scene, allowed_sources=("local",), allow_cousin=False, output_root=tmp_path / "resolve"
    )
    assert result.status == "blocked" and not result.resolved.assets
    assert not backend.calls


@pytest.mark.parametrize(
    "fault", ["dimensions", "preview", "visual", "unknown_version", "short_budget"]
)
def test_unsuitable_candidate_is_not_selected_or_retried(tmp_path, fault):
    from self_improving.harness.x2env.resolver import LocalAssetResolver

    store, registry, version, scene, preview = inputs(
        tmp_path, dimensions=[0.2, 0.1, 0.1] if fault == "dimensions" else None
    )

    class WrongVersion(VisualDouble):
        def assess_asset_candidates(self, candidates, **kwargs):
            result = super().assess_asset_candidates(candidates, **kwargs)
            return result.model_copy(
                update={
                    "verdicts": tuple(
                        v.model_copy(update={"candidate_id": "unknown"}) for v in result.verdicts
                    )
                }
            )

    backend = (
        WrongVersion(store)
        if fault == "unknown_version"
        else VisualDouble(store, "mismatch" if fault == "visual" else "match")
    )
    result = LocalAssetResolver(
        store,
        registry,
        backend,
        None if fault == "preview" else lambda v, **kwargs: preview_proof(store, v, preview),
    ).resolve(
        scene,
        allowed_sources=("local", "web"),
        allow_cousin=True,
        output_root=tmp_path / "resolve",
        timeout=1 if fault == "short_budget" else 600,
    )
    assert result.status == "blocked"
    assert result.resolved.assets == ()
    assert len(backend.calls) == (1 if fault in {"visual", "unknown_version"} else 0)
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["cousin_status"] == "not_implemented"
    assert receipt["candidates"][0]["version_sha256"] == version.version_sha256
    assert result.next_source == (None if fault == "short_budget" else "web")
    if fault == "visual":
        assert receipt["candidates"][0]["advisory"]["verdicts"][0]["detail"]


def test_later_entity_miss_retains_partial_resolutions_and_next_source(tmp_path):
    from self_improving.harness.x2env.resolver import LocalAssetResolver

    store, registry, version, scene, preview = inputs(tmp_path, extra_entity=True)
    result = LocalAssetResolver(
        store, registry, VisualDouble(store), lambda v, **kwargs: preview_proof(store, v, preview)
    ).resolve(
        scene,
        allowed_sources=("local", "reconstruction"),
        allow_cousin=False,
        output_root=tmp_path / "resolve",
    )
    assert result.status == "blocked"
    assert [asset.entity_id for asset in result.resolved.assets] == ["box"]
    assert result.next_source == "reconstruction"
    assert json.loads(store.read_artifact(result.receipt))["unresolved_entities"] == ["mouse"]


def test_candidate_budget_is_eight_once_each(tmp_path):
    from self_improving.harness.x2env.resolver import LocalAssetResolver

    store, registry, version, scene, preview = inputs(tmp_path)
    for index in range(9):
        registry.register(
            "box",
            "box",
            tmp_path / "normalized",
            version.entrypoint,
            files=tuple(member.path for member in version.files),
            normalization_report=version.normalization_report,
            license=version.license,
            source=version.source,
            receipt=store.write_artifact(f"fixture variant {index}".encode(), "text/plain"),
        )
    backend = VisualDouble(store, "mismatch")
    result = LocalAssetResolver(
        store, registry, backend, lambda v, **kwargs: preview_proof(store, v, preview)
    ).resolve(
        scene, allowed_sources=("local",), allow_cousin=False, output_root=tmp_path / "resolve"
    )
    assert result.status == "blocked"
    assert len(backend.calls) == 8
    assert len({c.candidate_id for c in backend.calls}) == 8

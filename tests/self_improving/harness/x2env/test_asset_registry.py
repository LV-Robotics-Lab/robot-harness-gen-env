"""Immutable registration and reuse through the single real Store."""

import hashlib
import json

import pytest

from self_improving.harness.x2env.store import Store


def test_material_change_is_not_a_new_geometry_digest(tmp_path):
    import trimesh

    from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
    from self_improving.harness.x2env.normalization import normalize_mesh

    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)
    source = tmp_path / "source.glb"
    trimesh.creation.box().export(source)
    proof = store.write_artifact(b"fixture source", "text/plain")
    versions = []
    for index, (color, scale) in enumerate(
        [((1.0, 0.0, 0.0, 1.0), 1.0), ((0.0, 0.0, 1.0, 1.0), 1.0), ((0.0, 0.0, 1.0, 1.0), 2.0)]
    ):
        root = tmp_path / f"variant-{index}"
        report = normalize_mesh(
            source,
            root,
            dimensions_m=(scale, scale, scale),
            up_axis="Z",
            mass_kg=0.1,
            friction=0.4,
            color_rgba=color,
        )
        versions.append(
            registry.register(
                "box",
                "box",
                root,
                "asset.urdf",
                files=tuple(item["path"] for item in report["files"]),
                normalization_report=store.write_artifact(
                    json.dumps(report).encode(), "application/json"
                ),
                license=AssetLicense(
                    spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=proof
                ),
                source=AssetSource(
                    kind="local", provider="fixture", source_ref="test", evidence=proof
                ),
                receipt=proof,
                parent_version=versions[0].version_sha256 if versions else None,
            )
        )
    assert versions[0].version_sha256 != versions[1].version_sha256
    assert versions[0].geometry_sha256 == versions[1].geometry_sha256
    assert versions[1].geometry_sha256 != versions[2].geometry_sha256


def test_registry_rejects_copied_source_receipt_with_missing_transitive_evidence(tmp_path):
    from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
    from self_improving.harness.x2env.contracts import ArtifactRef

    store = Store(tmp_path / "state")
    root, files, report = prepared(tmp_path, store)
    missing = ArtifactRef(sha256="b" * 64, size_bytes=100, media_type="application/json")
    receipt = store.write_artifact(
        json.dumps({"source": missing.model_dump()}).encode(), "application/json"
    )
    license = store.write_artifact(b"fixture license", "text/plain")
    with pytest.raises(FileNotFoundError):
        AssetRegistry(store).register(
            "fixture",
            "mouse",
            root,
            "model.urdf",
            files=files,
            normalization_report=report,
            license=AssetLicense(
                spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=license
            ),
            source=AssetSource(
                kind="web", provider="fixture", source_ref="fixed", evidence=receipt
            ),
            receipt=license,
        )
    assert not store.asset_versions("mouse")


def prepared(tmp_path, store, mass="1"):
    root = tmp_path / f"normalized-{mass}"
    root.mkdir()
    (root / "visual.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    (root / "model.urdf").write_text(
        '<robot name="object"><link name="body"><inertial>'
        f'<mass value="{mass}"/></inertial><visual><geometry><mesh filename="visual.obj"/>'
        "</geometry></visual></link></robot>"
    )
    files = ("model.urdf", "visual.obj")
    report = {
        "schema_version": "x2env.normalization.v1",
        "status": "passed",
        "entrypoint": "model.urdf",
        "files": [
            {
                "path": name,
                "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
                "size_bytes": (root / name).stat().st_size,
            }
            for name in files
        ],
    }
    return root, files, store.write_artifact(json.dumps(report).encode(), "application/json")


def test_registered_version_survives_restart_and_preserves_web_origin(tmp_path):
    from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource

    store = Store(tmp_path / "state")
    root, files, report = prepared(tmp_path, store)
    evidence = store.write_artifact(b"fixture source license", "text/plain")
    registry = AssetRegistry(store)
    args = dict(
        asset_id="fixture",
        category="mouse",
        normalized_root=root,
        entrypoint="model.urdf",
        files=files,
        normalization_report=report,
        license=AssetLicense(
            spdx="CC-BY-4.0",
            attribution="Fixture author",
            source_url="https://example.org/license",
            evidence=evidence,
        ),
        source=AssetSource(
            kind="web", provider="fixture", source_ref="fixed-source", evidence=evidence
        ),
        receipt=evidence,
    )
    version = registry.register(**args)
    assert registry.register(**args) == version
    reopened = AssetRegistry(Store(tmp_path / "state"))
    assert reopened.inspect(version.version_sha256) == version
    assert reopened.find("mouse") == (version,)
    assert version.source.kind == "web"
    assert version.physical_evaluated is False and version.sim_ready is False
    changed_root, changed_files, changed_report = prepared(tmp_path, store, "2")
    revised = registry.register(
        **{
            **args,
            "normalized_root": changed_root,
            "files": changed_files,
            "normalization_report": changed_report,
        },
        parent_version=version.version_sha256,
    )
    assert revised.version_sha256 != version.version_sha256
    assert revised.geometry_sha256 == version.geometry_sha256
    assert revised.parent_version == version.version_sha256
    assert registry.inspect(version.version_sha256) == version
    member = version.files[0].artifact
    (store.cas / member.sha256[:2] / member.sha256).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        registry.inspect(version.version_sha256)


@pytest.mark.parametrize(
    "fault",
    [
        "escape",
        "symlink",
        "missing_dependency",
        "mesh_dependency",
        "report_mismatch",
        "wrong_parent",
        "unknown_license",
        "missing_author",
    ],
)
def test_registration_rejects_untrusted_assets(tmp_path, fault):
    from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource

    store = Store(tmp_path / "state")
    root, files, report = prepared(tmp_path, store)
    evidence = store.write_artifact(b"fixture license", "text/plain")
    if fault == "escape":
        files = (*files, "../outside.obj")
    elif fault == "symlink":
        (root / "linked.obj").symlink_to(root / "visual.obj")
        files = (*files, "linked.obj")
    elif fault == "missing_dependency":
        files = ("model.urdf",)
    elif fault == "report_mismatch":
        (root / "visual.obj").write_text("changed")
    elif fault == "mesh_dependency":
        mesh = root / "visual.obj"
        mesh.write_text("mtllib absent.mtl\n" + mesh.read_text())
        doc = json.loads(store.read_artifact(report))
        for member in doc["files"]:
            if member["path"] == "visual.obj":
                member["sha256"] = hashlib.sha256(mesh.read_bytes()).hexdigest()
                member["size_bytes"] = mesh.stat().st_size
        report = store.write_artifact(json.dumps(doc).encode(), "application/json")
    with pytest.raises((ValueError, KeyError)):
        license = AssetLicense(
            spdx="unknown" if fault == "unknown_license" else "CC-BY-4.0",
            attribution=None if fault == "missing_author" else "Fixture author",
            source_url="https://example.org/license",
            evidence=evidence,
        )
        AssetRegistry(store).register(
            "fixture",
            "mouse",
            root,
            "model.urdf",
            files=files,
            normalization_report=report,
            license=license,
            source=AssetSource(
                kind="web", provider="fixture", source_ref="fixed", evidence=evidence
            ),
            receipt=evidence,
            parent_version="f" * 64 if fault == "wrong_parent" else None,
        )
    assert AssetRegistry(store).find("mouse") == ()


def test_real_normalizer_report_registers_complete_geometry_closure(tmp_path):
    import trimesh

    from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
    from self_improving.harness.x2env.normalization import normalize_mesh

    source_path = tmp_path / "fixture.glb"
    source_path.write_bytes(trimesh.creation.box().export(file_type="glb"))
    normalized = tmp_path / "normalized"
    report = normalize_mesh(
        source_path,
        normalized,
        dimensions_m=(0.1, 0.1, 0.1),
        up_axis="Y",
        mass_kg=0.1,
        friction=0.6,
    )
    store = Store(tmp_path / "state")
    evidence = store.write_artifact(b"explicit test fixture license", "text/plain")
    version = AssetRegistry(store).register(
        "fixture",
        "box",
        normalized,
        report["entrypoint"],
        files=tuple(row["path"] for row in report["files"]),
        normalization_report=store.write_artifact(json.dumps(report).encode(), "application/json"),
        license=AssetLicense(
            spdx="CC0-1.0",
            attribution="test fixture",
            source_url="https://example.org/fixture",
            evidence=evidence,
        ),
        source=AssetSource(
            kind="local", provider="test-fixture", source_ref="fixture", evidence=evidence
        ),
        receipt=evidence,
    )
    assert len(version.files) == 4
    assert AssetRegistry(store).inspect(version.version_sha256) == version

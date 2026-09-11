"""Immutable layout derivation over Gujie's measured rigid tabletop adapter.

These functions produce/verify geometry, not asset qualification or runtime authority.
Gujie eb0b710 source semantics remain in the unchanged public adapter.

Layout yaw is absolute world yaw. Sources must be within one degree of world
+Z; a left delta-Z rotation preserves their original roll/pitch and tilt rather
than flattening a measured near-upright pose. Table geometry belongs to the
native scene, while the immutable Gujie snapshot contains only the object.
"""

import copy
import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .artifacts import LocalArtifactStore
from .gujie_scene_adapter import adapt_gujie_tabletop_scene, read_gujie_tabletop_scene
from .schemas.common import ArtifactRef
from .schemas.native_prompt_revision import TabletopLayoutRevisionV1


def read_revision_artifact(store, ref):
    """Read exact CAS bytes while refusing symlink members and locator aliases."""
    if type(store) is not LocalArtifactStore or type(ref) is not ArtifactRef:
        raise TypeError("revision evidence requires exact local CAS and ArtifactRef")
    if ref.uri != "artifact://sha256/" + ref.sha256:
        raise ValueError("revision evidence requires exact CAS locators")
    path = store.root / "sha256" / ref.sha256[:2] / ref.sha256
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("revision evidence cannot use symlinks")
    return store.resolve(ref).path.read_bytes()


def _closure(store, value, seen):
    if isinstance(value, dict):
        if {"name", "uri", "sha256", "bytes", "media_type", "schema_version"} <= value.keys():
            ref = ArtifactRef.model_validate(value)
            payload = read_revision_artifact(store, ref)
            if ref.sha256 not in seen:
                seen.add(ref.sha256)
                if ref.media_type == "application/json":
                    _closure(store, json.loads(payload), seen)
        else:
            for item in value.values():
                _closure(store, item, seen)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _closure(store, item, seen)


def _expected(store, layout, predecessor_scene):
    if type(layout) is not TabletopLayoutRevisionV1:
        raise TypeError("layout requires the exact revision contract")
    layout = TabletopLayoutRevisionV1.model_validate_json(layout.model_dump_json())
    _closure(store, predecessor_scene.model_dump(mode="json"), set())
    prior = read_gujie_tabletop_scene(store, predecessor_scene)
    if layout.original_source != prior.source:
        raise ValueError("layout source differs from the native predecessor")
    w, x, y, z = prior.orientation_wxyz
    if 1.0 - 2.0 * (x * x + y * y) < math.cos(math.radians(1.0)):
        raise ValueError("revised layout requires an upright original source")
    source = json.loads(read_revision_artifact(store, prior.source))
    if source["metadata"]["name"] != layout.source_object_id:
        raise ValueError("layout object differs from original Gujie source")
    # Only these two existing fields are changed. Every asset/physics/ground
    # declaration is preserved byte-for-byte in the canonical source structure.
    derived = copy.deepcopy(source)
    pose = derived["scene"]["state"]["registry"]["object_registry"][layout.source_object_id][
        "root_link"
    ]
    pose["pos"] = [*layout.position_xy_m, pose["pos"][2]]
    original_yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    half_delta = (layout.yaw_rad - original_yaw) / 2.0
    c, s = math.cos(half_delta), math.sin(half_delta)
    pose["ori"] = [c * x - s * y, c * y + s * x, c * z + s * w, c * w - s * z]
    return prior, derived


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class DerivedTabletopScene:
    original_source: ArtifactRef
    derived_source: ArtifactRef
    scene: ArtifactRef


def derive_revised_tabletop_scene(store, *, layout, predecessor_scene):
    prior, source = _expected(store, layout, predecessor_scene)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "derived-gujie-source.json"
        path.write_bytes(_bytes(source))
        ref = store.put_file(
            path, name=path.name, media_type="application/json", schema_version=None
        )
    scene = adapt_gujie_tabletop_scene(
        store,
        source_ref=ref,
        representation_ref=prior.representation,
        table_center_m=layout.table_center_m,
        table_size_m=layout.table_size_m,
        table_friction=prior.table_friction,
    )
    return DerivedTabletopScene(prior.source, ref, scene)


def verify_revised_tabletop_scene(store, *, layout, predecessor_scene, derived):
    """Pure read: recompute source edits and all-vertex scene geometry, without writes."""
    if type(derived) is not DerivedTabletopScene:
        raise TypeError("derived geometry requires its exact reference record")
    prior, expected = _expected(store, layout, predecessor_scene)
    if derived.original_source != prior.source or read_revision_artifact(
        store, derived.derived_source
    ) != _bytes(expected):
        raise ValueError("derived source changed outside the exact allowed layout edit")
    _closure(store, derived.scene.model_dump(mode="json"), set())
    scene = read_gujie_tabletop_scene(store, derived.scene)
    if (
        scene.source,
        scene.representation,
        scene.table_center_m,
        scene.table_size_m,
        scene.table_friction,
    ) != (
        derived.derived_source,
        prior.representation,
        layout.table_center_m,
        layout.table_size_m,
        prior.table_friction,
    ):
        raise ValueError("derived scene differs from the revised source and preserved physics")
    return scene

#!/usr/bin/env python3
"""Generate reusable RoboTwin scene modules from TabletopPlacementSpec."""

from __future__ import annotations

import argparse
import json
import pprint
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene.schemas import read_json, write_json


def slugify_name(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return "_".join(words) or "generated_scene"


def scene_module_source(spec: dict[str, Any], *, source_placement: str) -> str:
    scene_name = slugify_name(spec.get("placement_name", "generated_scene")).replace("_final_static_v0", "")
    spec_literal = pprint.pformat(spec, indent=2, sort_dicts=False, width=120)
    source_placement = source_placement.replace("\\", "/")
    source_placement_literal = json.dumps(source_placement)
    return f'''#!/usr/bin/env python3
"""Generated RoboTwin tabletop scene: {scene_name}.

Source placement: {source_placement}
Generated at: {date.today().isoformat()}
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SCENE_NAME = "{scene_name}"
SOURCE_PLACEMENT = {source_placement_literal}
PLACEMENT_SPEC: dict[str, Any] = {spec_literal}


def load_scene(task: Any, placement_spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load this generated tabletop scene into a RoboTwin task instance."""

    import sapien.core as sapien
    from envs.utils import create_actor, create_sapien_urdf_obj

    spec = deepcopy(placement_spec or PLACEMENT_SPEC)
    loaded: dict[str, Any] = {{}}
    table_z = 0.741 + getattr(task, "table_z_bias", 0)

    for obj in spec["objects"]:
        pose_data = obj["pose"]
        xyz = list(pose_data["xyz"])
        if pose_data.get("z_policy") == "snap_to_tabletop_on_load":
            xyz[2] = table_z

        qpos = list(pose_data.get("qpos", [1, 0, 0, 0]))
        if obj["asset_id"] == "003_plate" and qpos == [1, 0, 0, 0]:
            qpos = [0.5, 0.5, 0.5, 0.5]

        metadata = obj.get("asset_metadata", {{}})
        defaults = metadata.get("placement_defaults", {{}})
        loader = defaults.get("loader")
        asset_type = metadata.get("asset_type", "rigid")
        if loader == "sapien_urdf" or asset_type == "articulated":
            actor = create_sapien_urdf_obj(
                task,
                pose=sapien.Pose(xyz, qpos),
                modelname=obj["asset_id"],
                modelid=obj.get("model_id", 0),
                fix_root_link=defaults.get("fix_root_link", obj.get("physical", {{}}).get("is_static", False)),
            )
            if "articulation_qpos" in defaults:
                actor.set_qpos(defaults["articulation_qpos"])
        else:
            actor = create_actor(
                task,
                pose=sapien.Pose(xyz, qpos),
                modelname=obj["asset_id"],
                scale=metadata.get("scale", (1, 1, 1)) or (1, 1, 1),
                model_id=obj.get("model_id", 0),
                convex=True,
                is_static=obj.get("physical", {{}}).get("is_static", False),
            )
        if actor is None:
            raise RuntimeError(f"Failed to load asset {{obj['asset_id']}}")
        actor.set_name(obj["id"])
        loaded[obj["id"]] = actor

    task.generated_scene_objects = loaded
    return loaded
'''


def generate_scene_module(*, placement_path: Path, out_path: Path) -> dict[str, Any]:
    spec = read_json(placement_path)
    source = scene_module_source(spec, source_placement=str(placement_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(source, encoding="utf-8")
    return {
        "schema_version": "robotwin.generated_scene_module.v0",
        "status": "pass",
        "placement": str(placement_path),
        "scene_module": str(out_path),
        "scene_name": slugify_name(spec.get("placement_name", out_path.stem)).replace("_final_static_v0", ""),
        "generated_at": date.today().isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a RoboTwin scene module from a placement spec.")
    parser.add_argument("--placement", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()

    report = generate_scene_module(placement_path=Path(args.placement), out_path=Path(args.out))
    if args.report:
        write_json(Path(args.report), report)
    print(f"PASS {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

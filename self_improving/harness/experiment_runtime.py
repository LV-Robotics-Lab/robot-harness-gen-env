"""Experimental exact runtime adapters; the enclosing Harness owns lifecycle authority."""

import hashlib
import json
import mimetypes
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .genesis_scene_replay import evaluate_scene_replay
from .portable_genesis_package import export_package, run_package
from .schemas.common import ArtifactRef
from .schemas.genesis_scene_replay import GenesisSceneSpecV1


def _ref(value):
    return ArtifactRef.model_validate(value)


def _json(store, reference):
    return json.loads(store.resolve(_ref(reference)).path.read_bytes())


def _put(store, value, name, schema):
    with tempfile.TemporaryDirectory(prefix="experiment-record-") as temp:
        path = Path(temp) / name
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return store.put_file(
            path, name=name, media_type="application/json", schema_version=schema
        ).model_dump(mode="json")


def _retain(store, root):
    members = {}
    if root.exists():
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if (
                path.is_file()
                and not path.is_symlink()
                and relative.parts[0] not in {"cache", "home", "tmp"}
            ):
                members[path.relative_to(root).as_posix()] = store.put_file(
                    path,
                    name=path.name,
                    media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    schema_version=None,
                ).model_dump(mode="json")
    return members


def _receipt(store, result):
    result["receipt"] = _put(
        store, result, "experiment-replay.json", "harness.experiment_replay.v1"
    )
    return result


def _read_replay(store, result):
    reference = result["receipt"] if isinstance(result, dict) and "receipt" in result else result
    saved = _json(store, reference)
    if saved.get("schema_version") != "harness.experiment_replay.v1":
        raise ValueError("unexpected replay receipt schema")
    if (
        isinstance(result, dict)
        and "receipt" in result
        and {key: value for key, value in result.items() if key != "receipt"} != saved
    ):
        raise ValueError("replay summary differs from saved receipt")
    return reference, saved


def observe(
    store, replay_result, *, ttl_seconds=300, output_root=None, runtime_roots=None, denied_roots=()
):
    """Consume real camera completion time; expired evidence needs another actual capture."""
    from PIL import Image

    from .genesis_scene_assessment import require_fresh_capture

    result = {
        "schema_version": "harness.experiment_observation.v1",
        "status": "failed",
        "ttl_seconds": ttl_seconds,
        "source": "replay_final_camera_event",
    }
    try:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ValueError("observation TTL must be an integer from 1 to 300 seconds")
        reference, saved = _read_replay(store, replay_result)
        result.update(replay=_ref(reference).model_dump(mode="json"), scene=saved["scene"])
        if saved.get("simulator_executed") is not True:
            raise ValueError("replay contains no executed camera event")
        profile = "half_dt" if "half_dt" in saved["profiles"] else "baseline"
        members = saved["profiles"][profile]["members"]
        media = _json(store, members[profile + "/media.json"])
        capture = media["frames"][-1]
        result["captured_at"] = capture["captured_at"]
        try:
            require_fresh_capture(
                datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00")),
                now=datetime.now(timezone.utc),
                ttl_seconds=ttl_seconds,
            )
        except ValueError:
            if output_root is None or runtime_roots is None:
                result["error_code"] = "fresh_capture_required"
                raise ValueError(
                    "expired camera event needs declared runtime and new output directory"
                )
            fresh = run_package(
                saved["package_path"],
                runtime_roots=runtime_roots,
                output=output_root,
                denied_roots=denied_roots,
                profile="half_dt",
            )
            members = _retain(store, Path(output_root))
            result["fresh_execution"] = fresh
            result["fresh_members"] = members
            if fresh["status"] != "passed":
                raise ValueError("fresh capture execution failed")
            profile = "half_dt"
            capture = _json(store, members["half_dt/media.json"])["frames"][-1]
            result["source"] = "new_half_dt_replay_camera_event"
            result["captured_at"] = capture["captured_at"]
            require_fresh_capture(
                datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00")),
                now=datetime.now(timezone.utc),
                ttl_seconds=ttl_seconds,
            )
        image_ref = members[profile + "/" + capture["path"]]
        path = store.resolve(_ref(image_ref)).path
        with Image.open(path) as image:
            image.load()
            if (
                image.mode != "RGB"
                or hashlib.sha256(image.tobytes()).hexdigest() != capture["rgb_sha256"]
            ):
                raise ValueError("capture pixels differ from recorded event")
        result.update(
            status="passed",
            image=image_ref,
            capture=capture,
            checked_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as error:
        result.setdefault("error_code", "observation_evidence_failed")
        result["reason"] = str(error)
    result["receipt"] = _put(store, result, "experiment-observation.json", result["schema_version"])
    return result


def validate(store, *, input_ref, scene_ref, replay_result, visual_advisory=None, observation=None):
    """Recompute physical facts; an advisory never changes their outcome."""
    result = {
        "schema_version": "harness.experiment_validation.v1",
        "status": "failed",
        "input": _ref(input_ref).model_dump(mode="json"),
        "scene": _ref(scene_ref).model_dump(mode="json"),
        "physical_status": "not_run",
        "visual_status": "not_run",
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
        "qualification_status": "development_unqualified",
    }
    try:
        store.resolve(_ref(input_ref))
        reference, replayed = _read_replay(store, replay_result)
        result["replay"] = reference
        if replayed["scene"] != result["scene"]:
            raise ValueError("scene differs from replay binding")
        if visual_advisory is not None:
            if visual_advisory.get("status") not in {"passed", "failed", "not_run"}:
                raise ValueError("invalid visual advisory status")
            visual_status = visual_advisory.get("visual_status", visual_advisory["status"])
            if visual_status not in {"passed", "failed", "not_run"}:
                raise ValueError("invalid visual intent status")
            result["visual_status"] = visual_status
            result["visual_advisory"] = visual_advisory
        if "report" in replayed:
            scene = GenesisSceneSpecV1.model_validate(_json(store, scene_ref))
            rows = {}
            for profile in ("baseline", "half_dt"):
                trace = replayed["profiles"][profile]["members"][profile + "/trace.ndjson"]
                rows[profile] = [
                    json.loads(line)
                    for line in store.resolve(_ref(trace)).path.read_bytes().splitlines()
                ]
            report = evaluate_scene_replay(scene, rows["baseline"], rows["half_dt"])
            if report.model_dump(mode="json") != _json(store, replayed["report"]):
                raise ValueError("physical report differs from independently recomputed traces")
            result["physical_report"] = replayed["report"]
            result["physical_status"] = "passed" if report.status == "passed" else "failed"
        if observation is not None:
            obs_ref = observation.get("receipt", observation)
            observed = _json(store, obs_ref)
            if (
                observed.get("status") != "passed"
                or observed["replay"] != reference
                or observed["scene"] != result["scene"]
            ):
                raise ValueError("observation differs from replay/scene")
            from .genesis_scene_assessment import require_fresh_capture

            require_fresh_capture(
                datetime.fromisoformat(observed["captured_at"].replace("Z", "+00:00")),
                now=datetime.now(timezone.utc),
                ttl_seconds=observed["ttl_seconds"],
            )
            result["observation"] = obs_ref
        if (
            replayed["status"] == "passed"
            and result["physical_status"] == "passed"
            and result["visual_status"] == "passed"
            and observation is not None
        ):
            result["status"] = "passed"
        else:
            result["error_code"] = "validation_checks_incomplete_or_failed"
    except Exception as error:
        result["error_code"] = "validation_binding_or_evidence_failed"
        result["reason"] = str(error)
    result["receipt"] = _put(store, result, "experiment-validation.json", result["schema_version"])
    return result


def replay(store, scene_ref, output_root, runtime_roots, denied_roots=()):
    """Run both unchanged physical profiles and retain all produced evidence."""
    started = time.monotonic()
    root = Path(output_root).absolute()
    root.mkdir(parents=True, exist_ok=False)
    result = {
        "schema_version": "harness.experiment_replay.v1",
        "status": "failed",
        "physical_status": "not_run",
        "scene": _ref(scene_ref).model_dump(mode="json"),
        "profiles": {},
        "simulator_executed": False,
        "qualification_status": "development_unqualified",
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
    }
    try:
        scene = GenesisSceneSpecV1.model_validate(_json(store, scene_ref))
        export_package(store, scene_ref=scene_ref, output=root / "package")
        result["package_path"] = str(root / "package")
        rows = {}
        for profile in ("baseline", "half_dt"):
            execution = run_package(
                root / "package",
                runtime_roots=runtime_roots,
                output=root / profile,
                denied_roots=denied_roots,
                profile=profile,
            )
            members = _retain(store, root / profile)
            result["profiles"][profile] = {"execution": execution, "members": members}
            result["simulator_executed"] |= execution["simulator_executed"]
            if execution["status"] != "passed" or execution["simulator_executed"] is not True:
                result["error_code"] = execution.get("error_code", "replay_profile_failed")
                raise ValueError(f"{profile}: {execution.get('reason', 'profile failed')}")
            rows[profile] = [
                json.loads(line)
                for line in store.resolve(_ref(members[profile + "/trace.ndjson"]))
                .path.read_bytes()
                .splitlines()
            ]
        report = evaluate_scene_replay(scene, rows["baseline"], rows["half_dt"])
        result["report"] = _put(
            store, report.model_dump(mode="json"), "physical-report.json", report.schema_version
        )
        # Complete execution is useful diagnostic evidence even when physics fails.
        # Only validate can grant the independently recomputed physical profile.
        result["status"] = "passed"
        result["physical_status"] = report.status
    except Exception as error:
        result.setdefault("error_code", "replay_input_or_execution_failed")
        result["reason"] = str(error)
    result["wall_seconds"] = time.monotonic() - started
    return _receipt(store, result)

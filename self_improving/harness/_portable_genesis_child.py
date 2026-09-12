"""Package-owned entrypoint: public CLI or confined fresh Genesis child."""

import argparse
import json
import os
import sys
from pathlib import Path


def child(job_path):
    job = json.loads(Path(job_path).read_bytes())
    root, output = Path(job["root"]), Path(job["output"])
    roots = {k: Path(v) for k, v in job["roots"].items()}
    sys.path[:] = [
        str(root),
        str(roots["stdlib"]),
        str(roots["stdlib"] / "lib-dynload"),
        str(roots["distributions"] / "lib/python3.12/site-packages"),
        str(roots["genesis"]),
    ]
    cpu = Path("/proc/cpuinfo").read_text()
    memory = Path("/proc/meminfo").read_text()
    facts = {
        "cpu_name": next(
            line.partition(":")[2].strip()
            for line in cpu.splitlines()
            if line.startswith("model name")
        ),
        "memory_bytes": int(
            next(line.split()[1] for line in memory.splitlines() if line.startswith("MemTotal:"))
        )
        * 1024,
    }
    from portable_genesis_package import load_package
    from runner._genesis_scene_child import restrict_scene_process

    abi = restrict_scene_process({**roots, "package": root}, output)
    denials = []
    for denied in job["denied_roots"]:
        try:
            os.listdir(denied)
        except PermissionError:
            denials.append({"path": denied, "read_denied": True})
        else:
            raise RuntimeError("original source directory remained readable")
    manifest = load_package(root)
    profile = job.get("profile", "baseline")
    from runner.genesis_scene_cpu_backend import run_genesis_scene_cpu_v2

    observation = {
        "landlock_abi": abi,
        "source_read_denials": denials,
        "package_root": str(root),
        "external_runtime_roots": job["roots"],
    }
    result = run_genesis_scene_cpu_v2(
        json.loads((root / manifest["scene"]).read_bytes()),
        root / manifest["urdf"],
        output / profile,
        profile=profile,
        cpu_facts=facts,
        runtime_observer=lambda: observation,
    )
    summary = json.loads((output / profile / "result.json").read_bytes())
    if summary["status"] != "complete":
        raise RuntimeError("scene runner did not pass")
    media = json.loads((output / profile / "media.json").read_bytes())
    final = {
        "status": "passed",
        "simulator_executed": True,
        "steps": summary["steps"],
        "profile": profile,
        "isolation": observation,
        "total_frames": media["total_frames"],
        "unique_frames": media["unique_frames"],
        "checks": {
            "relocated_load_step": "passed",
            "sequential_media": "passed",
            "dual_dt_physical_profile": "not_run",
        },
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
    }
    (output / "child-result.json").write_text(json.dumps(final, sort_keys=True) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    parser.add_argument("--runtime", help="JSON mapping of declared runtime roots")
    parser.add_argument("--output")
    parser.add_argument("--deny-root", action="append", default=[])
    parser.add_argument("--profile", choices=("baseline", "half_dt"), default="baseline")
    args = parser.parse_args()
    if args.child:
        child(args.child)
        return 0
    if not args.runtime or not args.output:
        parser.error("--runtime and --output are required")
    from portable_genesis_package import run_package

    result = run_package(
        Path(__file__).parent,
        runtime_roots=json.loads(Path(args.runtime).read_text()),
        output=args.output,
        denied_roots=args.deny_root,
        profile=args.profile,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

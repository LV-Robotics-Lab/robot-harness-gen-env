"""Fixed-path CoACD CPU worker. External library is imported, never vendored."""

import hashlib
import importlib.util
import json
import math
import numbers
import sys
import time
from pathlib import Path


def _arrays(vertices, faces, code):
    import numpy as np

    for rows, integer in ((vertices, False), (faces, True)):
        if not isinstance(rows, (list, tuple, np.ndarray)) or (
            isinstance(rows, np.ndarray) and rows.ndim != 2
        ):
            raise ValueError(code)
        if not len(rows):
            raise ValueError(code)
        for row in rows:
            if not isinstance(row, (list, tuple, np.ndarray)) or len(row) != 3:
                raise ValueError(code)
            for value in row:
                if isinstance(value, (bool, np.bool_)) or not isinstance(
                    value, numbers.Integral if integer else numbers.Real
                ):
                    raise ValueError(code)
                try:
                    finite = math.isfinite(value)
                except OverflowError:
                    finite = False
                if not finite:
                    raise ValueError(code)
    vertices, faces = np.asarray(vertices), np.asarray(faces)
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError(code)
    return vertices, faces


def execute(job):
    import numpy as np

    package = Path(job["coacd_package"])
    for name, expected in (
        ("__init__.py", job["package_sha256"]),
        ("lib_coacd.so", job["library_sha256"]),
    ):
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != expected:
            raise ValueError("coacd_library_identity_mismatch")
    if sorted(p.name for p in package.iterdir() if p.name.startswith("lib_coacd")) != [
        "lib_coacd.so"
    ]:
        raise ValueError("ambiguous_coacd_native_library")
    metadata = package.parent / f"coacd-{job['version']}.dist-info/METADATA"
    if f"Version: {job['version']}" not in metadata.read_text().splitlines():
        raise ValueError("coacd_version_mismatch")
    raw = Path(job["input_path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != job["input_sha256"]:
        raise ValueError("coacd_input_identity_mismatch")
    params = dict(job["policy"])
    for key in ("max_input_triangles", "max_output_triangles"):
        params.pop(key)
    spec = importlib.util.spec_from_file_location("coacd", package / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = json.loads(raw)
    vertices, faces = _arrays(data["vertices"], data["faces"], "invalid_coacd_input_arrays")
    parts = module.run_coacd(module.Mesh(vertices, faces), **params)
    if not 1 <= len(parts) <= job["policy"]["max_convex_hull"]:
        raise ValueError("coacd_piece_count_exceeded")
    count = 0
    names = []
    for index, part in enumerate(parts):
        if not isinstance(part, (list, tuple)) or len(part) != 2:
            raise ValueError("invalid_coacd_piece_arrays")
        vertices, faces = _arrays(*part, "invalid_coacd_piece_arrays")
        count += len(faces)
        if count > job["policy"]["max_output_triangles"]:
            raise ValueError("coacd_triangle_budget_exceeded")
        name = f"part-{index}.obj"
        with (Path(job["output_root"]) / name).open("x") as stream:
            for row in vertices:
                stream.write("v " + " ".join(format(float(v), ".17g") for v in row) + "\n")
            for row in faces:
                stream.write("f " + " ".join(str(int(v) + 1) for v in row) + "\n")
        names.append(name)
    return {
        "status": "succeeded",
        "pieces": names,
        "scope": "coacd_cpu_candidate",
        "version": job["version"],
        "package_sha256": job["package_sha256"],
        "library_sha256": job["library_sha256"],
        "numpy_version": np.__version__,
        "input_sha256": job["input_sha256"],
        "policy": job["policy"],
    }


def main():
    job = json.loads(Path(sys.argv[1]).read_bytes())
    started = time.monotonic()
    try:
        result = execute(job)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result = {"status": "failed", "error_code": str(exc)}
    result["wall_seconds"] = time.monotonic() - started
    (Path(job["output_root"]) / "result.json").write_text(
        json.dumps(result, sort_keys=True, allow_nan=False)
    )
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())

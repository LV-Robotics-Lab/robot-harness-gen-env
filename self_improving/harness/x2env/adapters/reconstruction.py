"""Bounded command adapter for fixed Gujie SAM2 and reconstruction-only seams.

Original Gujie/SimFoundry/Hunyuan interfaces remain available in their fixed source
checkout, non-default and unchanged. This adapter neither owns a workflow nor plans.
Input-derived output permission is independent of source/model software licensing.
"""

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image
from pydantic import Field

from ..contracts import ArtifactRef, Model, Sha256


class ArtifactStore(Protocol):
    def read_artifact(self, ref: ArtifactRef) -> bytes: ...
    def write_artifact(self, data: bytes, media_type: str) -> ArtifactRef: ...


@dataclass(frozen=True)
class ReconstructionDeployment:
    source_root: Path
    source_commit: str
    python: Path
    python_sha256: str
    segmentation_runtime: Path
    segmentation_runtime_sha256: str
    reconstruction_runtime: Path
    reconstruction_runtime_sha256: str
    model_refs: dict[str, str]
    derivation_authorization: ArtifactRef | None = None


class SegmentationProposal(Model):
    input_sha256: Sha256
    proposal_receipt: ArtifactRef
    box_xyxy: tuple[float, float, float, float] | None = None
    point_coords: tuple[tuple[float, float], ...] = Field(default=(), max_length=32)
    point_labels: tuple[Literal[0, 1], ...] = Field(default=(), max_length=32)


class ReconstructionResult(Model):
    status: Literal["succeeded", "blocked", "failed"]
    geometry: ArtifactRef | None = None
    source_path: str | None = None
    receipt: ArtifactRef
    code_model_licenses: ArtifactRef | None = None
    derivation_authorization: ArtifactRef | None = None
    registration_allowed: bool = False
    authorization_trust: Literal["delegated_to_harness"] = "delegated_to_harness"
    error_code: str | None = None
    physical_evaluated: Literal[False] = False


SEGMENT_BRIDGE = (
    "import json,sys; from pathlib import Path; "
    "from self_improving.sim_adapters.simfoundry.controlled_segmentation import segment_image; "
    "r=json.loads(Path(sys.argv[1]).read_text()); "
    "result=segment_image(r.pop('image'),r.pop('output'),**r); "
    "print(json.dumps(result)); sys.exit(0 if result['status']=='passed' else 1)"
)


class ReconstructionAdapter:
    def __init__(self, deployment: ReconstructionDeployment, store: ArtifactStore):
        self.deployment = deployment
        self.store = store

    def reconstruct(
        self,
        image: ArtifactRef,
        *,
        proposal: SegmentationProposal,
        mass_kg: float,
        friction: float,
        seed: int,
        output_root: Path,
        timeout: int = 1770,
    ):
        import math

        if (
            type(timeout) is not int
            or not 1 <= timeout <= 1770
            or not math.isfinite(mass_kg)
            or mass_kg <= 0
            or not math.isfinite(friction)
            or friction < 0
            or type(seed) is not int
            or not 0 <= seed < 2**32
        ):
            raise ValueError("invalid reconstruction budget or supplied physics")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("attempt root must be a new absolute non-symbolic directory")
        root.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        deadline = started + timeout
        status, error, geometry, source_path, licenses = "failed", None, None, None, None
        allowed = False
        deployment = self.deployment
        artifacts = []

        def record(name, data, media_type="application/json"):
            (root / name).write_bytes(data)
            ref = self.store.write_artifact(data, media_type)
            artifacts.append({"path": name, "artifact": ref.model_dump()})
            return ref

        def command(argv, label):
            record(f"{label}-command.json", json.dumps(argv).encode())
            environment = {
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "CUDA_VISIBLE_DEVICES"}
            }
            environment["PYTHONPATH"] = str(deployment.source_root)
            with (
                (root / f"{label}.stdout").open("wb") as stdout,
                (root / f"{label}.stderr").open("wb") as stderr,
            ):
                process = subprocess.Popen(
                    argv,
                    cwd=deployment.source_root,
                    env=environment,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                stat = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()
                record(
                    f"{label}-process.json",
                    json.dumps(
                        {
                            "pid": process.pid,
                            "pgid": process.pid,
                            "start_ticks": stat[19],
                            "source_commit": deployment.source_commit,
                        }
                    ).encode(),
                )
                try:
                    code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    os.killpg(process.pid, signal.SIGINT)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=10)
                    raise ValueError(
                        "backend_timeout"
                        if isinstance(exc, subprocess.TimeoutExpired)
                        else "backend_interrupted"
                    ) from exc
            if code:
                raise ValueError("backend_failed")

        try:
            for path, expected in (
                (deployment.python, deployment.python_sha256),
                (deployment.segmentation_runtime, deployment.segmentation_runtime_sha256),
                (deployment.reconstruction_runtime, deployment.reconstruction_runtime_sha256),
            ):
                if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                    raise ValueError("deployment_identity_mismatch")
            head = (
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=deployment.source_root, timeout=10
                )
                .decode()
                .strip()
            )
            dirty = subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=deployment.source_root, timeout=10
            )
            if head != deployment.source_commit or dirty:
                raise ValueError("deployment_identity_mismatch")
            raw = self.store.read_artifact(image)
            self.store.read_artifact(proposal.proposal_receipt)
            if proposal.input_sha256 != image.sha256:
                raise ValueError("proposal_input_mismatch")
            with Image.open(BytesIO(raw)) as decoded:
                width, height = decoded.size
                decoded.verify()
            box = proposal.box_xyxy
            if box and not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
                raise ValueError("invalid_segmentation_proposal")
            if (
                len(proposal.point_coords) != len(proposal.point_labels)
                or (not box and not proposal.point_coords)
                or any(not 0 <= x < width or not 0 <= y < height for x, y in proposal.point_coords)
            ):
                raise ValueError("invalid_segmentation_proposal")
            record("input.png", raw, image.media_type)
            record("proposal.json", proposal.model_dump_json().encode())
            seg_runtime = json.loads(Path(deployment.segmentation_runtime).read_bytes())
            rec_runtime = json.loads(Path(deployment.reconstruction_runtime).read_bytes())
            if seg_runtime.get("python") != str(deployment.python) or rec_runtime.get(
                "python"
            ) != str(deployment.python):
                raise ValueError("deployment_identity_mismatch")
            record("reconstruction-runtime.json", json.dumps(rec_runtime).encode())
            record(
                "deployment.json",
                json.dumps(
                    {
                        "source_root": str(deployment.source_root),
                        "source_commit": head,
                        "python": str(deployment.python),
                        "python_sha256": deployment.python_sha256,
                        "model_refs": deployment.model_refs,
                        "segmentation_runtime": seg_runtime,
                        "reconstruction_runtime": rec_runtime,
                    }
                ).encode(),
            )
            segmentation_request = {
                "image": str(root / "input.png"),
                "output": str(root / "segmentation"),
                "runtime": seg_runtime,
                "provenance": {
                    "kind": "proposed_by_managed_codex",
                    "input_sha256": image.sha256,
                    "proposal_receipt_sha256": proposal.proposal_receipt.sha256,
                },
                "box_xyxy": list(box) if box else None,
                "point_coords": [list(p) for p in proposal.point_coords],
                "point_labels": list(proposal.point_labels),
            }
            record("segmentation-request.json", json.dumps(segmentation_request).encode())
            command(
                [
                    str(deployment.python),
                    "-B",
                    "-c",
                    SEGMENT_BRIDGE,
                    str(root / "segmentation-request.json"),
                ],
                "segmentation",
            )
            segmented = json.loads((root / "segmentation/result.json").read_bytes())
            rgba = root / "segmentation/rgba.png"
            if (
                segmented["status"] != "passed"
                or segmented["input_sha256"] != image.sha256
                or hashlib.sha256(rgba.read_bytes()).hexdigest() != segmented["rgba"]["sha256"]
            ):
                raise ValueError("backend_integrity_mismatch")
            command(
                [
                    str(deployment.python),
                    "-B",
                    "-m",
                    "self_improving.sim_adapters.simfoundry.reconstruction_only",
                    "--image",
                    str(rgba),
                    "--output",
                    str(root / "reconstruction"),
                    "--runtime",
                    str(root / "reconstruction-runtime.json"),
                    "--mass-kg",
                    str(mass_kg),
                    "--friction",
                    str(friction),
                    "--seed",
                    str(seed),
                    "--input-mode",
                    "masked_rgba",
                ],
                "reconstruction",
            )
            result = json.loads((root / "reconstruction/result.json").read_bytes())
            if (
                result["status"] != "passed"
                or result["input_sha256"] != segmented["rgba"]["sha256"]
            ):
                raise ValueError("backend_integrity_mismatch")
            actual_models = {
                "segmentation": segmented.get("backend", {}).get("provenance", {}),
                "reconstruction": result["provenance"],
            }
            if not deployment.model_refs:
                raise ValueError("deployment_identity_mismatch")
            for key, expected in deployment.model_refs.items():
                actual = actual_models
                for part in key.split("."):
                    actual = actual.get(part) if isinstance(actual, dict) else None
                if actual != expected:
                    raise ValueError("deployment_identity_mismatch")
            path = root / "reconstruction/geometry.glb"
            data = path.read_bytes()
            if (
                path.is_symlink()
                or hashlib.sha256(data).hexdigest() != result["geometry"]["sha256"]
                or len(data) != result["geometry"]["bytes"]
            ):
                raise ValueError("backend_integrity_mismatch")
            geometry = self.store.write_artifact(data, "model/gltf-binary")
            source_path = str(path)
            licenses = record(
                "code-model-licenses.json",
                json.dumps(
                    {
                        "segmentation": segmented.get("backend", {}).get("provenance"),
                        "reconstruction": result["provenance"],
                        "output_license_inferred_from_model": False,
                    }
                ).encode(),
            )
            auth_ref = deployment.derivation_authorization
            if auth_ref:
                auth = json.loads(self.store.read_artifact(auth_ref))
                allowed = (
                    auth.get("input_sha256") == image.sha256
                    and auth.get("allow_derivative") is True
                    and auth.get("output_spdx") in {"CC0-1.0", "CC-BY-4.0"}
                    and bool(auth.get("source_url"))
                    and (auth["output_spdx"] == "CC0-1.0" or bool(auth.get("attribution")))
                )
            status, error = ("succeeded", None) if allowed else ("blocked", "blocked_license")
        except FileNotFoundError:
            status, error = "blocked", "blocked_external_resource"
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            known = {
                "deployment_identity_mismatch",
                "proposal_input_mismatch",
                "invalid_segmentation_proposal",
                "backend_timeout",
                "backend_interrupted",
                "backend_failed",
                "backend_integrity_mismatch",
            }
            error = str(exc) if str(exc) in known else "invalid_reconstruction_evidence"
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and "cache" not in path.relative_to(root).parts
            ):
                ref = self.store.write_artifact(path.read_bytes(), "application/octet-stream")
                artifacts.append(
                    {"path": str(path.relative_to(root)), "artifact": ref.model_dump()}
                )
        receipt = record(
            "receipt.json",
            json.dumps(
                {
                    "status": status,
                    "error_code": error,
                    "input": image.model_dump(),
                    "proposal": proposal.model_dump(),
                    "geometry": geometry.model_dump() if geometry else None,
                    "physics": {
                        "mass_kg": mass_kg,
                        "friction": friction,
                        "basis": "supplied_not_measured",
                    },
                    "derivation_authorization": deployment.derivation_authorization.model_dump()
                    if deployment.derivation_authorization
                    else None,
                    "authorization_trust": "delegated_to_harness",
                    "registration_allowed": allowed,
                    "wall_seconds": time.monotonic() - started,
                    "artifacts": artifacts,
                }
            ).encode(),
        )
        return ReconstructionResult(
            status=status,
            error_code=error,
            geometry=geometry,
            source_path=source_path,
            receipt=receipt,
            code_model_licenses=licenses,
            derivation_authorization=deployment.derivation_authorization,
            registration_allowed=allowed,
        )

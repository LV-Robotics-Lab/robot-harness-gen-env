"""Canonical reconstruction seam; external runtime is unavailable or an explicit double."""

import hashlib
import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from self_improving.harness.x2env.store import Store


def test_missing_deployment_dependency_is_structured_blocker(tmp_path):
    from self_improving.harness.x2env.adapters.reconstruction import (
        ReconstructionAdapter,
        ReconstructionDeployment,
        SegmentationProposal,
    )

    store = Store(tmp_path / "state")
    image = store.write_artifact(b"image", "image/png")
    receipt = store.write_artifact(b"proposal", "application/json")
    deployment = ReconstructionDeployment(
        source_root=tmp_path / "missing",
        source_commit="a" * 40,
        python=Path("/missing/python"),
        python_sha256="b" * 64,
        segmentation_runtime=tmp_path / "sam.json",
        segmentation_runtime_sha256="c" * 64,
        reconstruction_runtime=tmp_path / "runtime.json",
        reconstruction_runtime_sha256="d" * 64,
        model_refs={},
    )
    result = ReconstructionAdapter(deployment, store).reconstruct(
        image,
        proposal=SegmentationProposal(
            input_sha256=image.sha256, proposal_receipt=receipt, box_xyxy=(0.0, 0.0, 1.0, 1.0)
        ),
        mass_kg=0.1,
        friction=0.6,
        seed=1,
        output_root=tmp_path / "attempt",
        timeout=10,
    )
    assert result.status == "blocked"
    assert result.error_code == "blocked_external_resource"
    assert result.registration_allowed is False
    assert store.read_artifact(result.receipt)


@pytest.mark.parametrize("fault", ["none", "timeout", "wrong_model", "deployment_permission"])
def test_geometry_without_input_derivation_permission_remains_blocked(tmp_path, monkeypatch, fault):
    from self_improving.harness.x2env.adapters.reconstruction import (
        ReconstructionAdapter,
        ReconstructionDeployment,
        SegmentationProposal,
    )

    source = tmp_path / "source"
    source.mkdir()
    python = tmp_path / "runtime-double"
    python.write_text(
        f"#!{sys.executable}\n"
        + """
import sys,json,hashlib,time
from pathlib import Path
if "-c" in sys.argv:
    r=json.loads(Path(sys.argv[-1]).read_text()); out=Path(r['output']); out.mkdir()
    raw=Path(r['image']).read_bytes(); (out/'rgba.png').write_bytes(raw)
    result={'status':'passed','input_sha256':hashlib.sha256(raw).hexdigest(),
        'rgba':{'sha256':hashlib.sha256(raw).hexdigest()},'backend':{'provenance':{'source_commit':'sam-fixed'}}}
else:
    out=Path(sys.argv[sys.argv.index('--output')+1]); out.mkdir()
    raw=Path(sys.argv[sys.argv.index('--image')+1]).read_bytes()
    (out/'geometry.glb').write_bytes(b'explicit external geometry double')
    result={'status':'passed','input_sha256':hashlib.sha256(raw).hexdigest(),
        'geometry':{'sha256':hashlib.sha256(b'explicit external geometry double').hexdigest(),
                    'bytes':33},
        'provenance':{'model_commit':'model-fixed','licenses':{'model':'MIT'}}}
(out/'result.json').write_text(json.dumps(result))
""".replace(
            "raw=Path(r['image'])",
            "time.sleep(20); raw=Path(r['image'])"
            if fault == "timeout"
            else "raw=Path(r['image'])",
        )
    )
    python.chmod(0o700)
    sam = tmp_path / "sam.json"
    runtime = tmp_path / "runtime.json"
    sam.write_text(
        json.dumps(
            {"python": str(python), "sam2_root": str(source), "checkpoint": str(source / "weights")}
        )
    )
    runtime.write_text(
        json.dumps(
            {
                "python": str(python),
                "trellis_root": str(source),
                "dino_root": str(source),
                "models_root": str(source),
            }
        )
    )
    monkeypatch.setattr(
        subprocess, "check_output", lambda argv, **kw: b"a" * 40 if argv[1] == "rev-parse" else b""
    )
    store = Store(tmp_path / "state")
    output = BytesIO()
    Image.new("RGB", (3, 3), "red").save(output, format="PNG")
    image = store.write_artifact(output.getvalue(), "image/png")
    receipt = store.write_artifact(b"explicit proposal double", "text/plain")
    deployment = ReconstructionDeployment(
        source,
        "a" * 40,
        python,
        hashlib.sha256(python.read_bytes()).hexdigest(),
        sam,
        hashlib.sha256(sam.read_bytes()).hexdigest(),
        runtime,
        hashlib.sha256(runtime.read_bytes()).hexdigest(),
        {
            "segmentation.source_commit": "sam-fixed",
            "reconstruction.model_commit": "wrong" if fault == "wrong_model" else "model-fixed",
        },
    )
    if fault == "deployment_permission":
        from dataclasses import replace

        authorization = store.write_artifact(
            json.dumps(
                {
                    "input_sha256": image.sha256,
                    "allow_derivative": True,
                    "output_spdx": "CC-BY-4.0",
                    "attribution": "Explicit unit fixture author",
                    "source_url": "https://example.org/fixture",
                }
            ).encode(),
            "application/json",
        )
        deployment = replace(deployment, derivation_authorization=authorization)
    result = ReconstructionAdapter(deployment, store).reconstruct(
        image,
        proposal=SegmentationProposal(
            input_sha256=image.sha256, proposal_receipt=receipt, box_xyxy=(0.0, 0.0, 3.0, 3.0)
        ),
        mass_kg=0.1,
        friction=0.6,
        seed=1,
        output_root=tmp_path / "attempt",
        timeout=1 if fault == "timeout" else 10,
    )
    assert result.registration_allowed is (fault == "deployment_permission")
    if fault == "deployment_permission":
        assert result.status == "succeeded"
        assert result.authorization_trust == "delegated_to_harness"
        return
    if fault == "none":
        assert result.status == "blocked"
        assert result.error_code == "blocked_license"
        assert result.geometry is not None
    else:
        assert result.status == "failed"
        assert result.error_code == (
            "backend_timeout" if fault == "timeout" else "deployment_identity_mismatch"
        )

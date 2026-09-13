"""Explicit transport doubles verify Yuxin's injected model boundary, not VLM quality."""

import hashlib
import json
import sys
from io import BytesIO

import pytest
from PIL import Image

from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.store import Store


@pytest.mark.parametrize("fault", [None, "wrong_color", "model_tool", "schema"])
def test_candidate_assessment_uses_original_verifier_and_bound_codex_images(tmp_path, fault):
    from self_improving.harness.x2env.asset_advisory import VisualCandidate

    store = Store(tmp_path / "state")
    data = BytesIO()
    Image.new("RGB", (8, 8), "pink").save(data, format="PNG")
    image = store.write_artifact(data.getvalue(), "image/png")
    answer = {
        "object": "mouse",
        "match": True,
        "colors": ["pink"],
        "materials": ["plastic"],
        "confidence": 0.9,
        "same_kind": None,
        "plausible": None,
        "suggests": None,
    }
    if fault == "wrong_color":
        answer["colors"] = ["blue"]
    if fault == "schema":
        answer["skill_success"] = True
    program = tmp_path / "codex-double"
    program.write_text(
        f"#!{sys.executable}\nimport sys,pathlib\n"
        "sys.stdin.read()\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])"
        f".write_text({json.dumps(answer)!r})\n"
        'print(\'{"type":"turn.completed"}\')\n'
        + (
            'print(\'{"type":"item.completed","item":{"type":"mcp_tool_call"}}\')\n'
            if fault == "model_tool"
            else ""
        )
    )
    program.chmod(0o700)
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "test-double", store
    )
    result = backend.assess_asset_candidates(
        (
            VisualCandidate(
                candidate_id="mouse-1",
                name="mouse",
                category="mouse",
                preview=image,
                want_color="pink",
                want_material="plastic",
            ),
        ),
        output_root=tmp_path / "assessment",
        timeout=30,
    )
    assert result.status == ("failed" if fault in {"model_tool", "schema"} else "completed")
    assert result.verdicts[0].verdict == (
        "unreadable"
        if fault in {"model_tool", "schema"}
        else "mismatch"
        if fault == "wrong_color"
        else "match"
    )
    assert result.verdicts[0].preview == image
    assert result.verdicts[0].candidate_id == "mouse-1"
    assert result.physical_evaluated is False
    assert len(list((tmp_path / "assessment").glob("*/invocation.json"))) == (
        1 if fault in {"model_tool", "schema"} else 2
    )
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["verifier"].endswith("a6_verify.verify_candidate")
    assert receipt["model"] == "test-double"

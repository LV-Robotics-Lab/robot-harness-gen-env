import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "a_forward" / "s5_check_ir.py"


def _rep(tmp_path, name, sha_char):
    uri = tmp_path / name
    uri.write_text("usd")
    return {
        "format": "usd",
        "uri": str(uri),
        "backend": "isaacsim",
        "role": "visual",
        "sha256": sha_char * 64,
        "size_bytes": 3,
    }


def _run(*paths):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(p) for p in paths]],
        capture_output=True,
        text=True,
    )


def test_ledger_input_unpacks_and_checks_each_model(tmp_path):
    # T7: s5's new branch -- an authoritative per-asset ledger (top-level
    # "models" list) must be unpacked via to_ir_bundles into one IR bundle
    # per model, each independently validated (not just the first / not
    # collapsed into a single check).
    ledger_doc = {
        "asset_id": "external_399_widget",
        "category": "widget",
        "tags": ["rigid"],
        "models": [
            {
                "model_id": 0,
                "physical": {},
                "representations": [_rep(tmp_path, "m0.usd", "a")],
                "source": {},
                "articulation": {},
            },
            {
                "model_id": 1,
                "physical": {},
                "representations": [_rep(tmp_path, "m1.usd", "b")],
                "source": {},
                "articulation": {},
            },
        ],
    }
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps(ledger_doc))

    r = _run(p)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS s5" in r.stdout
    assert "external_399_widget_m0" in r.stdout
    assert "external_399_widget_m1" in r.stdout
    assert r.stdout.count("PASS ledger.json::") == 2  # one line per model


def test_flattened_bundle_input_still_works(tmp_path):
    # Back-compat: a legacy flattened per-model bundle dict (no "models"
    # key) must go through the old single-bundle path unchanged, including
    # the pre-existing PASS-uses-basename convention.
    flat_doc = {
        "asset_id": "external_399_widget_m0",
        "category": "widget",
        "representations": [_rep(tmp_path, "m0.usd", "a")],
        "source": {},
        "physical": {},
        "articulation": {},
        "tags": [],
    }
    p = tmp_path / "flat_bundle.json"
    p.write_text(json.dumps(flat_doc))

    r = _run(p)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS s5" in r.stdout
    assert f"PASS {p.name}: isaacsim rep" in r.stdout


def test_ledger_input_one_bad_model_fails_whole_run(tmp_path):
    # A single unusable model inside a multi-model ledger must surface its
    # own FAIL line (identifying which model) and flip the overall verdict,
    # without a good sibling model masking it.
    ledger_doc = {
        "asset_id": "external_399_widget",
        "category": "widget",
        "tags": ["rigid"],
        "models": [
            {
                "model_id": 0,
                "physical": {},
                "representations": [_rep(tmp_path, "m0.usd", "a")],
                "source": {},
                "articulation": {},
            },
            {
                "model_id": 1,
                "physical": {},
                "representations": [
                    {
                        "format": "usd",
                        "uri": str(tmp_path / "does_not_exist.usd"),
                        "backend": "isaacsim",
                        "role": "visual",
                        "sha256": "b" * 64,
                        "size_bytes": 3,
                    }
                ],
                "source": {},
                "articulation": {},
            },
        ],
    }
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps(ledger_doc))

    r = _run(p)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL s5" in r.stdout
    assert "PASS ledger.json::external_399_widget_m0" in r.stdout
    assert "FAIL " in r.stdout and "external_399_widget_m1" in r.stdout
    assert "representation uri missing on disk" in r.stdout

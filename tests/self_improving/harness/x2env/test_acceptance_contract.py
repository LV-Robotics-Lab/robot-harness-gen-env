"""Public frozen acceptance documents; these tests do not claim runtime qualification."""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PROGRESS = ROOT / "self_improving/golden_e2e_progress"


def test_matrix_preserves_approved_twelve_cases_and_difficulty():
    matrix = json.loads((PROGRESS / "qualification-matrix-v1.json").read_text())
    assert [c["id"] for c in matrix["cases"]] == [
        "S01",
        "S02",
        "S03",
        "S04",
        "M01",
        "M02",
        "M03",
        "M04",
        "H01",
        "H02",
        "H03",
        "H04",
    ]
    assert Counter(c["difficulty"] for c in matrix["cases"]) == {
        "simple": 4,
        "medium": 4,
        "hard": 4,
    }
    assert [c["seed"] for c in matrix["cases"]] == [
        11,
        23,
        37,
        41,
        101,
        103,
        107,
        109,
        211,
        223,
        227,
        229,
    ]
    assert matrix["scoring"]["minimum_total"] == 8
    assert matrix["scoring"]["required_simple"] == 4


def test_prompts_match_frozen_plan_quotes_without_paraphrase():
    matrix = json.loads((PROGRESS / "qualification-matrix-v1.json").read_text())
    plan = (PROGRESS / matrix["plan"]).read_text()
    for case in matrix["cases"]:
        section = plan.split("#### " + case["id"] + " — ", 1)[1].split("\n#### ", 1)[0]
        quoted = re.findall(r"^> (.*)$", section, re.MULTILINE)
        assert case["prompt"] == ("".join(quoted) if quoted else None)
    assert {m["sha256"] for m in matrix["media"]} == {
        "a4c62f05001d759a43aa0bf0964bde7e7ee5df08ae546e21d6ba2e182ef27202",
        "7400aa21cb455c6efb142d96d2f1632d5329cf759aeb01ba498ed39e21dc5df8",
        "044e68562224788ad4e395d9c7cae49c148eb509a15f9ff11eb4aa39bc467d48",
        "5c7aa9c809aaff9c5f4d3076502d99cba0f72e46754102184b9c5c9db8d88634",
    }


def test_physics_profile_and_public_authority_preserve_frozen_limits():
    profile = json.loads((PROGRESS / "physics-assertions-v1.json").read_text())
    assert profile["replay"] == {
        "baseline": {"dt_s": 0.004, "steps": 1000},
        "half_dt": {"dt_s": 0.002, "steps": 2000},
        "duration_s": 4,
        "zero_initial_velocity": True,
        "independent_runs": True,
    }
    assert profile["stability"]["window_s"] == 0.5
    assert profile["stability"]["translation_m_max"] == 0.001
    assert profile["support"]["target_local_complete_footprint_margin_m_min"] == 0.02
    assert profile["containment"]["horizontal_clearance_m_min"] == 0.005
    assert profile["penetration"]["all_trajectory_m_max"] == 0.001
    assert profile["articulation"]["drift_deg_max"] == 2
    assert profile["authority"]["workflow_states"] == [
        "active",
        "succeeded",
        "failed",
        "blocked",
        "cancelled",
    ]
    assert profile["authority"]["public_skills"] == [
        "x2env.compile",
        "x2env.replay",
        "x2env.validate",
    ]
    assert profile["missing_metadata_error"] == "missing_physical_metadata"
    expected = {
        "stability": {
            "window_s": 0.5,
            "translation_m_max": 0.001,
            "rotation_deg_max": 0.5,
            "drift_m_per_s_max": 0.002,
            "rotation_rate_deg_per_s_max": 1,
            "excursion_m_max": 0.001,
        },
        "support": {
            "tail_contact_dropout_max": 0.05,
            "effective_upward_force_N_exclusive_min": 0.000001,
            "effective_upward_fraction_min": 0.8,
            "target_local_complete_footprint_margin_m_min": 0.02,
        },
        "penetration": {"all_trajectory_m_max": 0.001, "foreground_lowest_vertex_m_min": -0.001},
        "containment": {
            "horizontal_clearance_m_min": 0.005,
            "lowest_point_to_inner_support_m_max": 0.003,
        },
        "multi_support": {"per_declared_target_tail_effective_contact_fraction_min": 0.5},
        "cross_profile": {
            "per_foreground_final_position_m_max": 0.01,
            "per_foreground_final_rotation_deg_max": 5,
        },
        "articulation": {
            "open_revolute_limit_span_deg_min": 30,
            "initial_opening_span_fraction_min": 0.2,
            "initial_opening_span_fraction_max": 0.8,
            "drift_deg_max": 2,
            "tail_speed_deg_per_s_max": 1,
            "all_trajectory_limit_excess_deg_max": 0.5,
        },
    }
    for group, fields in expected.items():
        for name, limit in fields.items():
            assert profile[group][name] == limit
    assert profile["stability"]["sustained_effective_speed"] == {
        "formula": "max(norm(v), radius*norm(w)) >= 1.5*g*dt",
        "consecutive_rows_exclusive_max": 5,
    }
    assert profile["articulation"]["initial_distance_from_closed"] == (
        "max(15deg, opening_span*0.20)"
    )


def test_public_contract_documents_point_to_one_canonical_authority():
    for name in ("SEAMS.md", "ACCEPTANCE.md", "SKILL_CONTRACTS.md"):
        text = (PROGRESS / name).read_text()
        assert "canonical" in text
        assert "qualification-matrix-v1.json" in text
        assert "physics-assertions-v1.json" in text
        for target in re.findall(r"\]\(([^)]+)\)", text):
            assert (PROGRESS / target).is_file()
    vocabulary = (ROOT / "CONTEXT.md").read_text()
    assert "Managed Codex Backend" in vocabulary
    assert "External Codex Agent（外部 Codex Agent）" not in vocabulary
    assert "同一父级身份" not in vocabulary

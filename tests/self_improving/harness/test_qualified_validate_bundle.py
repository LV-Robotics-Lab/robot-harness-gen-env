"""Landing contract for the checked-in ``text2env.validate`` qualification."""

from __future__ import annotations

import hashlib
from pathlib import Path

from self_improving.harness.qualification import verify_qualification_bundle

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATE_BUNDLE = REPO_ROOT / "self_improving/harness/qualified_skills/text2env.validate/2.0.0"

EXPECTED_DOCUMENT_SHA256 = {
    "manifest.json": "fa324e584bdadf68d12fecb20db28df6bd61b5cd1c6765bbc6c3ed959d4dc8fe",
    "qualification.json": "86aa8c4f6d6ad853fa840a11a690255da25e9f76dc31555e9669a943e3c95842",
    "report.json": "2f3ef5d8993a5d33ee762c99f569b13c062139c81e88bd5d8526ce910e1818d6",
}
EXPECTED_IMPLEMENTATION_SHA256 = "a36fb7d9940e126d3c260c87c3e5aec9625f8f472b01850750ceb6f121c7a683"
EXPECTED_CHECK_NAMES = (
    "evidence.complete_lineage",
    "evidence.fresh_cas_reload",
    "evidence.negative_cases",
    "physics.real_recomputation",
    "registry.candidate_repeatability",
    "source.stability",
)


def test_checked_in_validate_qualification_matches_current_source_identity() -> None:
    document_sha256 = {
        name: hashlib.sha256((VALIDATE_BUNDLE / name).read_bytes()).hexdigest()
        for name in EXPECTED_DOCUMENT_SHA256
    }
    assert document_sha256 == EXPECTED_DOCUMENT_SHA256

    inspected = verify_qualification_bundle(
        VALIDATE_BUNDLE,
        skill_ref="text2env.validate@2.0.0",
        implementation_root=REPO_ROOT,
        scene_gen_root=REPO_ROOT / "scene_gen",
        ledger_contract_root=(
            REPO_ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib"
        ),
    )

    assert inspected.qualification.status == "pass"
    assert inspected.report.status == "pass"
    assert inspected.implementation_sha256 == EXPECTED_IMPLEMENTATION_SHA256
    assert inspected.manifest.bundle_sha256 == EXPECTED_IMPLEMENTATION_SHA256
    assert inspected.report.implementation_sha256 == EXPECTED_IMPLEMENTATION_SHA256
    assert tuple(check.name for check in inspected.report.checks) == EXPECTED_CHECK_NAMES
    assert {check.status for check in inspected.report.checks} == {"pass"}

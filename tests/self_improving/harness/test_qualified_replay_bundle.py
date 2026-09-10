"""Landing contract for the checked-in ``text2env.replay`` qualification."""

from __future__ import annotations

import hashlib
from pathlib import Path

from self_improving.harness.qualification import verify_qualification_bundle
from self_improving.harness.qualify_replay import REPLAY_IMPLEMENTATION_PATHS

REPO_ROOT = Path(__file__).resolve().parents[3]
REPLAY_BUNDLE = REPO_ROOT / "self_improving/harness/qualified_skills/text2env.replay/1.0.0"
SCENE_GEN_ROOT = REPO_ROOT / "scene_gen"
LEDGER_CONTRACT_ROOT = REPO_ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib"

EXPECTED_DOCUMENT_SHA256 = {
    "manifest.json": "9c2df8b0da668917df4ba3c870bc553c1b842188f3eba0c875374fdab8d0b69f",
    "qualification.json": "8da49335dfbee9040ab5c93df7e45cea30d84a15d0a95b450b6e4d0173ff4bdb",
    "report.json": "003e6e01ac51bf1deafb73a21511a1bd68252749a9cf419cd7f4e9ba68550c73",
}
EXPECTED_IMPLEMENTATION_SHA256 = "f6bc59e628bd72ff3b5195915a6bea53c7656154d6c71bd135eb6dd39347c604"
EXPECTED_SCENE_GEN_SHA256 = "e2fe9fd66d917dc6dea40e8990248206e180e3a086c1619b9f40558c3efec330"
EXPECTED_LEDGER_CONTRACT_SHA256 = "1d52584d44ca5f5d759f5e72d327fed282f0bdb4adaf763387b2fda2467ef489"
EXPECTED_CHECK_NAMES = [
    "01.case_binding",
    "02.exact_dependencies",
    "03.event_lifecycle",
    "04.runtime_asset_snapshot",
    "05.media_decode",
    "06.physics_validation",
    "07.source_stability",
    "08.candidate_kernel_executions",
]


def test_checked_in_replay_qualification_matches_current_source_identity() -> None:
    assert REPLAY_BUNDLE.is_dir(), "text2env.replay qualification bundle is not checked in"

    document_sha256 = {
        name: hashlib.sha256((REPLAY_BUNDLE / name).read_bytes()).hexdigest()
        for name in EXPECTED_DOCUMENT_SHA256
    }
    assert document_sha256 == EXPECTED_DOCUMENT_SHA256

    inspected = verify_qualification_bundle(
        REPLAY_BUNDLE,
        skill_ref="text2env.replay@1.0.0",
        implementation_root=REPO_ROOT,
        scene_gen_root=SCENE_GEN_ROOT,
        ledger_contract_root=LEDGER_CONTRACT_ROOT,
    )

    assert inspected.qualification.status == "pass"
    assert inspected.report.status == "pass"
    assert inspected.implementation_sha256 == EXPECTED_IMPLEMENTATION_SHA256
    assert inspected.manifest.bundle_sha256 == EXPECTED_IMPLEMENTATION_SHA256
    assert inspected.report.implementation_sha256 == EXPECTED_IMPLEMENTATION_SHA256
    assert inspected.manifest.scene_gen_tree_sha256 == EXPECTED_SCENE_GEN_SHA256
    assert inspected.report.scene_gen_tree_sha256 == EXPECTED_SCENE_GEN_SHA256
    assert inspected.manifest.ledger_contract_tree_sha256 == EXPECTED_LEDGER_CONTRACT_SHA256
    assert inspected.report.ledger_contract_tree_sha256 == EXPECTED_LEDGER_CONTRACT_SHA256
    assert tuple(item.path for item in inspected.manifest.files) == REPLAY_IMPLEMENTATION_PATHS
    assert [check.name for check in inspected.report.checks] == EXPECTED_CHECK_NAMES
    assert {check.status for check in inspected.report.checks} == {"pass"}

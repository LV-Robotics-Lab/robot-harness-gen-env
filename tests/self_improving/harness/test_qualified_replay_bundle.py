"""Landing contract for the checked-in ``text2env.replay`` qualification."""

from __future__ import annotations

import hashlib
from pathlib import Path

from self_improving.harness.qualification import verify_qualification_bundle

REPO_ROOT = Path(__file__).resolve().parents[3]
REPLAY_BUNDLE = REPO_ROOT / "self_improving/harness/qualified_skills/text2env.replay/1.0.0"
SCENE_GEN_ROOT = REPO_ROOT / "scene_gen"
LEDGER_CONTRACT_ROOT = REPO_ROOT / "self_improving/asset_pipeline/active/1_asset_reuse/lib"

EXPECTED_DOCUMENT_SHA256 = {
    "manifest.json": "0bc3815ecb939b8488f4541ea4343ed5f332f5f130fcb47ea240a5a7f15a9e83",
    "qualification.json": "ae2b4ee1fe9304382805dc8447828758247be4a47364cae22bd9b03418b399be",
    "report.json": "0690d305a794b23b3b701379540394f998c06a34d43eb019fc05cdf521dbef53",
}
EXPECTED_IMPLEMENTATION_SHA256 = "3ad6aff715baade429e2315d6a1f53ccc432affe57610a1a3653ef51587a0379"
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


def _current_harness_source_paths() -> tuple[str, ...]:
    harness_root = REPO_ROOT / "self_improving" / "harness"
    paths: list[str] = []
    for path in sorted(harness_root.rglob("*"), key=lambda item: item.as_posix()):
        relative_to_harness = path.relative_to(harness_root)
        if (
            "qualified_skills" in relative_to_harness.parts
            or "__pycache__" in relative_to_harness.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        assert not path.is_symlink(), f"Harness source path is a symlink: {path}"
        if path.is_file():
            paths.append(path.relative_to(REPO_ROOT).as_posix())
    return tuple(paths)


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
    assert tuple(item.path for item in inspected.manifest.files) == _current_harness_source_paths()
    assert [check.name for check in inspected.report.checks] == EXPECTED_CHECK_NAMES
    assert {check.status for check in inspected.report.checks} == {"pass"}

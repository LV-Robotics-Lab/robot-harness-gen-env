"""Public exact native image evidence policy contracts."""

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_image2env_policy import NativeImageEvidencePolicy
from self_improving.harness.run_store import SQLiteRunStore


def test_native_policy_rejects_unqualified_or_legacy_skill(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    authority = SQLiteRunStore(tmp_path / "runs.sqlite")
    with pytest.raises(ValueError, match="exact"):
        NativeImageEvidencePolicy(store, authority, "genesis.observe@2.0.0")


@pytest.mark.parametrize("skill", ["image2env.compile@2.0.0", "image2env.replay@2.0.0", "genesis.observe@3.0.0", "image2env.validate@2.0.0"])
def test_native_policy_has_frozen_exact_input_and_source_identity(tmp_path, skill):
    policy = NativeImageEvidencePolicy(LocalArtifactStore(tmp_path / "cas"), SQLiteRunStore(tmp_path / "runs.sqlite"), skill)
    assert policy.skill_ref == skill
    assert len(policy.evidence_policy_implementation_sha256) == 64
    with pytest.raises(ValueError):
        policy.normalize_parameters({})

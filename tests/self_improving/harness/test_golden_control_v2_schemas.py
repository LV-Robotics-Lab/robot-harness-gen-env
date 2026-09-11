"""Public v2 control contracts preserve exact fresh-capture evidence."""

import hashlib
import json

from self_improving.harness.schemas.golden_control_v2 import ObservationCitationV2


def ref(schema, label="x", media="application/json"):
    digest = hashlib.sha256(label.encode()).hexdigest()
    return dict(name=label, uri="artifact://sha256/" + digest, sha256=digest,
                bytes=1, media_type=media, schema_version=schema)


def citation():
    observation = dict(
        schema_version="harness.genesis_fresh_capture_receipt.v1",
        replay_run_id="10000000-0000-4000-8000-000000000001",
        replay_receipt=ref("harness.genesis_scene_execution_receipt.v2"),
        request=ref("harness.genesis_fresh_capture_request.v1"),
        capture_result=ref("harness.genesis_fresh_capture_result.v1"),
        image=ref(None, media="image/png"),
        captured_at="2026-09-11T10:00:00Z", checked_at="2026-09-11T10:00:05Z",
        expires_at="2026-09-11T10:05:00Z", ttl_seconds=300,
        authority="observed_restored_final_pose", process_continuation=False,
    )
    raw = json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
    receipt = ref(observation["schema_version"])
    receipt.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    receipt["uri"] = "artifact://sha256/" + receipt["sha256"]
    return dict(schema_version="harness.observation_citation.v2", receipt=receipt,
                observation=observation, captured_at=observation["captured_at"],
                expires_at=observation["expires_at"], ttl_seconds=300,
                authority="observed_restored_final_pose", process_continuation=False)


def test_citation_binds_actual_fresh_receipt_not_unchecked_time_claim():
    parsed = ObservationCitationV2.model_validate(citation())
    assert parsed.observation.replay_run_id.hex == "10000000000040008000000000000001"
    assert parsed.expires_at == parsed.observation.expires_at

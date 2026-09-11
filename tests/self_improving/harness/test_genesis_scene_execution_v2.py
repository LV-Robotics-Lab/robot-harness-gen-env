"""Versioned scene evidence binds actual capture events and browser media."""

from datetime import datetime, timezone

import pytest

from self_improving.harness.schemas.genesis_scene_execution_v2 import GenesisSceneCaptureEventV2
from tests.self_improving.harness.test_genesis_scene_execution import reference


def test_capture_event_requires_actual_utc_timestamp_and_png_identity():
    image = reference(None)
    image["media_type"] = "image/png"
    payload = dict(
        step=0,
        time_s=0.0,
        captured_at=datetime.now(timezone.utc),
        pixels_sha256="a" * 64,
        width=640,
        height=480,
        image=image,
    )
    assert GenesisSceneCaptureEventV2.model_validate(payload).step == 0
    payload["captured_at"] = datetime.now()
    with pytest.raises(ValueError):
        GenesisSceneCaptureEventV2.model_validate(payload)


@pytest.mark.parametrize(
    "key,value",
    [
        ("step", True),
        ("time_s", float("nan")),
        ("width", 0),
        ("height", -1),
        ("pixels_sha256", "bad"),
        ("captured_at", "2026-09-11T08:00:00+01:00"),
    ],
)
def test_capture_rejects_invalid_observed_values(key, value):
    image = reference(None)
    image["media_type"] = "image/png"
    payload = dict(
        step=0,
        time_s=0.0,
        captured_at=datetime.now(timezone.utc),
        pixels_sha256="a" * 64,
        width=640,
        height=480,
        image=image,
    )
    payload[key] = value
    with pytest.raises(ValueError):
        GenesisSceneCaptureEventV2.model_validate(payload)


def test_only_fixed_producer_can_construct_live_v2_capability():
    from self_improving.harness.genesis_scene_execution_v2 import LiveGenesisSceneExecutionV2

    with pytest.raises(TypeError, match="fixed producer"):
        LiveGenesisSceneExecutionV2(None, None)


def test_invalid_request_preserves_diagnostic_without_execution(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v2 import (
        GenesisSceneExecutionError,
        GenesisSceneExecutionProducerV2,
    )
    from self_improving.harness.schemas.common import ArtifactRef

    store = LocalArtifactStore(tmp_path / "cas")
    ref = ArtifactRef.model_validate(reference("harness.genesis_scene_execution_request.v2"))
    producer = GenesisSceneExecutionProducerV2(store, None, None)
    with pytest.raises(GenesisSceneExecutionError) as failure:
        producer.produce(ref, roots={})
    diagnostic = store.resolve(failure.value.diagnostic_ref).path.read_text()
    assert ref.sha256 in diagnostic
    assert "error_type" in diagnostic
    assert '"launch_result":null' in diagnostic


def test_retained_index_requires_logical_string_order(tmp_path):
    import json

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v2 import read_retained_scene_index

    store = LocalArtifactStore(tmp_path / "cas")
    member = tmp_path / "bytes"
    member.write_bytes(b"real retained member")
    refs = [
        store.put_file(
            member, name=name, media_type="application/octet-stream", schema_version=None
        )
        for name in ("baseline-job.json", "baseline/frames/frame.png")
    ]
    index_path = tmp_path / "index.json"

    def publish(items):
        index_path.write_text(
            json.dumps(
                dict(
                    files=[x.model_dump(mode="json") for x in items],
                    execution_authority=False,
                    qualified=False,
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return store.put_file(
            index_path, name="index.json", media_type="application/json", schema_version=None
        )

    assert tuple(read_retained_scene_index(store, publish(refs))) == tuple(x.name for x in refs)
    with pytest.raises(ValueError, match="sorted and unique"):
        read_retained_scene_index(store, publish(list(reversed(refs))))
    with pytest.raises(ValueError, match="sorted and unique"):
        read_retained_scene_index(store, publish(refs + refs))


@pytest.mark.parametrize(
    "change", ["authority", "extra", "noncanonical", "escape", "absolute", "missing"]
)
def test_retained_index_rejects_invalid_closure(tmp_path, change):
    import json

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v2 import read_retained_scene_index

    store = LocalArtifactStore(tmp_path / "cas")
    source = tmp_path / "member"
    source.write_bytes(b"retained data")
    ref = store.put_file(
        source, name="member", media_type="application/octet-stream", schema_version=None
    )
    member = ref.model_dump(mode="json")
    payload = dict(files=[member], execution_authority=False, qualified=False)
    if change == "authority":
        payload["execution_authority"] = True
    elif change == "extra":
        payload["extra"] = None
    elif change == "escape":
        member["name"] = "../member"
    elif change == "absolute":
        member["name"] = "/member"
    elif change == "missing":
        store.resolve(ref).path.unlink()
    source.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + ("\n" if change == "noncanonical" else "")
    )
    index = store.put_file(
        source, name="index.json", media_type="application/json", schema_version=None
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        read_retained_scene_index(store, index)

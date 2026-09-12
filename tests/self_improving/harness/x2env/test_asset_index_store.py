"""The sole Store persists immutable registry records without a second database."""

import pytest

from self_improving.harness.x2env.store import Store


def test_asset_index_is_immutable_and_queryable_after_restart(tmp_path):
    store = Store(tmp_path)
    store.register_asset("a" * 64, "mouse-one", "mouse", '{"record":"first"}')
    again = Store(tmp_path)
    assert again.asset_version("a" * 64) == '{"record":"first"}'
    assert again.asset_versions("mouse") == ('{"record":"first"}',)
    assert again.asset_versions("cabinet") == ()
    again.register_asset("a" * 64, "mouse-one", "mouse", '{"record":"first"}')
    with pytest.raises(ValueError, match="immutable"):
        again.register_asset("a" * 64, "mouse-one", "mouse", '{"record":"changed"}')
    with pytest.raises(KeyError):
        again.asset_version("b" * 64)

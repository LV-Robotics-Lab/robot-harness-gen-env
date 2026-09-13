"""The sole Store persists immutable registry records without a second database."""

import pytest

from self_improving.harness.x2env.store import Store


def test_category_vocabulary_is_distinct_sorted_bounded_after_restart(tmp_path):
    store = Store(tmp_path)
    for key, category in enumerate(("mouse", "box", "mouse")):
        store.register_asset(str(key), str(key), category, "opaque")
    assert Store(tmp_path).asset_categories(limit=2) == ("box", "mouse")
    with pytest.raises(ValueError, match="catalog_category_limit"):
        store.asset_categories(limit=1)
    for invalid in (0, 129, True, 1.5):
        with pytest.raises(ValueError, match="invalid category limit"):
            store.asset_categories(limit=invalid)


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

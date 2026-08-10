"""Tests for lib/a5_alias.py: query alias expansion + LLM semantic screening
(degraded/no-API mode). All LLM behavior is exercised via injected fake
functions -- zero real network calls."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import a5_alias as a5


# ---- load_alias_cache / save_alias_cache ----


def test_load_alias_cache_missing_file_returns_empty_dict(tmp_path):
    assert a5.load_alias_cache(tmp_path / "nope.json") == {}


def test_save_and_load_alias_cache_roundtrip(tmp_path):
    path = tmp_path / "aliases.json"
    a5.save_alias_cache(path, {"milktea": ["milk tea", "boba"]})
    assert a5.load_alias_cache(path) == {"milktea": ["milk tea", "boba"]}
    text = path.read_text()
    assert "\n" in text  # pretty-printed, not a single line


# ---- compound_split ----


def test_compound_split_lowercase_compound_found_in_wordlist():
    assert a5.compound_split("milktea") == ["milk tea"]


def test_compound_split_underscore_separator():
    assert a5.compound_split("top_cap") == ["top cap"]


def test_compound_split_camel_case():
    assert a5.compound_split("milkTea") == ["milk tea"]


def test_compound_split_all_uppercase_no_signal_returns_empty():
    # No separator, no lower->upper transition, and not a pure-lowercase
    # compound (the wordlist heuristic only fires on lowercase input) ->
    # dumb and predictable: no split.
    assert a5.compound_split("TOPCAP") == []


def test_compound_split_no_wordlist_match_returns_empty():
    assert a5.compound_split("randomword") == []


def test_compound_split_too_short_returns_empty():
    assert a5.compound_split("cup") == []


# ---- expand_terms ----


def test_expand_terms_cache_hit_does_not_call_llm():
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        return "[]"

    cache = {"milktea": ["milk tea", "bubble tea", "boba"]}
    result = a5.expand_terms(
        "milktea", [], {"enabled": True}, llm_fn=fake_llm, cache=cache
    )
    assert result["source"] == "cache"
    assert result["added"] == ["milk tea", "bubble tea", "boba"]
    assert "milk tea" in result["terms"]
    assert calls == []


def test_expand_terms_llm_path_writes_back_to_cache(tmp_path):
    cache_path = tmp_path / "aliases.json"
    a5.save_alias_cache(cache_path, {})
    cache = a5.load_alias_cache(cache_path)
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        return json.dumps(["widget alias one", "widget alias two"])

    result = a5.expand_terms(
        "widget", [], {"enabled": True}, llm_fn=fake_llm, cache=cache
    )
    assert result["source"] == "llm"
    assert result["added"] == ["widget alias one", "widget alias two"]
    assert len(calls) == 1

    a5.save_alias_cache(cache_path, cache)
    reloaded = a5.load_alias_cache(cache_path)
    assert reloaded["widget"] == ["widget alias one", "widget alias two"]


def test_expand_terms_disabled_falls_back_to_split():
    result = a5.expand_terms("milktea", [], {"enabled": False}, cache={})
    assert result["source"] == "split"
    assert result["added"] == ["milk tea"]


def test_expand_terms_no_cache_no_llm_no_split_returns_none_source():
    result = a5.expand_terms("randomword", [], {"enabled": False}, cache={})
    assert result["source"] == "none"
    assert result["added"] == []
    assert result["terms"] == ["randomword"]


def test_expand_terms_dedups_preserves_order_and_lowercases():
    result = a5.expand_terms("Cup", ["Cup", "Mug"], {"enabled": False}, cache={})
    assert result["terms"] == ["cup", "mug"]


def test_expand_terms_missing_cfg_defaults_to_split_path():
    # cfg absent/empty -> no LLM path, still degrades cleanly.
    result = a5.expand_terms("milktea", [], {}, cache={})
    assert result["source"] == "split"


# ---- screen_candidates ----


class FakeCandidate:
    def __init__(self, candidate_id, name):
        self.candidate_id = candidate_id
        self.name = name


def test_screen_candidates_disabled_returns_empty_dict():
    result = a5.screen_candidates(
        "cup",
        [FakeCandidate("a:1", "cup.usd")],
        {"enabled": False},
        llm_fn=lambda p: "{}",
    )
    assert result == {}


def test_screen_candidates_marks_one_not_ok():
    def fake_llm(prompt):
        return json.dumps(
            {
                "a:1": {"ok": True, "reason": "matches"},
                "a:2": {"ok": False, "reason": "looks like a lamp, not a cup"},
            }
        )

    result = a5.screen_candidates(
        "cup",
        [FakeCandidate("a:1", "cup.usd"), FakeCandidate("a:2", "lamp.usd")],
        {"enabled": True},
        llm_fn=fake_llm,
    )
    assert result["a:1"]["ok"] is True
    assert result["a:2"]["ok"] is False
    assert "lamp" in result["a:2"]["reason"]


def test_screen_candidates_malformed_llm_output_returns_empty_dict():
    result = a5.screen_candidates(
        "cup",
        [FakeCandidate("a:1", "cup.usd")],
        {"enabled": True},
        llm_fn=lambda p: "not json",
    )
    assert result == {}


def test_screen_candidates_no_candidates_returns_empty_dict_without_calling_llm():
    calls = []
    result = a5.screen_candidates(
        "cup", [], {"enabled": True}, llm_fn=lambda p: calls.append(p) or "{}"
    )
    assert result == {}
    assert calls == []


# ---- default_llm_fn ----


def test_default_llm_fn_returns_none_when_disabled():
    cfg = {"enabled": False, "api_key_file": "/nonexistent/should/not/be/read"}
    assert a5.default_llm_fn(cfg) is None


def test_default_llm_fn_returns_none_when_key_file_missing():
    cfg = {"enabled": True, "api_key_file": "/nonexistent/should/not/be/read"}
    assert a5.default_llm_fn(cfg) is None

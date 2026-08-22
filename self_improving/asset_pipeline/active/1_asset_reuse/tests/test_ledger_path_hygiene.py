"""Public-repo path hygiene for tracked ledgers + portable-uri contract.

Companion to test_runtime_config.test_active_sources_do_not_embed_personal_
home_paths (sources) and tests/test_public_evidence_redaction.py on the repo
main (evidence logs): tracked ledgers must not leak /home/<user> paths, and
uris must resolve machine-independently via lib.ledger's ACTIVE_ROOT anchor.
"""

import sys
from pathlib import Path

ACTIVE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ACTIVE_ROOT / "1_asset_reuse"))

from lib import ledger  # noqa: E402

FORBIDDEN = ("/home/", "/Users/")


def _tracked_ledgers():
    for root in ("data/asset_library", "data/upstream_ledgers"):
        yield from sorted((ACTIVE_ROOT / root).glob("*/ledger.json"))


def test_tracked_ledgers_contain_no_personal_home_paths():
    offenders = []
    for lp in _tracked_ledgers():
        text = lp.read_text(encoding="utf-8")
        if any(marker in text for marker in FORBIDDEN):
            offenders.append(str(lp))
    assert not offenders, f"local home paths in tracked ledgers: {offenders}"


def test_portable_uri_round_trips_under_active_root():
    here = Path(__file__).resolve()
    portable = ledger.to_portable_uri(here)
    assert not portable.startswith("/"), portable
    assert ledger.resolve_uri(portable).resolve() == here


def test_resolve_uri_leaves_absolute_paths_alone():
    assert ledger.resolve_uri("/tmp/x.glb") == Path("/tmp/x.glb")


def test_to_portable_uri_keeps_paths_outside_the_tree_absolute():
    outside = "/somewhere/else/mesh.glb"
    assert ledger.to_portable_uri(outside) == outside

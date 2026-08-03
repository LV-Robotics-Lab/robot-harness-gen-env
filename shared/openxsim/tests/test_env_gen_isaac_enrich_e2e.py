"""e2e: env-gen scene -> IR -> enrich isaacsim USD -> IsaacSimCompiler compiles.

Proves the junction closes the DoD's cross-sim path: attaching a USD
representation to the env-gen IR makes IsaacSimCompiler stop reporting
"no existing USD representation" and emit a scene.usda. A minimal stub .usd is
used (the compiler only needs the file to exist to reference it); the real
GLB->USD conversion is the asset pipeline's job, exercised separately.
"""

import sys
from pathlib import Path


HERE = Path(__file__).resolve()
OX = HERE.parents[1]  # shared/openxsim
DEV = HERE.parents[3]  # env-gen-dev
for _p in (
    OX / "source/agenticsim",
    OX / "deps/metasim_core",
    OX / "third_party/MetaSim",
    DEV / "2_sim_migration" / "lib",
):
    sys.path.insert(0, str(_p))

from agenticsim.openxsim.backends import IsaacSimCompiler  # noqa: E402
from agenticsim.openxsim.env_gen import import_env_gen  # noqa: E402
from usd_enrich import enrich_isaac_usd  # noqa: E402

FIX = HERE.parent / "fixtures" / "env_gen" / "bottle_on_table.resolved_scene.json"


def _stub_usd(path: Path) -> Path:
    path.write_text('#usda 1.0\ndef "root" {}\n')
    return path


def test_enrich_unblocks_isaac_compile(tmp_path):
    pkg = import_env_gen(FIX)
    # baseline: env-gen assets carry only SAPIEN meshes -> Isaac blocks on USD
    r0 = IsaacSimCompiler().compile(pkg, tmp_path / "before")
    assert any("USD" in str(b) for b in r0.blockers)

    usd = _stub_usd(tmp_path / "bottle.usd")
    enriched = enrich_isaac_usd(pkg, {("001_bottle", 0): str(usd)})
    rep = enriched.assets[0].representation_for("isaacsim", ("usd", "usda", "usdc"))
    assert rep is not None and rep.uri == str(usd)

    # with the USD registered, Isaac compiles cleanly and references it
    r1 = IsaacSimCompiler().compile(enriched, tmp_path / "after")
    assert r1.status == "compiled"
    assert not r1.blockers
    assert "bottle.usd" in Path(r1.artifact_path).read_text()


def test_enrich_leaves_unmatched_assets_untouched(tmp_path):
    pkg = import_env_gen(FIX)
    enriched = enrich_isaac_usd(
        pkg, {("nonexistent", 0): str(_stub_usd(tmp_path / "x.usd"))}
    )
    assert enriched.assets[0].representation_for("isaacsim", ("usd",)) is None

"""e2e: env-gen scene -> IR -> enrich isaacsim USD -> IsaacSimCompiler compiles.

Proves the junction closes the DoD's cross-sim path: attaching a USD
representation to the env-gen IR makes IsaacSimCompiler stop reporting
"no existing USD representation" and emit a scene.usda. A minimal stub .usd is
used (the compiler only needs the file to exist to reference it); the real
GLB->USD conversion is the asset pipeline's job, exercised separately.

Also guards the two defects found while making the render actually work:
  * Bug 1 (openxsim IsaacSimCompiler): quatd xformOp:orient was emitted as a
    nested tuple ``(w, (x, y, z))`` -> invalid USD, unopenable. Must be flat.
  * Bug 2 (this junction): a baked USD carries mesh_scale, so re-applying the
    object's scale double-scaled it. enrich must neutralize scale to identity.
"""

import re
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


def test_enrich_neutralizes_scale_for_baked_usd(tmp_path):
    """Bug 2: a matched (baked) USD -> object scale becomes identity; an
    unmatched asset keeps its original mesh_scale (SAPIEN path still needs it)."""
    pkg = import_env_gen(FIX)
    original = pkg.env.objects[0].scale
    assert original != (1.0, 1.0, 1.0)  # env-gen bottle carries a real mesh_scale

    usd = _stub_usd(tmp_path / "bottle.usd")
    enriched = enrich_isaac_usd(pkg, {("001_bottle", 0): str(usd)})
    assert enriched.env.objects[0].scale == (1.0, 1.0, 1.0)

    # no match -> scale preserved
    untouched = enrich_isaac_usd(pkg, {("nonexistent", 0): str(usd)})
    assert untouched.env.objects[0].scale == original


def test_compiled_usda_quat_flat_and_scale_identity(tmp_path):
    """Bug 1 + Bug 2 as they surface in the emitted scene.usda text (no pxr needed):
    quatd orient is a flat 4-tuple, and the baked object's scale line is identity."""
    pkg = import_env_gen(FIX)
    usd = _stub_usd(tmp_path / "bottle.usd")
    enriched = enrich_isaac_usd(pkg, {("001_bottle", 0): str(usd)})
    r = IsaacSimCompiler().compile(enriched, tmp_path / "out")
    text = Path(r.artifact_path).read_text()

    m = re.search(r"quatd xformOp:orient = \(([^\n]*)\)", text)
    assert m, "no quatd orient found in scene.usda"
    inner = m.group(1)
    assert "(" not in inner and ")" not in inner, f"nested quat tuple: {inner!r}"
    assert len(inner.split(",")) == 4, f"quat is not a flat 4-tuple: {inner!r}"

    assert "xformOp:scale = (1.0, 1.0, 1.0)" in text

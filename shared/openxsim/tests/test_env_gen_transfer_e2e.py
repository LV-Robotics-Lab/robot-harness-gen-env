from __future__ import annotations

from pathlib import Path

from agenticsim.openxsim.backends import IsaacSimCompiler
from agenticsim.openxsim.importers import import_environment

FIX = (
    Path(__file__).parent / "fixtures" / "env_gen" / "can_on_plate.resolved_scene.json"
)


def test_env_gen_ir_compiles_to_isaac_reports_missing_usd(tmp_path: Path) -> None:
    # env-gen resolved scene -> IR, via the auto-routing dispatcher
    package = import_environment(FIX)
    package.validate()

    # IR -> Isaac Sim backend, non-strict so we can inspect blockers instead of raising
    result = IsaacSimCompiler().compile(package, tmp_path / "isaac")

    # Both can_1 and plate_1 are mesh (.glb) assets with no USD representation,
    # so Isaac must honestly report a blocker for each rather than silently
    # degrading or dropping them.
    assert result.status == "partial"
    assert len(result.blockers) == 2
    assert all("USD" in blocker for blocker in result.blockers)
    ids = {o.instance_id for o in package.env.objects}
    reported = {blocker.split(":", 1)[0] for blocker in result.blockers}
    assert reported == ids

    # The compiled artifact still exists (compilation does not crash on the
    # blocker; it emits an explicit placeholder instead).
    assert Path(result.artifact_path).is_file()

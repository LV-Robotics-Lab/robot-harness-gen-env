from pathlib import Path

from self_improving import MODULES, audit_repository


def test_registry_has_unique_names_and_paths() -> None:
    assert len({module.name for module in MODULES}) == len(MODULES)
    assert len({module.path for module in MODULES}) == len(MODULES)
    assert {module.name for module in MODULES} >= {
        "gen_env_core",
        "harness_mvp",
        "stage5",
        "alchedata",
        "asset_pipeline",
        "pearl_evidence_portal",
        "robotwin_text2env_alt_archive",
        "openreal2sim",
        "digital_cousins",
    }

    archive = next(module for module in MODULES if module.name == "robotwin_text2env_alt_archive")
    assert archive.required is False
    assert archive.mutable is False


def test_audit_is_read_only_and_reports_uninitialized_submodules(tmp_path: Path) -> None:
    for module in MODULES:
        if module.required:
            (tmp_path / module.path).mkdir(parents=True)

    report = audit_repository(tmp_path)

    assert report["ready"] is False
    assert set(report["required_failures"]) == {"openreal2sim", "digital_cousins"}
    statuses = {module["name"]: module["status"] for module in report["modules"]}
    assert statuses["gen_env_core"] == "ready"
    assert statuses["openreal2sim"] == "uninitialized"

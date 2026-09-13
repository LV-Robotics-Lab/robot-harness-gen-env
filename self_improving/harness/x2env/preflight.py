"""Read-only deployment check.

Reports which components are configured and whether their pins and paths resolve.
It never reads credential contents, loads models, or touches the state directory.
"""

import json
from pathlib import Path

from .deployment import Deployment

SCHEMA_VERSION = "x2env.deployment_check.v1"


def _path_state(value):
    return "present" if Path(value).exists() else "missing"


def _pinned_state(pinned):
    path = Path(pinned.path)
    if not path.exists():
        return "missing"
    try:
        pinned.read()
        return "passed"
    except (OSError, ValueError):
        return "failed"


def _component(status, checks=None, **extra):
    return {"status": status, "checks": checks or {}, **extra}


def _status_from(checks):
    if any(state in {"missing", "failed"} for state in checks.values()):
        return "misconfigured"
    return "configured"


def check_deployment(config) -> dict:
    config = Deployment.model_validate(config)
    components = {}
    missing = []

    components["local"] = _component("configured" if config.local_enabled else "not_configured")

    if config.codex is None:
        components["codex"] = _component("not_configured")
    else:
        checks = {"executable": _path_state(config.codex.executable)}
        if config.codex.router is not None:
            checks["router_credential"] = _path_state(config.codex.router.api_key_file)
        components["codex"] = _component(_status_from(checks), checks, model=config.codex.model)
        if checks["executable"] == "missing":
            missing.append("codex.executable")
        if checks.get("router_credential") == "missing":
            missing.append("codex.router.api_key_file")

    if config.genesis is None:
        components["genesis"] = _component("not_configured")
    else:
        checks = {
            f"runtime_roots.{name}": _path_state(value)
            for name, value in config.genesis.runtime_roots.model_dump().items()
        }
        components["genesis"] = _component(
            _status_from(checks), checks, denied_roots=len(config.genesis.denied_roots)
        )
        missing.extend(f"genesis.{k}" for k, v in checks.items() if v == "missing")

    if config.web is None:
        components["web"] = _component("not_configured")
    else:
        checks = {"provider_config": _pinned_state(config.web.provider_config)}
        providers = []
        if checks["provider_config"] == "passed":
            try:
                providers = sorted(
                    json.loads(Path(config.web.provider_config.path).read_bytes())
                    .get("providers", {})
                    .keys()
                )
            except (ValueError, AttributeError):
                checks["provider_config"] = "failed"
        if config.web.license_records is not None:
            checks["license_records"] = _pinned_state(config.web.license_records)
        components["web"] = _component(_status_from(checks), checks, providers=providers)
        missing.extend(f"web.{k}" for k, v in checks.items() if v == "missing")

    if config.reconstruction is None:
        components["reconstruction"] = _component("not_configured")
    else:
        rec = config.reconstruction
        checks = {
            "source_root": _path_state(rec.source_root),
            "python": _path_state(rec.python),
            "segmentation_runtime": _pinned_state(rec.segmentation_runtime),
            "reconstruction_runtime": _pinned_state(rec.reconstruction_runtime),
            "model_refs": "present" if rec.model_refs else "missing",
            "derivation_authorization": (
                "present" if rec.derivation_authorization is not None else "missing"
            ),
        }
        components["reconstruction"] = _component(
            _status_from(checks), checks, source_commit=rec.source_commit
        )
        missing.extend(f"reconstruction.{k}" for k, v in checks.items() if v == "missing")

    return {
        "schema_version": SCHEMA_VERSION,
        "state_dir": config.state_dir,
        "timeout_seconds": config.timeout_seconds,
        "components": components,
        "missing": sorted(missing),
        "ok": all(c["status"] != "misconfigured" for c in components.values()),
    }

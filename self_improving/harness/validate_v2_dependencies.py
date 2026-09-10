"""Exact dependency identity for the legacy ``text2env.validate@2.0.0`` verifier."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from .schemas.common import DependencyRef

TEXT2ENV_VALIDATE_V2_SKILL_REF = "text2env.validate@2.0.0"
VALIDATE_V2_POLICY_DEPENDENCY = "text2env.validate.v2.policy"
_DISTRIBUTION_ROOT = Path(__file__).resolve().parents[2]
_IDENTITY_PATHS = (
    "self_improving/harness/handlers/text2env_validate_v2.py",
    "self_improving/harness/schemas/text2env_validate_v2.py",
    "self_improving/harness/validate_v2.py",
    "self_improving/harness/validate_v2_dependencies.py",
    "self_improving/validate_v2_snapshot.py",
)


def validate_v2_policy_sha256(*, distribution_root: Path = _DISTRIBUTION_ROOT) -> str:
    """Hash the exact deterministic validation implementation closure."""

    root = distribution_root.expanduser().resolve(strict=True)
    records: list[dict[str, object]] = []
    for relative in _IDENTITY_PATHS:
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise ValueError(f"validate v2 implementation path is unsafe: {relative}")
        payload = path.read_bytes()
        records.append(
            {
                "path": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    canonical = json.dumps(
        {"domain": "harness.text2env.validate.v2.policy", "files": records},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class Text2EnvValidateV2DependencyResolver:
    """Return the one source-bound dependency used by every v2 invocation."""

    distribution_root: Path = _DISTRIBUTION_ROOT

    def resolve(
        self,
        skill_ref: str,
        effective_parameters: BaseModel,
    ) -> tuple[DependencyRef, ...]:
        if skill_ref != TEXT2ENV_VALIDATE_V2_SKILL_REF:
            return ()
        del effective_parameters
        return (
            DependencyRef(
                name=VALIDATE_V2_POLICY_DEPENDENCY,
                version="2",
                sha256=validate_v2_policy_sha256(distribution_root=self.distribution_root),
            ),
        )


__all__ = [
    "TEXT2ENV_VALIDATE_V2_SKILL_REF",
    "VALIDATE_V2_POLICY_DEPENDENCY",
    "Text2EnvValidateV2DependencyResolver",
    "validate_v2_policy_sha256",
]

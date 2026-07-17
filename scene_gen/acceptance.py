"""Acceptance aggregation shared by local and runtime scene batches."""

from __future__ import annotations

from typing import Any


def summarize_acceptance(
    outcomes: list[dict[str, Any]], *, minimum_pass_rate: float = 0.95
) -> dict[str, Any]:
    if not 0.0 <= minimum_pass_rate <= 1.0:
        raise ValueError("minimum_pass_rate must be in [0, 1]")
    total = len(outcomes)
    passed = sum(item.get("status") == "pass" for item in outcomes)
    pass_rate = passed / total if total else 0.0
    return {
        "status": "pass" if total and pass_rate >= minimum_pass_rate else "fail",
        "total": total,
        "pass_count": passed,
        "fail_count": total - passed,
        "pass_rate": pass_rate,
        "minimum_pass_rate": minimum_pass_rate,
    }

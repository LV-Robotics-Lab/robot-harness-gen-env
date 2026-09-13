"""Reproducible case planning for the compile-chain acceptance campaign."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

AcceptanceGroup = Literal["fresh", "existing_reuse", "digital_cousins"]


class CampaignAcceptanceError(ValueError):
    """The campaign output does not prove the requested compile behavior."""


@dataclass(frozen=True, slots=True)
class AcceptanceCase:
    case_id: str
    group: AcceptanceGroup
    request: str
    seed: int
    expected_origin: str
    source_case_id: str | None = None


_FRESH_SHAPES = (
    "hexagonal pedestal",
    "octagonal plinth",
    "cylindrical column",
    "triangular prism",
    "ribbed obelisk",
    "rounded caddy",
    "tapered stand",
    "low riser",
    "faceted vessel",
    "square marker",
    "arched holder",
    "grooved puck",
    "stout tower",
    "wide capsule",
    "slender bollard",
)
_COLORS = ("blue", "green", "orange", "pink", "purple", "red", "white", "yellow")
_MATERIALS = ("ceramic", "glass", "metal", "plastic", "wooden")
def build_acceptance_plan(*, seed: int) -> tuple[AcceptanceCase, ...]:
    """Return a deterministic 10 fresh + 5 reuse + 5 Digital Cousins plan."""

    rng = random.Random(seed)
    shapes = rng.sample(_FRESH_SHAPES, 10)
    fresh: list[AcceptanceCase] = []
    for index, shape in enumerate(shapes, start=1):
        color = rng.choice(_COLORS)
        material = rng.choice(_MATERIALS)
        fresh.append(
            AcceptanceCase(
                case_id=f"fresh-{index:02d}",
                group="fresh",
                request=f"Place a {color} {material} {shape} on the table.",
                seed=rng.randrange(1, 2_147_483_647),
                expected_origin="procedural_generated",
            )
        )

    reuse = [
        AcceptanceCase(
            case_id=f"reuse-{index:02d}",
            group="existing_reuse",
            request=source.request,
            seed=rng.randrange(1, 2_147_483_647),
            expected_origin="accepted_library",
            source_case_id=source.case_id,
        )
        for index, source in enumerate(fresh[:5], start=1)
    ]
    digital_cousins: list[AcceptanceCase] = []
    for index, source in enumerate(fresh[5:10], start=1):
        words = source.request.removeprefix("Place a ").removesuffix(" on the table.").split()
        shape = " ".join(words[2:])
        digital_cousins.append(
            AcceptanceCase(
                case_id=f"digital-cousins-{index:02d}",
                group="digital_cousins",
                request=f"Place a {shape} on the table.",
                seed=rng.randrange(1, 2_147_483_647),
                expected_origin="accepted_library_task_affordance_cousin",
                source_case_id=source.case_id,
            )
        )
    return tuple((*fresh, *reuse, *digital_cousins))


def validate_campaign_summary(summary: dict[str, object]) -> None:
    """Fail closed unless 20 compile runs prove complete environment delivery."""

    raw_cases = summary.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != 20:
        raise CampaignAcceptanceError("campaign must contain exactly 20 cases")
    groups: dict[str, list[dict[str, object]]] = {
        "fresh": [],
        "existing_reuse": [],
        "digital_cousins": [],
    }
    for raw_record in raw_cases:
        if not isinstance(raw_record, dict):
            raise CampaignAcceptanceError("campaign case must be an object")
        case = raw_record.get("case")
        group = case.get("group") if isinstance(case, dict) else None
        if group not in groups:
            raise CampaignAcceptanceError(f"unknown campaign group: {group!r}")
        groups[str(group)].append(raw_record)
        if raw_record.get("status") != "succeeded":
            raise CampaignAcceptanceError("every compile case must succeed")
        if raw_record.get("portable_receipt_verified_from_destination_cas_only") is not True:
            raise CampaignAcceptanceError("every run receipt must verify from destination CAS")
        environment = raw_record.get("environment")
        if not isinstance(environment, dict):
            raise CampaignAcceptanceError("every case must materialize an environment package")
        digest = environment.get("resolved_scene_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise CampaignAcceptanceError("environment must carry its resolved scene digest")
        load_smoke = environment.get("load_smoke")
        if not isinstance(load_smoke, dict) or load_smoke.get("status") != "loaded":
            raise CampaignAcceptanceError("compiled environment must pass a simulator load smoke")
        if load_smoke.get("resolved_scene_sha256") != digest:
            raise CampaignAcceptanceError("load smoke is not bound to the resolved scene")
        media = raw_record.get("media")
        if not isinstance(media, dict) or media.get("kind") != "compiled_environment_preview":
            raise CampaignAcceptanceError("every case must provide a compiled environment preview")
        if media.get("resolved_scene_sha256") != digest:
            raise CampaignAcceptanceError("environment media is not bound to the resolved scene")
        if not media.get("preview") or not media.get("video"):
            raise CampaignAcceptanceError("environment media must include image and video")

    if {group: len(records) for group, records in groups.items()} != {
        "fresh": 10,
        "existing_reuse": 5,
        "digital_cousins": 5,
    }:
        raise CampaignAcceptanceError("campaign group counts do not match 10 + 5 + 5")
    for record in groups["existing_reuse"]:
        reuse = record.get("reuse_verification")
        if not isinstance(reuse, dict) or reuse.get("same_asset_id") is not True:
            raise CampaignAcceptanceError("existing reuse did not select the same asset id")
    for record in groups["digital_cousins"]:
        selection = record.get("digital_cousin_selection")
        if (
            not isinstance(selection, dict)
            or selection.get("status") != "selected"
            or selection.get("exact_request_match") is not False
            or not selection.get("selected_asset_id")
            or not selection.get("source_case_id")
        ):
            raise CampaignAcceptanceError("every Digital Cousin case needs a real selection record")

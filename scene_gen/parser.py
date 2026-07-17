"""Bounded bilingual parser that never emits code, paths, ids, or poses."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .schema import RelationSpec, RelationType, SceneObjectSpec, SceneSpec, SceneSpecError


class StructuredSceneProvider(Protocol):
    def parse_scene(self, *, request: str, seed: int) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Mention:
    category: str
    surface: str
    start: int
    end: int
    object_id: str


OBJECT_TERMS: dict[str, tuple[str, ...]] = {
    "apple": ("apple", "苹果"),
    "basket": ("basket", "篮子", "筐"),
    "block": ("block", "cube", "方块", "积木"),
    "bottle": ("bottle", "瓶子", "瓶"),
    "bowl": ("bowl", "碗"),
    "calculator": ("calculator", "计算器"),
    "cabinet": ("cabinet", "drawer cabinet", "柜子", "抽屉柜"),
    "can": ("cola can", "soda can", "can", "可乐罐", "罐子", "易拉罐"),
    "cup": ("coffee mug", "mug", "cup", "马克杯", "杯子", "杯"),
    "box": ("storage box", "box", "盒子", "箱子"),
    "hammer": ("hammer", "锤子", "锤"),
    "knife": ("knife", "刀子", "刀"),
    "laptop": ("laptop", "notebook computer", "笔记本电脑", "电脑"),
    "microwave": ("microwave oven", "microwave", "微波炉"),
    "oven": ("oven", "烤箱"),
    "plate": ("plate", "dish", "盘子", "盘"),
    "remote_control": ("remote control", "remote", "遥控器"),
    "tray": ("tray", "托盘"),
    "vegetable": ("vegetable", "veggie", "蔬菜"),
}

COLOR_TERMS: dict[str, tuple[str, ...]] = {
    "black": ("black", "黑色", "黑"),
    "blue": ("blue", "蓝色", "蓝"),
    "brown": ("brown", "棕色", "棕"),
    "green": ("green", "绿色", "绿"),
    "orange": ("orange", "橙色", "橙"),
    "pink": ("pink", "粉色", "粉"),
    "purple": ("purple", "紫色", "紫"),
    "red": ("red", "红色", "红"),
    "white": ("white", "白色", "白"),
    "yellow": ("yellow", "黄色", "黄"),
}

MATERIAL_TERMS: dict[str, tuple[str, ...]] = {
    "ceramic": ("ceramic", "陶瓷"),
    "glass": ("glass", "玻璃"),
    "metal": ("metal", "metallic", "金属"),
    "plastic": ("plastic", "塑料"),
    "wood": ("wooden", "wood", "木质", "木制"),
}

REGION_TERMS: dict[str, tuple[str, ...]] = {
    "center": ("center", "centre", "middle", "中央", "中心", "中间"),
    "left": ("left region", "left side of the table", "桌面左侧", "桌子左侧"),
    "right": ("right region", "right side of the table", "桌面右侧", "桌子右侧"),
    "front": ("front region", "front of the table", "桌面前部", "桌子前部"),
    "back": ("back region", "back of the table", "桌面后部", "桌子后部"),
}

FORBIDDEN_PROMPT_PATTERNS = (
    (r"```|\b(?:import|exec|eval)\s*\(|\bdef\s+[a-z_]", "executable code"),
    (r"(?:^|\s)(?:/[^\s]+|~\/[^\s]+|[a-zA-Z]:\\[^\s]+)", "filesystem path"),
    (r"\b(?:asset_id|model_id|qpos|quaternion|wxyz|world_xyz)\b", "backend field"),
    (r"\b[xyz]\s*=\s*-?\d", "world coordinate"),
    (r"\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d", "coordinate tuple"),
)

UNSUPPORTED_FEATURE_PATTERNS = (
    (r"\bbetween\b|两者之间|中间对齐", "between"),
    (r"\balign(?:ed|ment)?\b|对齐", "alignment"),
)

ARTICULATED_CATEGORIES = {"box", "cabinet", "laptop", "microwave", "oven"}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def _language(text: str) -> str:
    cjk = _contains_cjk(text)
    latin = bool(re.search(r"[a-zA-Z]", text))
    return "mixed" if cjk and latin else "zh" if cjk else "en"


def _scene_id(request: str) -> str:
    words = re.findall(r"[a-z0-9]+", request.lower())[:8]
    digest = hashlib.sha256(request.encode("utf-8")).hexdigest()[:10]
    stem = "_".join(words) if words else "scene"
    if not stem[0].isalpha():
        stem = f"scene_{stem}"
    return f"{stem}_{digest}"[:96]


def validate_prompt_boundary(request: str) -> None:
    normalized = _normalize(request)
    for pattern, label in FORBIDDEN_PROMPT_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            raise SceneSpecError(f"prompt contains forbidden {label}")
    for pattern, feature in UNSUPPORTED_FEATURE_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            raise SceneSpecError(f"unsupported MVP scene feature: {feature}")


def _term_pattern(term: str) -> str:
    escaped = re.escape(term)
    if re.search(r"[a-z0-9]", term):
        return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    return escaped


def extract_mentions(request: str) -> list[Mention]:
    normalized = _normalize(request)
    candidates: list[tuple[int, int, str, str]] = []
    for category, terms in OBJECT_TERMS.items():
        for term in sorted(terms, key=len, reverse=True):
            for match in re.finditer(_term_pattern(term), normalized, flags=re.IGNORECASE):
                candidates.append((match.start(), match.end(), category, match.group(0)))
    generic_patterns = (
        r"\b(?:place|put|add|create|generate|stack)\s+(?:a|an|the)\s+(?P<object>[a-z][a-z0-9_-]*(?:\s+[a-z][a-z0-9_-]*){0,3}?)(?=\s+(?:on\s+top\s+of|inside|into|on|near|to\s+the|in\s+front\s+of|behind)|[.,]|$)",
        r"\b(?:a|an)\s+(?P<object>[a-z][a-z0-9_-]*(?:\s+[a-z][a-z0-9_-]*){0,3}?)\s+(?:is\s+)?(?=on\s+top\s+of|inside|on\s+the\s+table|near|to\s+the|in\s+front\s+of|behind)",
    )
    attribute_terms = {
        term
        for lexicon in (COLOR_TERMS, MATERIAL_TERMS)
        for terms in lexicon.values()
        for term in terms
        if re.fullmatch(r"[a-z][a-z0-9_-]*", term)
    }
    for pattern in generic_patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            start, end = match.span("object")
            if any(not (end <= other_start or start >= other_end) for other_start, other_end, _, _ in candidates):
                continue
            words = [word for word in match.group("object").split() if word not in attribute_terms]
            if not words:
                continue
            category = re.sub(r"[^a-z0-9]+", "_", "_".join(words)).strip("_")[:64]
            if category:
                candidates.append((start, end, category, match.group("object")))
    selected: list[tuple[int, int, str, str]] = []
    for candidate in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]), item[2])):
        start, end, _, _ = candidate
        if any(not (end <= other_start or start >= other_end) for other_start, other_end, _, _ in selected):
            continue
        selected.append(candidate)
    selected.sort(key=lambda item: (item[0], item[1], item[2]))
    counts: dict[str, int] = {}
    mentions: list[Mention] = []
    for start, end, category, surface in selected:
        counts[category] = counts.get(category, 0) + 1
        mentions.append(
            Mention(
                category=category,
                surface=surface,
                start=start,
                end=end,
                object_id=f"{category}_{counts[category]}",
            )
        )
    if not mentions:
        raise SceneSpecError("no supported tabletop object found")
    return mentions


def _nearest_attribute(request: str, mention: Mention, lexicon: dict[str, tuple[str, ...]]) -> str | None:
    normalized = _normalize(request)
    window_start = max(0, mention.start - 24)
    window_end = min(len(normalized), mention.end + 4)
    window = normalized[window_start:window_end]
    matches: list[tuple[int, str]] = []
    for canonical, terms in lexicon.items():
        for term in terms:
            for match in re.finditer(_term_pattern(term), window, flags=re.IGNORECASE):
                absolute_end = window_start + match.end()
                if absolute_end <= mention.end:
                    matches.append((absolute_end, canonical))
    return max(matches, default=(0, None), key=lambda item: item[0])[1]


def _region_for(request: str, mention: Mention, next_start: int | None) -> str:
    normalized = _normalize(request)
    end = min(len(normalized), max(mention.end + 40, next_start or mention.end))
    window = normalized[max(0, mention.start - 12):end]
    for region, terms in REGION_TERMS.items():
        if any(re.search(_term_pattern(term), window, flags=re.IGNORECASE) for term in terms):
            return region
    return "center"


def _relation_between(request: str, first: Mention, second: Mention) -> RelationType | None:
    normalized = _normalize(request)
    between = normalized[first.end:second.start]
    after = normalized[second.end:min(len(normalized), second.end + 16)]
    combined = f"{between} {after}"
    if re.search(r"\b(?:inside|into|within)\b", between) or re.search(r"放进|装进|放入|置于.*(?:里面|内部)", between):
        return RelationType.INSIDE
    if re.search(r"\b(?:on\s+top\s+of|stacked?\s+(?:on|onto)|onto|on)\b", between) or re.search(r"叠在|堆在|放在", between):
        return RelationType.ON_TOP_OF
    if re.search(r"\b(?:to\s+the\s+)?left\s+of\b", between) or re.search(r"(?:的)?左边|左侧", after):
        return RelationType.LEFT_OF
    if re.search(r"\b(?:to\s+the\s+)?right\s+of\b", between) or re.search(r"(?:的)?右边|右侧", after):
        return RelationType.RIGHT_OF
    if re.search(r"\bin\s+front\s+of\b", between) or re.search(r"(?:的)?前方|前面", after):
        return RelationType.FRONT_OF
    if re.search(r"\bbehind\b", between) or re.search(r"(?:的)?后方|后面", after):
        return RelationType.BEHIND
    if re.search(r"\b(?:near|next\s+to|beside)\b", combined) or re.search(r"靠近|旁边|邻近", combined):
        return RelationType.NEAR
    return None


def _articulation_for(request: str, mention: Mention) -> dict[str, Any] | None:
    if mention.category not in ARTICULATED_CATEGORIES:
        return None
    normalized = _normalize(request)
    local = normalized[max(0, mention.start - 32):min(len(normalized), mention.end + 48)]
    if re.search(r"\b(?:half[ -]?open|partially\s+open|halfway\s+open)\b|打开一半|半开", local):
        return {
            "state": "partially_open",
            "open_fraction": 0.5,
            "joint_selector": "all_movable",
        }
    percentage = re.search(r"(?:open|opened)\s+(\d{1,3})\s*%|打开\s*(\d{1,3})\s*%", local)
    if percentage:
        fraction = float(next(group for group in percentage.groups() if group is not None)) / 100.0
        if not 0.0 < fraction < 1.0:
            raise SceneSpecError("articulation percentage must be between 1% and 99%")
        return {
            "state": "partially_open",
            "open_fraction": fraction,
            "joint_selector": "all_movable",
        }
    if re.search(r"\b(?:closed|shut)\b|关闭|闭合|关上", local):
        return {"state": "closed", "open_fraction": 0.0, "joint_selector": "all_movable"}
    if re.search(r"\b(?:open|opened)\b|打开|开启|开着", local):
        return {"state": "open", "open_fraction": 1.0, "joint_selector": "all_movable"}
    return None


def parse_rule_based(request: str, *, seed: int = 0) -> SceneSpec:
    validate_prompt_boundary(request)
    mentions = extract_mentions(request)
    objects: list[SceneObjectSpec] = []
    for index, mention in enumerate(mentions):
        next_start = mentions[index + 1].start if index + 1 < len(mentions) else None
        objects.append(
            SceneObjectSpec(
                object_id=mention.object_id,
                category=mention.category,
                color=_nearest_attribute(request, mention, COLOR_TERMS),
                material=_nearest_attribute(request, mention, MATERIAL_TERMS),
                region=_region_for(request, mention, next_start),
                articulation=_articulation_for(request, mention),
            )
        )
    pair_relations: list[RelationSpec] = []
    nested_sources: set[str] = set()
    for first_index, first in enumerate(mentions):
        for second in mentions[first_index + 1:]:
            relation = _relation_between(request, first, second)
            if relation is not None:
                pair_relations.append(
                    RelationSpec(
                        relation=relation,
                        source=first.object_id,
                        target=second.object_id,
                        max_distance_m=0.25 if relation == RelationType.NEAR else None,
                    )
                )
                if relation in {RelationType.ON_TOP_OF, RelationType.INSIDE}:
                    nested_sources.add(first.object_id)
    relations: list[RelationSpec] = [
        RelationSpec(relation=RelationType.ON_TABLE, source=item.object_id, target="table")
        for item in objects
        if item.object_id not in nested_sources
    ]
    relations.extend(pair_relations)

    normalized = _normalize(request)
    distance_match = re.search(r"(?:at\s+least|minimum|至少)\s*(\d+(?:\.\d+)?)\s*(m|meter|meters|米)", normalized)
    if distance_match:
        if len(mentions) < 2:
            raise SceneSpecError("distance constraint requires at least two objects")
        relations.append(
            RelationSpec(
                relation=RelationType.DISTANCE_AT_LEAST,
                source=mentions[0].object_id,
                target=mentions[1].object_id,
                min_distance_m=float(distance_match.group(1)),
            )
        )
    if len(mentions) >= 2 and re.search(r"(?:two objects|both objects|两件物体|两个物体|两者).{0,8}(?:near|close|靠近|相邻)", normalized):
        pair = {mentions[0].object_id, mentions[1].object_id}
        if not any(
            relation.relation == RelationType.NEAR and {relation.source, relation.target} == pair
            for relation in relations
        ):
            relations.append(
                RelationSpec(
                    relation=RelationType.NEAR,
                    source=mentions[0].object_id,
                    target=mentions[1].object_id,
                    max_distance_m=0.25,
                )
            )
    return SceneSpec(
        scene_id=_scene_id(request),
        request=request,
        language=_language(request),
        seed=seed,
        objects=tuple(objects),
        relations=tuple(relations),
    )


def parse_provider_payload(payload: dict[str, Any], *, request: str, seed: int) -> SceneSpec:
    if not isinstance(payload, dict):
        raise SceneSpecError("structured provider output must be a JSON object")
    candidate = dict(payload)
    candidate.setdefault("request", request)
    candidate.setdefault("seed", seed)
    candidate.setdefault("scene_id", _scene_id(request))
    candidate.setdefault("language", _language(request))
    spec = SceneSpec.model_validate(candidate)
    if spec.request != request:
        raise SceneSpecError("structured provider changed the user request")
    if spec.seed != seed:
        raise SceneSpecError("structured provider changed the deterministic seed")
    return spec


def parse_with_provider(provider: StructuredSceneProvider, *, request: str, seed: int = 0) -> SceneSpec:
    validate_prompt_boundary(request)
    return parse_provider_payload(provider.parse_scene(request=request, seed=seed), request=request, seed=seed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a bounded bilingual prompt into SceneSpec JSON.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    spec = parse_rule_based(args.prompt, seed=args.seed)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec.canonical_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"PASS scene_id={spec.scene_id} sha256={spec.digest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

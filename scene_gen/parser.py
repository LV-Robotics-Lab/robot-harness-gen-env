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
class MentionGroup:
    category: str
    surface: str
    start: int
    end: int
    group_id: int
    quantity: int
    object_ids: tuple[str, ...]


@dataclass(frozen=True)
class _MentionCandidate:
    category: str
    surface: str
    start: int
    end: int
    is_english: bool
    is_plural: bool


OBJECT_TERMS: dict[str, tuple[str, ...]] = {
    "apple": ("apple", "苹果"),
    "basket": ("basket", "篮子", "筐"),
    "block": ("block", "cube", "方块", "积木"),
    "mustard_bottle": ("mustard bottle",),
    "soft_scrub": ("soft scrub bottle", "soft scrub"),
    "bottle": ("bottle", "瓶子", "瓶"),
    "bowl": ("bowl", "碗"),
    "calculator": ("calculator", "计算器"),
    "cabinet": ("cabinet", "drawer cabinet", "柜子", "抽屉柜"),
    "coffee_can": ("coffee can",),
    "spam_can": ("spam can",),
    "tomato_soup_can": ("tomato soup can",),
    "can": ("cola can", "soda can", "can", "可乐罐", "罐子", "易拉罐"),
    "mug": ("coffee mug", "mug"),
    "cup": ("cup", "马克杯", "杯子", "杯"),
    "sugar_box": ("sugar box",),
    "cheez_it": ("cheez-it box", "cheez it box", "cheez-it", "cheez it"),
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

ENGLISH_QUANTITIES = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
}

CHINESE_QUANTITIES = {
    "一": 1,
    "一个": 1,
    "一只": 1,
    "二": 2,
    "两": 2,
    "两个": 2,
    "两只": 2,
    "三": 3,
    "三个": 3,
    "三只": 3,
}

IRREGULAR_ENGLISH_PLURALS = {
    "knife": "knives",
}

UNSUPPORTED_ENGLISH_QUANTITIES = {
    "zero",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "couple",
    "dozen",
    "hundred",
    "many",
    "multiple",
    "several",
}

MAX_SCENE_OBJECTS = 12


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
    (r"\brespectively\b|分别", "one-to-one distribution"),
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


def _pluralize_english_term(term: str) -> str:
    words = term.split()
    singular = words[-1]
    if singular in IRREGULAR_ENGLISH_PLURALS:
        plural = IRREGULAR_ENGLISH_PLURALS[singular]
    elif singular.endswith("y") and len(singular) > 1 and singular[-2] not in "aeiou":
        plural = f"{singular[:-1]}ies"
    elif singular.endswith(("s", "x", "z", "ch", "sh")):
        plural = f"{singular}es"
    else:
        plural = f"{singular}s"
    return " ".join((*words[:-1], plural))


def _attribute_pattern(*, english: bool) -> str:
    terms = {
        term
        for lexicon in (COLOR_TERMS, MATERIAL_TERMS)
        for variants in lexicon.values()
        for term in variants
        if bool(re.search(r"[a-z]", term)) == english
    }
    return "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True))


def _quantity_before(
    normalized: str, candidate: _MentionCandidate
) -> tuple[int, tuple[int, int] | None]:
    prefix = normalized[: candidate.start]
    attributes = _attribute_pattern(english=candidate.is_english)
    if candidate.is_english:
        match = re.search(
            rf"(?<![a-z0-9])(?P<quantity>[a-z]+|\d+)\s+"
            rf"(?:(?:{attributes})\s+)*$",
            prefix,
        )
        token = match.group("quantity") if match else None
        if token in ENGLISH_QUANTITIES:
            quantity = ENGLISH_QUANTITIES[token]
            quantity_span = match.span("quantity")
        elif token is not None and (token.isdigit() or token in UNSUPPORTED_ENGLISH_QUANTITIES):
            raise SceneSpecError(f"unsupported object quantity: {token}")
        else:
            quantity = 1
            quantity_span = None
        if candidate.is_plural and quantity_span is None:
            raise SceneSpecError(
                f"plural object term {candidate.surface!r} requires an explicit supported quantity"
            )
        if candidate.is_plural != (quantity > 1) and quantity_span is not None:
            raise SceneSpecError(f"quantity does not agree with object term {candidate.surface!r}")
        return quantity, quantity_span

    quantities = "|".join(
        re.escape(term) for term in sorted(CHINESE_QUANTITIES, key=len, reverse=True)
    )
    match = re.search(
        rf"(?P<quantity>{quantities})\s*(?:(?:{attributes})\s*)*$",
        prefix,
    )
    if match:
        token = match.group("quantity")
        return CHINESE_QUANTITIES[token], match.span("quantity")
    unsupported = re.search(
        rf"(?P<quantity>[0-9零一二两三四五六七八九十百千万]+(?:个|只)?)"
        rf"\s*(?:(?:{attributes})\s*)*$",
        prefix,
    )
    if unsupported:
        raise SceneSpecError(f"unsupported object quantity: {unsupported.group('quantity')}")
    return 1, None


def _span_is_covered(span: tuple[int, int], covering_spans: list[tuple[int, int]]) -> bool:
    return any(start <= span[0] and span[1] <= end for start, end in covering_spans)


def _validate_unbound_quantities(normalized: str, consumed_spans: list[tuple[int, int]]) -> None:
    reference_spans = [
        match.span()
        for pattern in (
            r"\b(?:two|both)\s+objects?\b",
            r"两件物体|两个物体|两者",
        )
        for match in re.finditer(pattern, normalized)
    ]
    english_terms = set(ENGLISH_QUANTITIES) - {"a", "an"}
    english_terms.update(UNSUPPORTED_ENGLISH_QUANTITIES)
    english_pattern = "|".join(
        re.escape(term) for term in sorted(english_terms, key=len, reverse=True)
    )
    for match in re.finditer(rf"\b(?:{english_pattern})\b", normalized):
        if _span_is_covered(match.span(), consumed_spans + reference_spans):
            continue
        raise SceneSpecError(f"quantity term {match.group(0)!r} is not bound to an object")

    chinese_terms = "|".join(
        re.escape(term) for term in sorted(CHINESE_QUANTITIES, key=len, reverse=True)
    )
    for match in re.finditer(chinese_terms, normalized):
        if _span_is_covered(match.span(), consumed_spans + reference_spans):
            continue
        if match.group(0) == "一" and normalized[match.end() :].startswith(("半", "起")):
            continue
        if match.start() > 0 and normalized[match.start() - 1] == "第":
            continue
        raise SceneSpecError(f"quantity term {match.group(0)!r} is not bound to an object")

    for match in re.finditer(r"\b\d+\b", normalized):
        if (match.start() > 0 and normalized[match.start() - 1] == ".") or (
            match.end() < len(normalized) and normalized[match.end()] == "."
        ):
            continue
        suffix = normalized[match.end() :]
        if re.match(r"\s*(?:%|m\b|meters?\b|米)", suffix):
            continue
        if re.match(r"\s*(?:[a-z]|[\u3400-\u9fff])", suffix):
            raise SceneSpecError(f"numeric object quantity {match.group(0)!r} is unsupported")

    for match in re.finditer(r"[零一二两三四五六七八九十百千万]+(?:个|只)", normalized):
        if _span_is_covered(match.span(), consumed_spans + reference_spans):
            continue
        raise SceneSpecError(f"quantity term {match.group(0)!r} is not bound to an object")


def extract_mentions(request: str) -> list[MentionGroup]:
    normalized = _normalize(request)
    candidates: list[_MentionCandidate] = []
    for category, terms in OBJECT_TERMS.items():
        for term in sorted(terms, key=len, reverse=True):
            is_english = bool(re.fullmatch(r"[a-z0-9 ]+", term))
            variants = ((term, False),)
            if is_english:
                variants += ((_pluralize_english_term(term), True),)
            for variant, is_plural in variants:
                for match in re.finditer(_term_pattern(variant), normalized, flags=re.IGNORECASE):
                    candidates.append(
                        _MentionCandidate(
                            category=category,
                            surface=match.group(0),
                            start=match.start(),
                            end=match.end(),
                            is_english=is_english,
                            is_plural=is_plural,
                        )
                    )
    generic_patterns = (
        r"\b(?:place|put|add|create|generate|stack)\s+(?:a|an|the|one)\s+(?P<object>[a-z][a-z0-9_-]*(?:\s+[a-z][a-z0-9_-]*){0,3}?)(?=\s+(?:on\s+top\s+of|inside|into|on|near|to\s+the|in\s+front\s+of|behind)|[.,]|$)",
        r"\b(?:a|an|one)\s+(?P<object>[a-z][a-z0-9_-]*(?:\s+[a-z][a-z0-9_-]*){0,3}?)\s+(?:is\s+)?(?=on\s+top\s+of|inside|on\s+the\s+table|near|to\s+the|in\s+front\s+of|behind)",
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
            if any(
                not (end <= candidate.start or start >= candidate.end) for candidate in candidates
            ):
                continue
            words = [word for word in match.group("object").split() if word not in attribute_terms]
            if not words:
                continue
            category = re.sub(r"[^a-z0-9]+", "_", "_".join(words)).strip("_")[:64]
            if category:
                candidates.append(
                    _MentionCandidate(
                        category=category,
                        surface=match.group("object"),
                        start=start,
                        end=end,
                        is_english=True,
                        is_plural=False,
                    )
                )
    selected: list[_MentionCandidate] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (item.start, -(item.end - item.start), item.category),
    ):
        if any(
            not (candidate.end <= other.start or candidate.start >= other.end) for other in selected
        ):
            continue
        selected.append(candidate)
    selected.sort(key=lambda item: (item.start, item.end, item.category))
    counts: dict[str, int] = {}
    mentions: list[MentionGroup] = []
    consumed_quantity_spans: list[tuple[int, int]] = []
    object_count = 0
    for group_id, candidate in enumerate(selected):
        quantity, quantity_span = _quantity_before(normalized, candidate)
        if quantity_span is not None:
            consumed_quantity_spans.append(quantity_span)
        object_count += quantity
        if object_count > MAX_SCENE_OBJECTS:
            raise SceneSpecError(f"scene requests more than {MAX_SCENE_OBJECTS} objects")
        first_index = counts.get(candidate.category, 0) + 1
        object_ids = tuple(
            f"{candidate.category}_{index}" for index in range(first_index, first_index + quantity)
        )
        counts[candidate.category] = first_index + quantity - 1
        mentions.append(
            MentionGroup(
                category=candidate.category,
                surface=candidate.surface,
                start=candidate.start,
                end=candidate.end,
                group_id=group_id,
                quantity=quantity,
                object_ids=object_ids,
            )
        )
    _validate_unbound_quantities(normalized, consumed_quantity_spans)
    if not mentions:
        raise SceneSpecError("no supported tabletop object found")
    return mentions


def _nearest_attribute(
    request: str,
    mention: MentionGroup,
    lexicon: dict[str, tuple[str, ...]],
    previous_end: int | None,
) -> str | None:
    normalized = _normalize(request)
    window_start = max(previous_end or 0, mention.start - 24)
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


def _region_for(request: str, mention: MentionGroup, next_start: int | None) -> str:
    normalized = _normalize(request)
    end = min(len(normalized), max(mention.end + 40, next_start or mention.end))
    window = normalized[max(0, mention.start - 12) : end]
    for region, terms in REGION_TERMS.items():
        if any(re.search(_term_pattern(term), window, flags=re.IGNORECASE) for term in terms):
            return region
    return "center"


def _relation_between(
    request: str, first: MentionGroup, second: MentionGroup
) -> RelationType | None:
    normalized = _normalize(request)
    between = normalized[first.end : second.start]
    after = normalized[second.end : min(len(normalized), second.end + 16)]
    combined = f"{between} {after}"
    if re.search(r"\b(?:inside|into|within)\b", between) or re.search(
        r"放进|装进|放入|置于.*(?:里面|内部)", between
    ):
        return RelationType.INSIDE
    if re.search(r"\b(?:on\s+top\s+of|stacked?\s+(?:on|onto)|onto|on)\b", between) or re.search(
        r"叠在|堆在|放在", between
    ):
        return RelationType.ON_TOP_OF
    if re.search(r"\b(?:to\s+the\s+)?left\s+of\b", between) or re.search(
        r"(?:的)?左边|左侧", after
    ):
        return RelationType.LEFT_OF
    if re.search(r"\b(?:to\s+the\s+)?right\s+of\b", between) or re.search(
        r"(?:的)?右边|右侧", after
    ):
        return RelationType.RIGHT_OF
    if re.search(r"\bin\s+front\s+of\b", between) or re.search(r"(?:的)?前方|前面", after):
        return RelationType.FRONT_OF
    if re.search(r"\bbehind\b", between) or re.search(r"(?:的)?后方|后面", after):
        return RelationType.BEHIND
    if re.search(r"\b(?:near|next\s+to|beside)\b", combined) or re.search(
        r"靠近|旁边|邻近", combined
    ):
        return RelationType.NEAR
    return None


def _articulation_for(request: str, mention: MentionGroup) -> dict[str, Any] | None:
    if mention.category not in ARTICULATED_CATEGORIES:
        return None
    normalized = _normalize(request)
    local = normalized[max(0, mention.start - 32) : min(len(normalized), mention.end + 48)]
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
    mention_groups = extract_mentions(request)
    objects: list[SceneObjectSpec] = []
    for index, group in enumerate(mention_groups):
        next_start = mention_groups[index + 1].start if index + 1 < len(mention_groups) else None
        previous_end = mention_groups[index - 1].end if index > 0 else None
        color = _nearest_attribute(request, group, COLOR_TERMS, previous_end)
        material = _nearest_attribute(request, group, MATERIAL_TERMS, previous_end)
        region = _region_for(request, group, next_start)
        articulation = _articulation_for(request, group)
        for object_id in group.object_ids:
            objects.append(
                SceneObjectSpec(
                    object_id=object_id,
                    category=group.category,
                    color=color,
                    material=material,
                    region=region,
                    articulation=articulation,
                )
            )

    pair_relations: list[RelationSpec] = []
    nested_sources: set[str] = set()
    for first_index, first in enumerate(mention_groups):
        for second in mention_groups[first_index + 1 :]:
            relation = _relation_between(request, first, second)
            if relation is None:
                continue
            if second.quantity > 1:
                raise SceneSpecError(
                    "relations with a plural target are unsupported; "
                    "only multiple sources to one target are supported"
                )
            target = second.object_ids[0]
            for source in first.object_ids:
                pair_relations.append(
                    RelationSpec(
                        relation=relation,
                        source=source,
                        target=target,
                        max_distance_m=0.25 if relation == RelationType.NEAR else None,
                    )
                )
                if relation in {RelationType.ON_TOP_OF, RelationType.INSIDE}:
                    nested_sources.add(source)

    relations: list[RelationSpec] = [
        RelationSpec(relation=RelationType.ON_TABLE, source=item.object_id, target="table")
        for item in objects
        if item.object_id not in nested_sources
    ]
    relations.extend(pair_relations)

    normalized = _normalize(request)
    distance_match = re.search(
        r"(?:at\s+least|minimum|至少)\s*(\d+(?:\.\d+)?)\s*(m|meter|meters|米)",
        normalized,
    )
    if distance_match:
        if len(mention_groups) < 2:
            raise SceneSpecError("distance constraint requires at least two object groups")
        first, second = mention_groups[:2]
        if second.quantity > 1:
            raise SceneSpecError("distance relations with a plural target are unsupported")
        for source in first.object_ids:
            relations.append(
                RelationSpec(
                    relation=RelationType.DISTANCE_AT_LEAST,
                    source=source,
                    target=second.object_ids[0],
                    min_distance_m=float(distance_match.group(1)),
                )
            )

    object_ids = tuple(object_id for group in mention_groups for object_id in group.object_ids)
    if re.search(
        r"(?:two objects|both objects|两件物体|两个物体|两者).{0,8}"
        r"(?:near|close|靠近|相邻)",
        normalized,
    ):
        if len(object_ids) != 2:
            raise SceneSpecError("two-object near reference requires exactly two object instances")
        pair = set(object_ids)
        if not any(
            relation.relation == RelationType.NEAR and {relation.source, relation.target} == pair
            for relation in relations
        ):
            relations.append(
                RelationSpec(
                    relation=RelationType.NEAR,
                    source=object_ids[0],
                    target=object_ids[1],
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


def parse_with_provider(
    provider: StructuredSceneProvider, *, request: str, seed: int = 0
) -> SceneSpec:
    validate_prompt_boundary(request)
    return parse_provider_payload(
        provider.parse_scene(request=request, seed=seed), request=request, seed=seed
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse a bounded bilingual prompt into SceneSpec JSON."
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    spec = parse_rule_based(args.prompt, seed=args.seed)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec.canonical_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"PASS scene_id={spec.scene_id} sha256={spec.digest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# 受限解析

## 为什么解析器不是「能听懂话」

`/gen-env` 的解析器不是 LLM，是一组正则 + 双语词典。设计原因很直接：自然语言里说 `can`，机器人在 RoboTwin 里有十几个匹配项；如果允许 prompt 直接带 `asset_id`、`qpos`、文件路径或 pose，编译就退化成「让用户写机器人配置」。所以解析器的第一职责是「拒绝」——把文本压回一个语义子集。你看到这个职责起作用的位置在 [一条真实路径 Step 1](../walkthroughs/one-real-run.md)。

受限子集允许：`apple`/`basket`/`block`/`bottle`/`bowl`/`calculator`/`cabinet`/`cup`/`box`/`hammer`/`knife`/`laptop`/`oven`/`plate`/`tray`/`vegetable` 等桌面物体名（中英同义），加颜色、材质、区域（left/right/front/back/center），加关系 `on_table`/`on_top_of`/`inside`/`left_of`/`right_of`/`front_of`/`behind`/`near`/`distance_at_least`，加 cabinet/box/laptop/microwave/oven 上的 articulation 状态。

它拒绝的明显在这一行 `FORBIDDEN_PROMPT_PATTERNS`：

```python
FORBIDDEN_PROMPT_PATTERNS = (
    (r"```|\b(?:import|exec|eval)\s*\(|\bdef\s+[a-z_]", "executable code"),
    (r"(?:^|\s)(?:/[^\s]+|~\/[^\s]+|[a-zA-Z]:\\[^\s]+)", "filesystem path"),
    (r"\b(?:asset_id|model_id|qpos|quaternion|wxyz|world_xyz)\b", "backend field"),
    (r"\b[xyz]\s*=\s*-?\d", "world coordinate"),
    (r"\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d", "coordinate tuple"),
)
```

外加 schema 端的 `FORBIDDEN_SCENE_KEYS`——即使 provider 路径返回的 JSON 也过不去这道。schema 的完整契约在 [类型化场景契约](scene-contract.md)。

## 怎么从一句话抽到 object + 关系

解析过程不是「整句翻译」，是「先找物体，再判断两两关系」。`extract_mentions` 把 sentence 里所有词典命中的 span 抽出来，长词优先；通用 `place/put/add a <object>` 模式补漏（按属性词剥离再拼成 category）。每个 mention 拿一个 `{category}_<counts>` 形式的 object_id，比如 `can_1`、`plate_1`。

然后 `_relation_between` 看两个 mention 之间的词和目标之后的窗口判定关系——`on top of` / `inside` / `to the left of` / `in front of` / `near` / `behind` 都匹配。嵌套 source（on_top_of / inside 的 source）去除 `on_table` 关系，只有放在桌上的才拿 `on_table`。最后 `parse_rule_based` 走 schema 构造 `SceneSpec`。

代表案（来自速测）的输入输出：

| Prompt | objects | relations |
| --- | --- | --- |
| `Place a can on top of a plate.` | can_1, plate_1 | on_table plate_1→table；on_top_of can_1→plate_1 |
| `Put a cup inside a basket.` | cup_1, basket_1 | on_table basket_1→table；inside cup_1→basket_1 |
| `Place a red can to the left of a plastic basket near the center.` | can_1, basket_1（color=red、material=plastic）| on_table basket_1→table；left_of can_1→basket_1；near can_1→basket_1(max 0.25 m) |
| `把杯子放进篮子里。` | cup_1, basket_1 | on_table basket_1→table；inside cup_1→basket_1 |
| `Place a half-open cabinet on the table.` | cabinet_1（articulation=partially_open 0.5）| on_table cabinet_1→table |

`half-open` / `打开一半` / `open 60%` 在 `_articulation_for` 里被识别成 `partially_open` + `open_fraction=0.5`（或显式百分比），比例必须在 `(0, 1)` 开区间内。

## provider 路径会改变这件事吗

`StructuredSceneProvider` 协议允许外部 provider 返回结构化 JSON。但有两道闸：`parse_provider_payload` 通过 `SceneSpec.model_validate` 过契约，并且强制 `spec.request == request`、`spec.seed == seed`——provider 不能改用户的请求、不能改 seed。`validate_prompt_boundary` 仍然在调用前跑。这条路径在 `script/` 默认 CLI 里不启用，使用时由 demo 或 host 自接；rule-based 是仓库默认。

## 改动入口与验证

- 加新物体词：改 `OBJECT_TERMS`（也别忘了 `asset_overrides.yml`/catalog 端的真模型）。
- 加新关系：先改 `schema.RelationType`、`SceneSpec.semantic_consistency`；再改 `_relation_between` 与 `_pair_relation_reasons`。
- 加新禁用模式：改 `FORBIDDEN_PROMPT_PATTERNS` 或 schema 端 `FORBIDDEN_SCENE_KEYS`。
- 改完跑 `pytest -q tests/scene_gen/test_parser.py`。

要回到主路径，到 [一条真实路径](../walkthroughs/one-real-run.md)。要继续追「`SceneSpec` 里能带什么字段、为什么 frozen」，去 [类型化场景契约](scene-contract.md)。

证据状态：除特别标注外，本页基于当前源码已确认。
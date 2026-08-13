# gen-env 规则解析器最小修改任务交接说明

## 1. 任务目标

先只修改 `robot-harness-gen-env` 的规则解析器，使数量词和已知物体的英文复数能够正确生成 `SceneSpec`。

首要回归案例：

```text
Place two cans on top of a plate.
Place two bottles on top of a plate.
```

期望第一句生成：

```text
can_1
can_2
plate_1
can_1 on_top_of plate_1
can_2 on_top_of plate_1
plate_1 on_table table
```

期望第二句同理生成 `bottle_1`、`bottle_2` 和 `plate_1`。

本阶段的目标是正确表达用户语义，不保证这些物体组合在真实几何上一定可解。正确的 `SceneSpec` 后续被 solver 判定为几何不可行，属于合理结果。

## 2. 已确认的根因

当前入口 `script/generate_scene.py` 固定调用：

```python
spec = parse_rule_based(args.prompt, seed=args.seed)
```

问题发生在 `scene_gen/parser.py::extract_mentions()`：

1. `OBJECT_TERMS` 只列出 `can`、`bottle` 等单数形式，没有 `cans`、`bottles`。
2. `_term_pattern()` 要求英文术语后面不能继续出现字母，所以 `can` 不会匹配 `cans`，`bottle` 不会匹配 `bottles`。
3. 通用物体正则只接受 `a|an|the`，不接受 `one|two|three` 等数量词。
4. 当前 `Mention` 一次只能产生一个对象，不会把 `two bottles` 展开为两个唯一实例。
5. 只要句子中还能识别出一个物体（例如 `plate`），解析器就不会失败，于是两个瓶子被静默删除。
6. 后续 solver 和 runtime 只验证错误的 SceneSpec，因此“一个盘子在桌上”仍会显示 PASS。

实际错误输出已经确认：

```json
{
  "objects": [
    {"object_id": "plate_1", "category": "plate"}
  ],
  "relations": [
    {"relation": "on_table", "source": "plate_1", "target": "table"}
  ]
}
```

## 3. 最小修改范围

优先只修改：

```text
scene_gen/parser.py
tests/scene_gen/test_parser.py
```

如需要补充固定测试样例，可以小幅更新：

```text
tests/fixtures/golden_prompts.json
```

明确不修改：

- `scene_gen/schema.py`：现有 Schema 已允许最多 12 个对象，并要求 `object_id` 唯一。
- `scene_gen/grounding.py`：已经按 `object_id` 独立匹配同类物体。
- `scene_gen/solver.py`：已经支持多个实例、共同支撑目标、重叠检查和回溯。
- `scene_gen/envs/generated_scene.py`：已经会把每个 resolved object 加载成独立 actor。
- RoboTwin、SAPIEN、资产 catalog 和 runtime。
- 本阶段不接入语言模型，不新增 Provider，不修改 CLI。

## 4. 推荐的最小实现方案

### 4.1 增加受控数量词

在 `parser.py` 中增加小型确定性词表。首版建议支持 1～3，避免一次扩展过大：

```python
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
```

如果首轮只要求修复当前英文案例，可以先实现英文 `one/two/three`；但仓库定位是双语解析器，推荐同时覆盖最基本的中文一/两/三，保持能力对称。

数量不得超过 `SceneSpec.objects` 的上限 12。超过支持范围或无法绑定到物体时应抛出 `SceneSpecError`，不能静默忽略。

### 4.2 支持已知英文物体的复数

不要只在 `OBJECT_TERMS` 中手工添加 `cans` 和 `bottles`，否则以后每个类别都会重复出现相同问题。

推荐在构造英文术语匹配模式时，为已知词生成受控复数变体：

- 普通单词：`can -> cans`、`bottle -> bottles`、`apple -> apples`。
- `-y`：`battery -> batteries`（如果属于支持词表）。
- `-s/-x/-ch/-sh`：使用 `-es`。
- 不规则形式使用小型显式映射，例如 `knife -> knives`。
- 中文词不做英文复数转换。

只对 `OBJECT_TERMS` 中已经允许的词生成复数，不能把任意未知复数名词自动变成新 category。

### 4.3 给已识别物体绑定数量

为 `Mention` 增加数量信息，或增加内部辅助结构，例如：

```python
quantity: int = 1
```

数量只从紧邻物体短语的限定词读取。例如：

```text
two bottles
three red cans
两个杯子
```

颜色和材质夹在数量与物体之间时仍应允许：

```text
two red cans
two plastic bottles
```

不要把句子其他位置的数字错误绑定到物体，例如距离 `0.20 m` 不能被当成对象数量。

### 4.4 将一个 mention 展开成多个实例

在 `extract_mentions()` 已完成候选去重之后，按 quantity 展开对象：

```text
two bottles
→ bottle_1
→ bottle_2
```

两个展开实例可以共享同一个文本 span，但必须拥有不同 `object_id`。

现有关系算法会遍历 mention pair。对于两个共享 span 的 bottle mention：

- `bottle_1` 与 `bottle_2` 之间不应凭空产生关系。
- `bottle_1` 到 `plate_1` 应识别 `on top of`。
- `bottle_2` 到 `plate_1` 也应识别同一条 `on top of`。

因此实现时必须显式验证“同一量词组展开的源对象都继承到目标物体的关系”。不要只依赖偶然的 span 排序行为。

推荐给展开实例保留内部 `group_id`，关系解析先以 mention group 为单位识别，再将关系展开到 group 中的所有实例。该字段只用于 parser 内部，不加入公共 `SceneSpec`。

### 4.5 失败必须 fail closed

本次最小改动至少增加以下保护：

- 识别到支持的数量词和支持物体后，最终对象数量必须一致。
- `two bottles` 不能产生 0 或 1 个 bottle。
- 数量词无法绑定到物体时抛出 `SceneSpecError`。
- 数量超过支持上限时抛出 `SceneSpecError`。
- 已知物体的复数不能被静默忽略。

本阶段不尝试解决任意自然语言的完整语义覆盖；只保证新增的受控数量/复数语法不会静默降级。

## 5. 预期数据流

输入：

```text
Place two cans on top of a plate.
```

解析过程：

```text
two + cans
→ category=can, quantity=2
→ can group [can_1, can_2]

a + plate
→ category=plate, quantity=1
→ plate group [plate_1]

on top of
→ source group=can, target group=plate
→ can_1 on_top_of plate_1
→ can_2 on_top_of plate_1

plate_1 未嵌套在其他物体上
→ plate_1 on_table table
```

最终 `SceneSpec`：

```json
{
  "objects": [
    {"object_id": "can_1", "category": "can", "region": "center"},
    {"object_id": "can_2", "category": "can", "region": "center"},
    {"object_id": "plate_1", "category": "plate", "region": "center"}
  ],
  "relations": [
    {"relation": "on_top_of", "source": "can_1", "target": "plate_1"},
    {"relation": "on_top_of", "source": "can_2", "target": "plate_1"},
    {"relation": "on_table", "source": "plate_1", "target": "table"}
  ]
}
```

## 6. 必须新增的测试

在 `tests/scene_gen/test_parser.py` 增加聚焦测试，至少覆盖：

### 成功案例

1. `Place two cans on top of a plate.`
   - 对象恰好是 `can_1`、`can_2`、`plate_1`。
   - 两个 can 都有且只有一条 `on_top_of plate_1`。
   - `plate_1` 有 `on_table table`。
   - 两个 can 不得被添加 `on_table`。

2. `Place two bottles on top of a plate.`
   - 对象数量与关系同上。

3. `Place two red cans on top of a plate.`
   - `can_1` 和 `can_2` 的 color 都是 `red`。

4. 中文对称案例，例如 `把两个杯子放进篮子里。`
   - 生成 `cup_1`、`cup_2`、`basket_1`。
   - 两个 cup 都是 `inside basket_1`。

5. 同一 prompt、同一 seed 的 digest 保持确定性。

### 失败案例

1. 超过支持数量或 Schema 上限的请求必须明确失败。
2. 孤立数量词或无法绑定的数量短语必须失败，不能退化成少量对象。
3. 未支持的复杂分配关系应失败，不能猜测，例如“两个瓶子分别放在两个盘子上”如果本阶段未实现一一对应。

### 兼容性

现有全部 parser 测试必须保持通过，特别是：

- `Place a red block on top of a plate.`
- `Put an apple inside a basket.`
- 中英文 articulation。
- `both objects are near each other` / `两个物体相邻`。这里的 `two objects` 是回指，不应错误生成两个 category=`object` 的新物体。

运行：

```bash
cd /home/jingxiang/gujie/robot-harness-gen-env
conda activate env-gen-sc311
pytest -q tests/scene_gen/test_parser.py
pytest -q
```

## 7. 验收标准

修改完成必须满足：

1. 两个首要英文案例生成正确数量、唯一 ID 和关系。
2. 不再出现“原文说 two cans，但 SceneSpec 只有 plate”的静默成功。
3. 相同 prompt 和 seed 输出完全确定。
4. 原有中英文 golden prompts 和全部测试通过。
5. 不修改 schema、solver、runtime 或 RoboTwin。
6. 不接入语言模型，不增加网络依赖。
7. 对几何不可行场景，parser 仍生成语义正确的 SceneSpec，由 solver 返回明确失败。

## 8. 已知的后续结果

当前真实 catalog 中：

```text
bottle model0 footprint ≈ 96.5 × 96.1 mm
plate stable support surface = 100 × 100 mm
plate required support margin = 8 mm
```

因此正确生成“两瓶一盘”的 SceneSpec 后，现有 solver 会因稳定支撑面积不足而失败。这个结果不代表 parser 修改失败，而表示：

```text
自然语言理解正确
SceneSpec 正确
真实资产几何不可行
```

“两罐一盘”是否可解应由真实 catalog 和 solver 决定，不属于本次 parser 修改的验收条件。

## 9. 本阶段之后的后续任务（不要混入本次修改）

规则解析器完成后，再单独规划：

1. 更完整的语义覆盖报告和未消费文本诊断。
2. 规则无法覆盖时调用结构化语言模型 Provider。
3. 语言模型候选的 Schema 验证与语义审计。
4. 更复杂的复数分配，例如两个源物体对应两个不同目标。
5. 经过物理验证的小尺寸代理资产或更大支撑目标。

当前提交应保持小而清晰，只解决受控数量词、已知物体复数、实例展开和关系复制。

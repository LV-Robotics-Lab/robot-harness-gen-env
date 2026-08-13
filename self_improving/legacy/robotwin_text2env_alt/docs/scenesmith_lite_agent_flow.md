# SceneSmith-lite Agent Flow

更新时间：2026-06-24 Asia/Shanghai  
范围：RoboTwin2 Text2Env demo 的 tabletop task 生成流程

---

## 1. 一句话目标

SceneSmith-lite 把自然语言 tabletop 任务转换成可检查的 Text2Env JSON。它不直接写 RoboTwin2 代码，而是先让 agent 产出结构化任务 spec，再交给 Step 4 的 scaffold generator。

```text
User task
  -> Designer proposes Text2Env draft
  -> Critic audits feasibility and schema/API support
  -> Orchestrator revises or accepts
  -> Final Text2Env JSON
  -> RoboTwin2 scaffold
```

---

## 2. 三个角色

### Designer

输入：

- 自然语言任务
- `schemas/text2env_schema_v0.md`
- 可用资产提示或现有 examples
- 当前限制：v0 优先 box / known assets / simple regions

输出：

- 一个完整 Text2Env JSON draft
- `status` 可以是 `ready_for_scaffold`、`draft_requires_review`、`draft_requires_articulation`

Designer 不做：

- 不直接写 Python task code
- 不假设不存在的 RoboTwin2 asset 一定可用
- 不把模糊成功条件留给后续代码解决

### Critic

输入：

- Designer draft JSON
- schema v0
- RoboTwin2 task API notes
- 可用资产列表或人工提供的 asset checks

输出：

- JSON audit report
- `verdict`: `accept`、`revise`、`reject`
- issues 列表和 precise patch suggestions

Critic 重点看：

- 对象、区域、动作、成功条件是否引用一致
- 物体初始 pose 是否可能重叠或越界
- ready task 是否用了 articulated object
- success 是否能从 simulator state 判断
- language placeholders 是否完整绑定
- RoboTwin2 是否支持所需 asset / primitive

### Orchestrator

输入：

- 原始用户任务
- Designer draft
- Critic report
- schema v0

输出：

- final Text2Env JSON
- decision report

Orchestrator 决策：

- `accept`: Critic 无 error，最多有低风险 warning
- `revise`: 能通过局部修改修复
- `retry`: draft 的任务分解或资产选择方向错了，需要 Designer 重新来
- `defer`: 需要外部确认，例如 drawer asset、articulation qpos、policy hook

---

## 3. 推荐执行流程

### A. 新任务从自然语言开始

```text
1. 把用户任务填入 prompts/text2env_designer.md
2. Designer 输出 draft JSON
3. 保存到 examples/tabletop_tasks/<task_name>.json 或 runs/<task>/designer.json
4. 运行 scripts/generate_text2env.py check <json>
5. 把 JSON 和 check report 填入 prompts/text2env_critic.md
6. Critic 输出 audit JSON
7. 把 original task + draft + critic report 填入 prompts/text2env_orchestrator.md
8. Orchestrator 输出 final JSON
9. 再运行 check，若无 error，进入 Step 4
```

### B. 从现有模板快速启动

Task B 这类 v0 demo 可以先复制模板：

```bash
python scripts/generate_text2env.py from-template \
  --template move_object_between_zones \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --task-name move_object_between_zones \
  --out examples/tabletop_tasks/move_object_between_zones.json
```

然后检查：

```bash
python scripts/generate_text2env.py check examples/tabletop_tasks/move_object_between_zones.json
```

---

## 4. Flow Contract

Designer output contract:

- Must be JSON only.
- Must conform to `schema_version = text2env.tabletop.v0`.
- Must include a realistic `status`.
- Must include all fields needed by Step 4 scaffold generation.

Critic output contract:

```json
{
  "verdict": "accept | revise | reject",
  "summary": "short decision summary",
  "issues": [
    {
      "severity": "error | warning | note",
      "code": "short_code",
      "path": "json.path",
      "message": "what is wrong",
      "suggested_fix": "concrete fix"
    }
  ],
  "patch_plan": [
    "ordered concrete edits"
  ]
}
```

Orchestrator output contract:

```json
{
  "decision": "accept | revised | retry | defer",
  "reason": "short reason",
  "final_text2env": {},
  "remaining_blockers": []
}
```

---

## 5. Acceptance Criteria for Step 3

Step 3 is complete when:

- Designer prompt exists and tells the model to output Text2Env JSON only.
- Critic prompt exists and returns structured issues.
- Orchestrator prompt exists and returns final JSON plus decision.
- `scripts/generate_text2env.py check` can validate core references in a JSON spec.
- Main plan marks Step 3 prompt files and `generate_text2env.py` as done.

---

## 6. Current Recommended Next Step

Use `examples/tabletop_tasks/move_object_between_zones.json` as the first Orchestrator-approved final Text2Env JSON, then proceed to Step 4:

```text
Text2Env JSON
  -> envs/move_object_between_zones.py scaffold
  -> description/task_instruction/move_object_between_zones.json
  -> demo_smoke run
```

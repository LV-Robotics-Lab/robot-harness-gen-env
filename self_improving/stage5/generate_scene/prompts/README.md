# Prompt Templates For External Scene Agents

These markdown files define how external LLM/VLM backends should behave.

They are used by:

```text
generate_scene/gpt_agent.py
generate_scene/observation_agent.py
```
Current agent prompts:

```text
asset_grounding_agent.md       # natural language -> catalog assets
designer_agent.md              # prompt + assets -> initial PlacementSpec
static_critic_agent.md         # static validation -> accept/repair decision
orchestrator_agent.md          # repair/accept final PlacementSpec before smoke
observation_vlm_agent.md       # smoke images -> visual_review.json
visual_repair_agent.md         # visual_review failure -> repaired PlacementSpec
```

Shared hard rules:

```text
- Return valid JSON only.
- Never invent asset_id or model_id values.
- Do not generate task play_once() code.
- left/right/front/back are in the robot or dual-arm first-person frame.
- Smoke pass is not visual pass.
```

# Orchestrate Placement Pipeline

Use this skill when combining Designer output, Critic review, and validation results into the next pipeline artifact. In the agent loop, this is the Orchestrator capability.

## Rules

- If the Critic rejects the placement, stop with a blocked decision.
- If repair is required, produce concrete repair instructions.
- If accepted, write a final `robotwin.tabletop_placement.v0` artifact with stage `final_static_for_smoke`.
- Preserve valid object ids, asset ids, model ids, and poses.
- Carry simulator and render checks into the validation plan.
- Do not claim visual verification until RoboTwin preview evidence has been reviewed.
- Treat `pending_visual_review` as expected after smoke when no human/VLM/Codex-visual report is provided.
- Only mark the full pipeline pass when static validation, smoke, and an explicit visual review report all pass.
- After each new prompt run, capture any newly discovered failure mode or asset-specific pose/loading requirement back into the relevant skill and asset catalog.

## Output

Return final placement JSON and a validation plan.

## Lessons From Runs

- Keep asset-specific fixes in the asset catalog (`placement_defaults`) rather than hard-coding one-off prompt logic in the harness.
- When a visual review fails, repair the placement and rerun smoke instead of overriding the review result.
- Preserve both pending and pass runs when useful: pending shows the visual gate worked; pass shows the reviewed final scene.

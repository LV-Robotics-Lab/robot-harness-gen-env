# Design Tabletop Placement

Use this skill when turning a natural-language tabletop scene request into an initial `TabletopPlacementSpec`. In the agent loop, this is the Designer capability.

## Inputs

- Placement prompt.
- Asset catalog JSON.
- Table workspace bounds.
- Optional downstream robot task hint.

## Rules

- Select only assets that exist in the catalog.
- Do not invent asset ids, model ids, paths, affordances, or task code.
- Convert spatial language such as left, right, near, on, inside, and separated into explicit tabletop poses or regions.
- Keep every object inside workspace bounds.
- Prefer simple stable tabletop placements unless the prompt requests contact, stacking, or containment.
- Mark simulator, render, and visual checks as pending until smoke evidence exists.
- Parse lateral relations as relations, not as object mention order. For example, "a laptop is on the right side of a knife" means `right_of(laptop, knife)` even though laptop appears first.
- Normalize simple determiners such as "a", "an", and "the" before matching relation templates.
- In the default RoboTwin observer camera, rendered visual left/right can be mirrored relative to tabletop-local x. For language prompts about left/right, prioritize the final preview image because the visual Critic/VLM is the acceptance gate.
- Use asset catalog `placement_defaults` for known pose fixes such as qpos, static/background loading, z policy, loader type, and articulated root-link behavior.

## Output

Return strict JSON matching `robotwin.tabletop_placement.v0`.

## Lessons From Runs

- `110_basket` looked sideways with identity qpos. Use the catalog default qpos `[0.70710678, 0.70710678, 0, 0]` so the basket opens upward.
- `015_laptop` is an articulated/URDF asset. It should use the catalog loader hint `sapien_urdf` and the RoboTwin-style qpos `[0.7, 0, 0, 0.7]`.
- Thin or unstable objects such as `034_knife` may be better treated as static reference/background objects for scene generation, then checked visually.

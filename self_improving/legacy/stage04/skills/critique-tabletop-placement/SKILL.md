# Critique Tabletop Placement

Use this skill when reviewing a Designer-generated `TabletopPlacementSpec`. In the agent loop, this is the Critic capability.

## Scope

Check scene-construction quality only:

- Prompt/object semantic match.
- Asset and model availability.
- Workspace bounds.
- Approximate collision risk.
- Stability metadata.
- Reachability and camera visibility risk.
- Whether the scene can support a later downstream robot task.
- Explicit spatial relations such as left_of/right_of/near/on/inside are represented and satisfied in the intended visual frame.

Do not judge policy training, data collection quality, or task success predicates.

## Static vs Visual Critic

- Static Critic checks JSON, assets, model ids, approximate bounds, and approximate collisions only.
- Static Critic must not mark a scene visually valid. It should require smoke render plus visual Critic/VLM review.
- A smoke pass means the simulator loaded and rendered the scene. It does not prove correct orientation, no penetration, or prompt match.
- If prompt semantics depend on view-relative language such as left/right, the Critic must compare against the preview image, not only tabletop-local coordinates.

## Output

Return strict JSON using `robotwin.tabletop_placement_critic.v0` with verdict `accept_for_next_stage`, `repair_required`, or `reject`.

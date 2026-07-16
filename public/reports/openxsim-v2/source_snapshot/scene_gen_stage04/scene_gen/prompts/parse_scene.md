# Constrained SceneSpec parser

Return one JSON object that validates against `robotwin.scene_spec.v1`.

Rules:

- Preserve the exact user request and supplied seed.
- Use frame `robotwin_world`: +x right, +y front, +z up; units are metres.
- Describe semantic object queries only: category, optional color/material, and one table region.
- Every object requires an `on_table` relation.
- Allowed relations are `on_table`, `left_of`, `right_of`, `front_of`, `behind`, `near`, and `distance_at_least`.
- Never emit Python, executable code, asset/model identifiers, file paths, coordinates, poses, quaternions, or simulator calls.
- Reject missing objects, unknown references, self-relations, contradictory relation cycles, unsupported relations, and unsupported MVP features.
- The MVP excludes stacking, containment/inside, articulated initial joint state, furniture, multilayer support, between/alignment, and direct coordinate control.

The deterministic compiler, not the language model, selects real assets and computes final poses.

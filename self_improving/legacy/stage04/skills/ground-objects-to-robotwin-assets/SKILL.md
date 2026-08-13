# Ground Objects To RoboTwin Assets

Use this skill when mapping natural-language object mentions to RoboTwin asset catalog entries.

## Rules

- Match prompt terms against `semantic_name`, `aliases`, and `tags`.
- Prefer exact semantic matches over broad category matches.
- Return uncertainty when multiple assets plausibly match.
- Never fabricate missing assets; mark them as unavailable or request a richer catalog.
- Preserve the selected `asset_id`, `model_id`, and metadata needed by the placement validator.
- Record `asset_type` accurately. Rigid GLB assets use `create_actor`; articulated/URDF assets such as `015_laptop` need the `sapien_urdf` loader path.
- Record `placement_defaults` for any asset with known orientation, scale, static/background, z-policy, or root-link requirements.
- Preserve RoboTwin's original spelling in `asset_id` even when it is odd, such as `069_vagetable`, and add natural aliases such as "vegetable".
- Treat `stable=false` metadata as a warning that the asset may need static/background loading and visual review.

## Known Asset Notes

- `069_vagetable`: RoboTwin spells the asset as `vagetable`; add `vegetable` as an alias.
- `110_basket`: use qpos `[0.70710678, 0.70710678, 0, 0]` for open-up visual orientation.
- `015_laptop`: articulated/URDF asset; use `sapien_urdf`, model id 0 / subdir `10040` for the MVP catalog, and qpos `[0.7, 0, 0, 0.7]`.
- `034_knife`: rigid but metadata says `stable=false`; treat as static reference object for scene-background placement unless the downstream task needs manipulation.

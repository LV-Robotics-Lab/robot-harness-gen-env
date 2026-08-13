# Asset Catalog Rules

Asset catalogs are the bridge from natural language to RoboTwin assets. Keep reusable asset metadata in the master catalog and use small prompt-case catalogs to select assets for one prompt.

## File Layout

```text
asset_catalogs/
  robotwin_tabletop_assets_master.json
  prompt_cases/
    apple_plate.json
    vegetable_basket.json
    laptop_knife.json
```

Use `robotwin_tabletop_assets_master.json` for reusable asset metadata such as aliases, model ids, scale, loader hints, qpos defaults, stability notes, and affordances.

Use `prompt_cases/<short_prompt>.json` for prompt-specific selection:

```json
{
  "catalog_version": "robotwin.tabletop_asset_catalog_case.v0",
  "base_catalog": "../robotwin_tabletop_assets_master.json",
  "mvp_prompt": "a laptop is on the right side of a knife",
  "selected_asset_ids": ["015_laptop", "034_knife"],
  "entry_overrides": {}
}
```

The harness expands prompt cases into the legacy `entries` shape before Designer, Critic, validation, and smoke run.

## Required Entry Fields

Each entry should include:

```json
{
  "asset_id": "034_knife",
  "semantic_name": "knife",
  "aliases": ["knife"],
  "tags": ["knife", "tabletop"],
  "asset_type": "rigid",
  "available_model_ids": [0],
  "default_model_id": 0,
  "model_metadata": [
    {
      "model_id": 0,
      "scale": [0.31, 0.31, 0.31],
      "approx_scaled_extents_m": [0.03, 0.46, 0.13],
      "stable": false
    }
  ],
  "placement_affordances": {
    "stable_on_table": false,
    "graspable": true,
    "support_surface_candidate": false,
    "role_candidates": ["reference_object", "scene_object"]
  },
  "placement_defaults": {
    "qpos": [1, 0, 0, 0],
    "is_static": true,
    "z_policy": "snap_to_tabletop_on_load"
  }
}
```

## Rigid Assets

Rigid GLB assets load through `create_actor`.

Use `model_dataN.json` for:

- `scale`
- `extents`
- `stable`
- points metadata if available

If `stable=false` but the object is only scene background/reference, prefer `placement_defaults.is_static=true` and require visual review.

## Articulated Assets

Articulated/URDF assets need loader hints:

```json
"asset_type": "articulated",
"placement_defaults": {
  "loader": "sapien_urdf",
  "qpos": [0.7, 0, 0, 0.7],
  "is_static": true,
  "fix_root_link": true,
  "z_policy": "snap_to_tabletop_on_load"
}
```

Example: `015_laptop` is articulated/URDF and should not use rigid `create_actor`.

## Known Asset Defaults

- `069_vagetable`: RoboTwin spells this as `vagetable`; add `vegetable` alias.
- `110_basket`: use qpos `[0.70710678, 0.70710678, 0, 0]` so the basket opens upward.
- `015_laptop`: use `sapien_urdf`, model id 0 / subdir `10040`, qpos `[0.7, 0, 0, 0.7]`, fix root link.
- `034_knife`: rigid but `stable=false`; use static background/reference loading unless a downstream task requires manipulating it.

## Do Not

- Do not invent asset ids.
- Do not omit `asset_type`.
- Do not copy the full master catalog for each prompt.
- Do not hide asset-specific pose fixes in ad hoc prompt code when they belong in `placement_defaults`.
- Do not assume a smoke pass means the visual orientation is right.

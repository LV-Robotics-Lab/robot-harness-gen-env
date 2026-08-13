# Visual Review Gate

The visual gate prevents bad scenes from being marked as successful just because RoboTwin rendered an image.

## Rule

```text
static pass + smoke pass != full pass
static pass + smoke pass + visual review pass = full pass
```

## What To Check

- requested objects are present
- no extra obvious wrong object dominates the scene
- spatial relations match the prompt in the preview image
- object orientation is plausible
- no obvious table penetration
- no severe floating
- no severe occlusion
- object identity is recognizable enough for the prompt

## Direction Frame

Judge natural-language directions from the robot / dual-arm first-person viewpoint. The external observer camera can mirror or rotate what the robot considers left/right/front/back, so do not accept or reject a scene only by screen-space left/right in `observer_camera.png`.

When reviewing a prompt such as `a laptop is on the right side of a knife`, check whether the laptop is on the robot's right side relative to the knife. If the observer image appears reversed, use head/robot-view evidence or RoboTwin pose coordinates to decide and repair.

## Failure Examples

- Basket appears sideways or penetrates the table.
- Laptop is placed on the robot's left when prompt says right of knife.
- A support/container object is loaded dynamically and falls or rotates.
- An articulated object is loaded through the rigid loader.

## Status Values

- `pending_human_or_vlm_visual_review`: artifacts exist but no semantic visual review has passed.
- `pass`: a human, Codex visual reference, or external VLM explicitly checked and accepted.
- `fail`: visual issue found; repair and rerun.

## Repair Hints

When failing, specify:

- object id
- visible problem
- likely fix: qpos, xyz, loader, static flag, scale, model id, or catalog default
- whether the fix belongs in code, `asset_catalogs/robotwin_tabletop_assets_master.json`, or a prompt case override under `asset_catalogs/prompt_cases/`

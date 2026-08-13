# Review RoboTwin Smoke Preview

Use this skill when checking RoboTwin smoke outputs from a placement run. Smoke success only proves that RoboTwin loaded and rendered the scene; it is not enough to accept the scene.

## Checks

- `smoke_report.json` exists and reports pass.
- `head_camera.png` and `observer_camera.png` exist.
- Optional `observer_camera.mp4` exists.
- The preview should be inspected by a human or VLM for object identity, prompt match, floating, penetration, severe occlusion, and wrong object count.
- Mark the scene failed when an object is visibly sideways, upside down, penetrating the table, floating, severely occluded, or semantically wrong.
- Mark the scene pending when no human/VLM/Codex-visual review has inspected the image.
- For left/right prompts, judge the relation in the preview image frame. The simulator coordinate frame can disagree with visual left/right.
- Check that container/support objects have plausible orientation: baskets should open upward, plates should lie flat, laptops should be recognizable as laptops rather than collapsed side views.
- When visual review fails, write a concrete repair hint: which object, what visual problem, and which qpos/position/loader/default should be changed.

This skill does not replace a visual model. It defines the review criteria and artifact contract.

## Recent Failure Modes

- Basket run: smoke passed while basket was visually side-facing. Fix was to probe orientations and store `110_basket` qpos `[0.70710678, 0.70710678, 0, 0]`.
- Laptop/knife run: static local coordinates put laptop on the wrong visual side. Fix was to make lateral language relations pass visual left/right in the observer preview.
- Laptop run: `015_laptop` required articulated URDF loading; rigid smoke loading would be the wrong interface.

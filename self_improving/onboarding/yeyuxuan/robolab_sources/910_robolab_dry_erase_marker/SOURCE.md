# Asset provenance

- Asset: YCB dry erase marker
- Source repository: https://github.com/NVLabs/RoboLab
- Source commit: 97bc1e766300a8c95656067ecb81e267e7e11823
- Source file: assets/objects/ycb/dry_erase_marker.usd
- Source texture: assets/objects/ycb/textures/obj_000018.png
- License: MIT
- Source physics: mass 0.02 kg, static friction 2.0, dynamic friction 2.0, restitution 0.1.
- Changes: USD world-space mesh extracted, bottom aligned to z=0,
  texture embedded into GLB, and convex-hull collision geometry generated.
- Collision limitation: A single convex hull is used; small cap and body surface details are simplified in the collision representation.
- Intended use: RoboTwin lightweight rigid-object placement and grasping validation.

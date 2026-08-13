# Asset provenance

- Asset: YCB Soft Scrub bottle
- Source repository: https://github.com/NVLabs/RoboLab
- Source commit: 97bc1e766300a8c95656067ecb81e267e7e11823
- Source file: assets/objects/ycb/soft_scrub.usd
- Source texture: assets/objects/ycb/textures/obj_000012.png
- License: MIT
- Source physics: mass 1.5 kg, static friction 2.0, dynamic friction 2.0, restitution 0.1.
- Changes: USD world-space mesh extracted, bottom aligned to z=0,
  texture embedded into GLB, and convex-hull collision geometry generated.
- Collision limitation: A single convex hull is used; cap grooves, neck transitions, and concave surface details are simplified in the collision representation.
- Intended use: RoboTwin rigid-object placement, stability, and grasping validation.

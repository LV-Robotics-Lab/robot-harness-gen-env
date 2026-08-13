# Asset provenance

- Asset: YCB clamp
- Source repository: https://github.com/NVLabs/RoboLab
- Source commit: 97bc1e766300a8c95656067ecb81e267e7e11823
- Source file: assets/objects/ycb/clamp.usd
- Source texture: assets/objects/ycb/textures/obj_000019.png
- License: MIT
- Source physics: mass 0.5 kg, static friction 2.0, dynamic friction 2.0, restitution 0.1.
- Changes: USD world-space mesh extracted, bottom aligned to z=0,
  texture embedded into GLB, and convex-hull collision geometry generated.
- Collision limitation: A single convex hull is used; the clamp opening, handle gaps, and concave regions are filled in the collision representation.
- Intended use: RoboTwin rigid-object placement and grasping validation; not suitable for functional clamping interactions.

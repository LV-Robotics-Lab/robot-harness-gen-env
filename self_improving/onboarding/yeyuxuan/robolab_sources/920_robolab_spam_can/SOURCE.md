# Asset provenance

- Asset: YCB Spam can
- Source repository: https://github.com/NVLabs/RoboLab
- Source commit: 97bc1e766300a8c95656067ecb81e267e7e11823
- Source file: assets/objects/ycb/spam_can.usd
- Source texture: assets/objects/ycb/textures/obj_000009.png
- License: MIT
- Source physics: mass 0.35 kg, static friction 2.0, dynamic friction 2.0, restitution 0.1.
- Changes: USD world-space mesh extracted, bottom aligned to z=0,
  texture embedded into GLB, and convex-hull collision geometry generated.
- Collision limitation: A single convex hull is used; lid seams, rim grooves, and small surface details are simplified in the collision representation.
- Intended use: RoboTwin rigid-object placement, stability, and grasping validation.

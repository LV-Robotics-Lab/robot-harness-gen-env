# Gujie scene adapter: finite tabletop candidate

Public seams: `adapt_gujie_tabletop_scene(store, *, source_ref, representation_ref,
table_center_m, table_size_m, table_friction) -> ArtifactRef` and
`read_gujie_tabletop_scene(store, scene_ref) -> GenesisSceneSpecV1`.
These compile a portable candidate and independently recompute it; neither executes physics
nor issues asset/Skill/environment authority. No separate mutable state is introduced.

Source is a canonical JSON CAS envelope: `representation` binds the selected representation;
`scene` retains the Gujie/SimFoundry OmniGibson scene object declarations/state and ground;
`metadata` contains the single source object's name/category/model; `relation` explicitly
requests `on_table` from that source name to `table`. Image/proposal/package/binding refs may
be bound by the enclosing compile evidence; this tracer envelope is not image understanding.

First profile: one dynamic single-link rigid URDF, full visual and collision vertices,
OBJ meshes or boxes, axis-aligned finite fixed Box table, horizontal ground at z=0.
Source XY and normalized source orientation are preserved. Only Z changes to put the
lowest transformed vertex 0.001 m above the tabletop. Every transformed visual/collision
vertex must lie within the finite tabletop with 0.01 m margin. No XY rescue/search, tilted
support, joints, scene scale/physics override, custom USD or visual-only body is accepted.
The geometry artifact records source pose, candidate pose, full vertices and fixed origins.
The pure reader recomputes the geometry and scene from source/representation CAS bytes.

## Fixed origin and actual transplantation

Origin responsibility: Gujie x2env; integration responsibility: Bingsheng Harness workstream.
Git identities remain recorded identities, not inferred natural-person mappings.
Source repository `/home/jingxiang/gujie/gen-env`, commit
`eb0b710581fd7794bc01b447b0f77cb871c8a711`:

| Source | SHA256 | Adapted deterministic behavior |
| --- | --- | --- |
| `self_improving/sim_adapters/genesis/import_simfoundry_scene.py` | `f424ab33de5a195b53fa94f365787e8ff9a8c69be086a5f5f3de3061b6a4826e` | source root placement, OG xyzw→wxyz, declaration/state identity, ground flags and unsupported-state rejection |
| `self_improving/sim_adapters/genesis/standard_urdf.py` | `8d94fee8cca8fa3839e216131e4e60f32561a6db602d47a04297a978bb96a856` | independent single-rigid-link geometry, mesh scale/origin transforms, authored physical fields |
| `self_improving/sim_adapters/genesis/position_solver.py` | `9388dd392d9c2fe33cf10dae916c27f7383abef7cbcc5ee0917f3622812d723e` | explicit fixed support, complete target-local footprint, 0.01 m margin / 0.001 m clearance, preserve orientation |

This is a bounded transplantation of the source's deterministic equations/validation into
Harness CAS and typed scene contracts. It does not copy the vendor tree or import a dirty
worktree. SimFoundry reconstruction and Genesis execution remain separate upstream-owned
dependencies. The original general convex solver/search, source cameras/background, visual
support tricks, source pass reports and raw absolute-path packages are not adopted here.
Unlike the upstream importer's measured-ground relation inference, `on_table` is mandatory
and remains a finite table relation; it is never converted into the old single-asset ground probe.

## Adoption map by function

- `import_simfoundry_scene.vector/quaternion/read_objects/environment` → adapter `_vector`,
  `_quaternion`, `_source_pose`: validation order and coordinate/identity semantics adapted
  to a one-object CAS envelope. Numeric implementation uses Python tuples/math instead of
  NumPy; this is adapted reuse, not a wholesale import of the upstream module.
- `standard_urdf.inspect/transform` → `_geometry`: directly carries over the rigid-link gate
  and `points * mesh_scale`, then `points @ R.T + origin` behavior for every visual/collision
  node. The current decoder restricts meshes to OBJ vertex records; it does not claim the
  upstream Trimesh scene-graph decoder or arbitrary mesh format support. Authored inertia
  validity uses symmetric-matrix principal-minor conditions instead of upstream eigensolving.
- `position_solver.solve` support-local checks and `candidates` vertical placement → `_derive`:
  the rectangular axis-aligned target specialization checks every transformed vertex at
  margin 0.01 m and sets candidate Z to target top + 0.001 m − lowest rotated vertex.
  Unlike the general upstream solver, no convex polygon clipping, search/backtracking,
  multi-object topology, collision avoidance, or translation-budget search is implemented.
- Harness additions: content-addressed source/representation binding, full member rehash,
  canonical geometry/spec artifacts, side-effect-free regeneration, relocation and tamper
  checks. These are new Harness contracts, not capabilities attributed to Gujie.

The adapter source envelope is only a mapping tracer until native image compile binds the
actual media ingest, interpretation proposal, durable asset qualification, source placement
and resulting scene. It cannot stand in for that complete M1 path.

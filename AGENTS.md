# Robot Harness /gen-env Contributor Rules

## Scope

This repository owns `text -> SceneSpec -> ResolvedSceneSpec -> RoboTwin/SAPIEN
evidence`. Do not add policy execution, collection, training, evaluation, or
cross-simulator transfer here.

## Acceptance Rules

- A render is not physical evidence.
- Nested sources must be dynamic and contact the declared target.
- Never accept stacking through `is_static`, contact-free poses, outer AABB
  overlap, or start/end screenshots alone.
- Support and containment calculations must be target-local and account for the
  complete source footprint.
- Runtime evidence must remain hash-bound to the resolved scene.
- A requested video must retain real sequential frames and report its total and
  unique frame counts.
- New asset overrides require measured geometry or a documented simulator probe.

## Tests

Run `pytest -q` locally. Changes to support, containment, loader, or validator
contracts also require a real RoboTwin/SAPIEN replay on a supported machine.
Keep attack tests for every discovered false-positive mode.

## Data And Secrets

Do not commit RoboTwin assets, generated datasets, checkpoints, API keys, SSH
credentials, local configs, or bulk runtime output. Small evidence samples may be
committed only when they are needed to explain an acceptance contract.

## Git

Keep changes scoped to `/gen-env`. Preserve upstream attribution and avoid
rewriting published history.

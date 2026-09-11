# Golden X2 typed exact-reuse compile closure evidence — 2026-09-11

## Result

`PASS`, fixed at implementation commit
`605f030cd271c090de14eb0115c28cd900de2f07`.

This result is limited to strict typed evidence and a pure-read compile-closure verifier. It does
not claim an image2env handler/application, a production asset qualification issuer, Genesis/GPU
execution, replay, publishability, MCP exposure, promotion, or robot-policy readiness.

## TDD history, including failed acceptance

1. `327dd80` froze twelve typed compile-evidence schemas as RED; `33d0695` made them GREEN.
2. `fd3d0e9` froze the exact-reuse deep-consumer seam; `314639c` made its initial graph GREEN.
3. Independent acceptance then found two false positives: an unselected available catalog entry
   could carry missing representation/qualification refs, and static validation could be a
   `noop/pass` record with no evidence.
4. `c2c6f57` preserved both findings as public-seam RED; `7a992f9` consumed all catalog refs and
   required the exact four static checks.
5. The next independent acceptance found another false positive: an unselected qualification
   could use `noop/pass`, or use the right check names with unrelated existing CAS evidence.
6. `f1aa527` preserved both qualification findings as RED; `605f030` applied one shared semantic
   gate to selected and unselected qualifications.

No failed acceptance result was removed or rewritten as a pass.

## Typed evidence boundary

Twelve former opaque ArtifactRef labels are now strict, self-hashed public models:

- scene interpretation proposal;
- portable asset catalog;
- asset representation;
- Genesis asset qualification;
- x2env asset selection;
- SceneIR and TaskIR;
- scene asset-closure manifest;
- x2env binding manifest;
- Genesis runtime lock;
- x2env static validation;
- x2env compile receipt.

The public schema catalog grew from 72 to 84 committed snapshots. Every snapshot is independently
valid Draft 2020-12, and the checked snapshots match the live exporter.

Asset qualification is intentionally single-asset and reusable across scenes. It binds one
representation, its loader-closure identity, the fixed runtime lock/profile/issuer and exact
load/collision evidence. The scene closure is a separate compile aggregate.

## Deep verifier boundary

`ExactReuseCompileClosureVerifier` accepts only the first constrained slice:

- image input;
- initial compile attempt;
- `exact_only` acquisition;
- `genesis.rigid_scene@1`;
- harness-native exact asset reuse;
- no model execution, repair, legacy adapter or physical/publishability claim.

It re-resolves the source image evidence and every typed document from one exact
`LocalArtifactStore`. Every available catalog entry, including unselected entries, must have a
present and byte-correct representation, every representation member, a trusted qualification and
every qualification evidence ref. Subject, representation digest, loader closure, runtime lock,
profile and issuer must agree.

Each qualification has exactly `backend_load` and `collision_probe`. Every check must include both
that qualification's representation and the fixed runtime lock. Static validation has exactly:

- `asset_closure_complete = {selection refs, asset closure}`;
- `binding_complete = {binding manifest}`;
- `runtime_lock_bound = {runtime lock}`;
- `scene_task_consistent = {SceneIR, TaskIR}`.

Verification is pure-read: successful and rejected calls leave the CAS byte manifest unchanged.

## Executed gates

- Final focused closure tests: `9 passed`.
- Deep verifier: `219/219` statements and `52/52` branches, 100%.
- Adjacent schema/x2env/image ingest regression: `79 passed`.
- Full repository suite: `4098 passed, 20 skipped`.
- Public schemas: `84` committed snapshots.

## Independent catalog and qualification acceptance

Fixed archive and fresh evidence root:
`/tmp/golden-x2-catalog-final-605f030.Oa5Ppx/evidence`.

The positive case contained two available catalog entries, two representations and two
qualifications. All were consumed from 31 CAS files / 51,900 bytes. Fourteen negative cases covered
missing or tampered representations, qualifications, members and check evidence; untrusted
qualification; runtime/profile/issuer/subject drift; `noop` checks; and correct check names backed
only by unrelated existing evidence. All fourteen were rejected with stable artifact or semantic
failure codes, and all verifier calls wrote zero CAS objects.

`catalog-matrix-summary.json` SHA-256:
`4d0ce6634bdef60873bf6644192a52211d0669d0dc615b3fbbb196482c3cc032`.

## Independent static and returned-closure acceptance

Fixed archive: `/tmp/x2-final-audit-2RPcmZ`; fresh evidence root:
`/tmp/x2-final-audit-2RPcmZ-evidence`.

The positive graph used 20 CAS files / 28,236 bytes. Recursive traversal of compile input, output
and the returned verified graph found 84 ArtifactRef occurrences / 20 unique identities; all 20
resolved with exact bytes and SHA-256. `noop/pass`, missing check, extra check, wrong evidence and an
extra unrelated-but-existing prompt artifact were all rejected. Every invocation left CAS
path/size/SHA unchanged.

## Provenance and limits

The implementation is Harness-native. It does not copy Gujie runtime source bytes. Gujie's fixed
source remains useful adapter research, but no legacy output is treated as a trusted qualification
or publishable package. The next slice is a controlled deterministic image exact-reuse
builder/handler that must return through this verifier; production qualification remains blocked on
a real Genesis asset issuer and runtime evidence.

# Genesis rigid-scene assessment

`GenesisSceneAssessment.validate(replay_run_id)` consumes the exact trusted
`genesis_scene_application_v2.GenesisSceneApplication` and a successful **registered** `image2env.replay@2.0.0`
SQLite terminal. Candidate terminals alone are insufficient. The implementation
checks the unique execution receipt, terminal output, invocation digest,
implementation dependencies, effective request and approved renderer manifest.
`GenesisSceneExecutionVerifierV2.read_receipt` recomputes the complete physics and
sequential-media closure before publishing validation. The public `resolve_input`
also checks the registered compile terminal from the V2 input envelope.

The resulting assessment scope is exactly `genesis.rigid_scene@1`. It does not
rerun Genesis, measure real material properties, evaluate robot policies, or
grant promotion authority. Assessment time is explicitly not capture time.

`GenesisSceneValidationApplication` owns the `image2env.validate@2.0.0` candidate
through the existing Registry, SQLiteRunStore and SQLiteEventJournal. Two distinct
successful candidate terminals must independently assess two successful replay
qualification candidates. Dedicated `validate_candidate` and `observe_candidate`
entrypoints require candidate events and set `candidate_only=true`; the production
methods require registered replay events and set it to false. The evidence-invariant
registration policy rechecks both terminals, source
lineage and implementation bytes on registration and restart. Registered invoke
and read also recheck the returned validation and its source. Candidate reports
alone cannot be registered through an arbitrary public issuer.

Two actual independent V2 replay candidates have now produced successful observation
and validation candidates. Both assessment Skills were qualified and registered by
the formal public kernel test; the subsequent registered four-Skill production
workflow remains a separate acceptance step.

`observe(run_id, ttl_seconds=...)` accepts only integer TTLs from 1 to 300 seconds
and the same trusted replay lineage. It consumes the final half-dt capture event
(step 2000, simulation time 4 seconds) from V2 process observation. V1 evidence
cannot satisfy this interface. Neither file modification times nor the
assessment/receipt completion timestamp can redate existing media.

The V2 renderer records an actual UTC timestamp adjacent to camera
capture and binds it to the same frame bytes, step, profile and execution request.
Its observation interval is closed `[captured_at, captured_at + TTL]`; later reads
reject expiration; this read method never reruns the simulator. A future
`genesis.observe@2.0.0` contract must preserve this distinction from the controlled
`genesis.observe@1.0.0` tracer.

`GenesisSceneObservationApplication` supplies exact `genesis.observe@2.0.0` through
the same Registry kernel. Its two qualification cases consume two distinct replay
candidates. First invocation checks actual wall-clock freshness after deep verification.
`read` and idempotent retry verify that the original `checked_at` was within the
capture lifetime; they preserve that historical receipt and never claim fresh capture
at the time of a later read. Actual capture-to-check delays were 214.148675 and
250.842754 seconds under the strict 300-second TTL. A later check of the second
actual capture at age 1025.150066 seconds was rejected without changing its receipt.

V2 makes browser-playable MP4 previews and the retained-output index directly
reachable from typed launch/receipt evidence. Lossless videos and PNG sequences
remain part of replay closure. The qualification summary is
`/home/jingxiang/bingsheng/golden-scene-runtime-v2-sorted-20260911.0h7liezw/assessment-qualification-summary.json`
(SHA-256 `412c312add6058d022a13729d4aa40a9ff83b75094471cb314ef8fe6bb67e2c7`).
The formal qualification test passed in 788.12 seconds without rerunning Genesis.
This does not grant robot-policy or promotion authority, and statement/branch
coverage has not reached the requested 100% gate.

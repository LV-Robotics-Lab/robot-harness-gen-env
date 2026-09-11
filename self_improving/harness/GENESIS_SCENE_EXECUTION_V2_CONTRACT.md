# Registered image compilation to scene replay v2

`GenesisSceneApplication` in `genesis_scene_application_v2.py` consumes
`GenesisSceneReplayInputV2`: a registered compile run UUID and a canonical
`GenesisSceneExecutionRequestV2` CAS reference. The application is configured with
the exact trusted native compile application. `resolve_input` requires its existing
successful registered terminal, performs public same-UUID deep recovery, and binds
the terminal output to the request's compile receipt, scene, asset qualification,
and operator-approved scene manifest. Missing or candidate compile runs cannot
cause implicit compilation.

The scene handler executes the fixed v2 launcher through the producer. Successful
execution alone returns the opaque live capability. CAS-only verification returns
`execution_authority=False`; its physical report remains
`trace_consistency_only`. Asset and scene runtime manifests are separate proofs.

Both baseline (1,000 steps at 0.004 s) and half-dt (2,000 steps at 0.002 s) retain
all rows, all 24,573 measured vertices in the mouse case, and 41 actual captures.
Capture timestamps are recorded immediately after camera rendering. Each event
binds step, simulation time, decoded RGB digest, dimensions, and PNG CAS bytes.
Launch and execution receipts directly retain both browser MP4 previews and the
complete retained-output index. Lossless video is decoded and compared with every
PNG; lossy previews are checked for complete frame timing and dimensions.

The batched host reader evaluates every vertex and row in bounded batches; it
does not sample or substitute a bounding box. Its qualification identity includes
the actual NumPy distribution RECORD file closure, native members, version, and
Python ABI. This identifies the host mathematical implementation, and does not
claim a complete host runtime seal. The separate scene runtime manifest identifies
the fixed simulator child. Install the `genesis-scene` extra for NumPy 2 support.

The existing SkillRegistry, SQLiteRunStore, and SQLiteEventJournal remain the
durable kernel. Two successful candidates from distinct registered compile runs
are required for the evidence-invariant replay qualification. The qualification
binds implementation, approved manifest, compile qualification, candidate
invocations, and complete verified receipts. Production uses
`image2env.replay@2.0.0` with v2 input and output. Replay is not claimed to be
byte-deterministic. Retry and restart must revalidate the same trusted terminal
and CAS closure. An intent-only run does not silently execute a second simulator.

Version 1 schemas, source identities, and historical lower-level evidence retain
their original meaning. A successful historical v1 candidate cannot qualify the
final registered-compile v2 workflow. Neither this receipt nor its physical report
evaluates robot policy or grants `genesis.robot_policy@1` authority.

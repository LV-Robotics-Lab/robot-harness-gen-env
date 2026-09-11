# Independent fresh camera observation

`genesis.observe@3.0.0` takes `GenesisFreshCaptureInputV1`: an existing registered
`image2env.replay@2.0.0` run UUID and an integer TTL from 1 through 300 seconds.
The exact trusted replay application, its registered qualification and durable
successful terminal are required. Replay qualification candidates are not accepted
as production sources. Two fresh observation candidates may use the same already
registered replay, but must execute two independent captures with distinct results.

Before starting the camera process, the producer deeply verifies the registered
replay, compile lineage, full dual-profile evidence and final half-dt row (step
2000, simulation time 4.0 s). The new request binds this row and scene to a separate
operator-approved capture runtime manifest. The original replay runtime and Skill
qualification remain independent and unchanged.

The fixed capture child restores the final object's position and wxyz orientation
in Genesis entity user coordinates, sets all six DOF velocities to zero, and
performs zero physics steps. It renders one new fixed 640 by 480 camera frame.
This is `restored_final_pose_zero_velocity`, not continuation of the original
process or restoration of the original nonzero velocity. Raw load evidence records
both requested source state and measured restored state, finite table and ground,
collision bodies, fixed camera parameters and this velocity policy.

Actual UTC capture time is recorded after rendering returns. The result binds PNG
bytes and decoded RGB digest to the raw event, process environment and interpreter
identity, module/native manifest members, observed restrictions, streams and full
retained-output index. The producer verifies this closure, then checks freshness
against the original capture time and writes the authoritative observation receipt
last. It alone returns an opaque live capability. Pure reads return a verified
historical closure with no live execution authority and do not renew timestamps.

The application uses existing SkillRegistry, SQLiteRunStore and SQLiteEventJournal.
Qualification rechecks two successful local candidate terminals, distinct capture
results, current host source identities, the approved capture manifest and replay
qualification. The Skill qualification is the final authority write. Production
invocation is idempotent through the existing controlled run intent. Pending runs
are not silently rerun; retries of terminal runs recheck stored evidence without
claiming a new camera event. A new successful invocation returns the receipt from
the actual producer without redundantly rereading historical replay after capture.

Historical `genesis.observe@2.0.0` remains a pure read of replay-time media. A fresh
capture does not validate robot policy, grant environment promotion, or claim that
the captured scene is a live continuation.

# Image2env M1 workflow assembly

This assembly coordinates the existing qualified applications. It owns no mutable
run table, qualification issuer, simulator implementation, or promotion authority.
`GoldenRunHarness` supplies the existing design rule: recover durable authority and
verify reachable evidence before reporting success. This bounded M1 assembly does
not create or advance a GoldenRun state and is not the M2 MCP controller.

## Public seam

`Image2EnvGoldenWorkflow(compile_application, replay_application,
observation_application, validation_application)` accepts the exact production
application types. Both assessment applications must reference the same replay application.
The configured replay application, through its public `resolve_input`, remains
responsible for verifying the registered compile terminal and approved runtime.

`run(replay_input, *, replay_run_id, observation_run_id, validation_run_id, ttl_seconds)` consumes a
registered native compile UUID through `GenesisSceneReplayInputV2`, invokes exact
`image2env.replay@2.0.0`, immediately invokes qualified `genesis.observe@3.0.0`, invokes
exact `image2env.validate@2.0.0`, and writes one canonical CAS audit summary only after
all segments have been deeply verified by their applications. UUIDs remain owned by existing application
run stores. Reusing an existing terminal never authorizes another simulation.
Expired captures fail; no timestamp refresh or invented observation is permitted.

The V3 observation must perform an actual post-replay capture and deliver a fresh
result after its verification. The previous V2 read of the replay's last frame
cannot satisfy this requirement. The first actual four-Skill attempt reached a
successful replay but failed the V2 freshness gate: capture to replay terminal
153.632 seconds, observation preflight 223.699 seconds, refusal 314.406 seconds.
The complete failed attempt remains evidence. Starting `checked_at` earlier,
increasing TTL, or skipping deep verification is not an accepted repair.

V3 records `observed_restored_final_pose`: a separate capture process restores
the verified replay's final pose with zero velocity and takes a new image. Its
receipt binds the source replay, capture request/result and image, and explicitly
sets `process_continuation=false`. It adds no new physics steps and must not be
presented as a live continuation of the original replay process.

An existing successful replay may support an explicitly recorded observation
repair attempt. Final acceptance still requires one new workflow invocation that
performs replay, then actual fresh capture, then validation without substituting
an older observation or retaining an expired image as current evidence.

All four exact Skills must already be registered before starting the line. The
assembly does not qualify applications as a side effect of execution.

The summary records compile/replay/observe/validate UUIDs, registered qualifications,
invocations and durable terminal snapshots, compile request/receipt, execution
request/receipt, scene, physics report, retained media index and preview videos,
fresh observation and validation references. A summary describes checked lineage;
it is not an additional physical or state authority. On failure, existing app
diagnostics and terminal states remain authoritative; no success summary is emitted.

`run` returns `GoldenWorkflowDelivery(summary_ref, delivered_at, expires_at)` only
after a real UTC clock check immediately following the CAS write. At equality with
expiration delivery remains valid; even one microsecond later fails. The assembly
also checks the real clock just before that write. A post-write expiration raises
`GoldenWorkflowDeliveryExpired` carrying `unpublished_summary_ref`; those bytes
remain a retained failure sample and do not constitute a successful delivery.
The delivery record itself is an audit record, not physical authority.

After the four public invokes, assembly consumes their already deep-verified typed
outputs. It checks small CAS bytes/hashes, terminal stability, exact receipt/output
equality and cross-stage lineage. It must not repeat application `read`, assessment
`read_replay`, or execution `read_receipt` during assembly. The formal test records
actual Python call events to check this rule without replacing any Harness method.

## Required integrity checks

- Reject caller-supplied application substitutes before any side effect.
- Require successful registered compile/replay/validate terminals, exact Skill
  versions, and equality between persisted inputs, outputs and receipt references.
- Resolve compile through its production application's public terminal recovery;
  resolve replay through the public assessment; resolve validation through its
  production application's public read. Do not access private helpers or copy state.
- The replay input compile UUID must match the chosen trusted compile application;
  replay receipt/request and validation/fresh observation must refer to this run.
- Preserve source image/proposal/seed and independent asset/runtime qualification
  identities. Three current inputs are historical simulated mouse views with an
  explicit operator-authored reuse interpretation, not fresh reconstruction.
- Missing members, changed source identities, pending/failed/candidate terminals,
  unqualified Skills, stale captures and lineage mismatch fail closed.

## Acceptance

Public RED starts with rejection of fabricated authorities. GREEN must include a
real registered line using the actual applications, fixed scene runtime and CAS.
No fake Harness application or private helper may supply the successful fixture.
The real line must retain camera capture time/TTL and sequential media. It proves
only the `genesis.rigid_scene@1` scope; robot policy, promotion, autonomous vision,
new asset generation and M2 routing remain outside this contract.

# Golden MCP three-Skill real evidence — 2026-09-11

## Outcome

Fixed implementation commit `b6f779659606fb3b91b7a6a2c98ff6a03970c83c` completed a real
RoboTwin/SAPIEN workflow through two official MCP 2.2 stdio server processes:

```text
harness_workflow_create_v1      revision 0
text2env.compile@1.0.0          revision 0 -> 1  succeeded
text2env.replay@1.0.0           revision 1 -> 2  succeeded
text2env.validate@2.0.0         revision 2 -> 3  succeeded
server restart + validate retry revision 3       same terminal operation
```

The evidence root is
`/home/jingxiang/bingsheng/golden-mcp-real-20260911.3CZQqP/evidence`. Its canonical
`summary.json` has SHA-256
`b0bced244cad3192cc02ffd2c0b7a0fd96eaa2bc072595fabdb915b6d34f32f9`.
The official client negotiated protocol `2026-07-28` and listed only workflow create plus the
three exact qualified text2env tools. No generic invoke tool was present.

## Failure retained before the passing run

The first real attempt is retained at
`/home/jingxiang/bingsheng/golden-mcp-real-20260911.BCRFXA/evidence`. It reached revision 3 and
retrieved the video, then the evidence runner accessed the MCP client's protocol property after its
context had closed. That post-run lifecycle bug produced no summary and prevented the restart check.
Commit `b6f7796` captures the negotiated string while the session is live. The passing run used a
new evidence root; no failed output was overwritten or promoted.

## MCP, workflow and restart closure

The run used RegistrySnapshot
`f723d8be740d859e91cef1b37bbf01d528a596a58ae22f7f476be7b4cea2aa8b`. Settings, workflow,
the three controlled intents and live qualification identities agree. Independent source closure
checks covered 11 compile, 50 replay and 15 validate manifest members with no byte or digest drift.

After process A exited, process B reopened all four SQLite authorities and the shared CAS. Repeating
the same workflow-create request returned revision 3, and repeating the same validate command
returned operation `44000000-0000-4000-8000-000000000003` byte-for-byte. The durable row counts
remained one workflow start, three workflow operations, and one intent/invocation/terminal RunState
for each child. Compile/replay/validate event counts were 14/19/4. The runner treats any pre/post
row-count or CAS-manifest change as fatal.

The shared CAS contains 201 objects and 61,836,772 bytes. Every object filename matches its content
digest. Scanning 169 JSON documents found 3,928 recursive ArtifactRef occurrences and 189 unique
content identities; every URI, byte count, digest and target object agrees. The canonical CAS
manifest digest is `d4aa557ed869cd9583ce1d406f07dcc70949f2edf0ee8a72c68e8f2b192b9205`.
The revision-0 start receipt and three operation receipts form a complete predecessor chain.
Their receipt digests are `7608ade6...` (start), `b1b917f2...` (compile), `4266afc0...`
(replay) and `43cdf8dd...` (validate). Applying each delta independently reproduces state hashes
`64bc3b58...` → `95014c00...` → `91cdf747...` → `ededf7a1...` exactly.
StateDelta keys are exactly:

```text
[assets.catalog, environment.package]
[replay.runtime_evidence]
[environment.validation]
```

All three ToolResults have zero fresh observations. Compile package and catalog are taken from the
compile trusted receipt and enter replay unchanged. The validate input resource is built from the
revision-2 compile/replay child journals and portable receipts, and validate independently recomputes
the physical report before committing its minimal state delta. Strict loading of the republished
compile/replay portable receipts closes 13/50 objects. Their RunState models equal the trusted child
RunStates field-for-field; canonical republishing intentionally gives them different CAS byte refs,
so this evidence does not claim they are the same ArtifactRef.

## Physics, assets and media

Replay runtime evidence
`82317c09c39b8bbf51f5e2a13e5eb3374bb43780633f149727156d24cfb9072c` reports 900 base/total
simulation steps, a 120-step contact window, 120 source frames, 100 source-unique frames and 12 fps.
The can-to-plate and plate-to-table dynamic contact fractions are both 1.0. The can support margin is
0.009692948 m against a 0.008 m minimum; penetration is zero, neither body dropped or remained in
motion, and initial/final robot collision counts are zero.

The runtime report
`01e85c898631343c46df50ecb2fb26dd0b4e9c2e424424553004f19b2ef09d75` has 37 checks. Validate
emitted an independent 39-check snapshot report
`054bb51927c95308a182773870cb2ccd68e07eb47233a0d031b8a989b94c713b`, adding package-manifest and
snapshot-binding checks; both reports pass with zero failed or not-run gates. Decision
`70c7dacff6547c0644b25e352b6efc349a82a853c103fc67f0bbbde50f044357` is publishable with no
blockers for the legacy profile.

The runtime asset closure has 23 members and 57,291,124 bytes: `003_plate` contributes four members
and 2,410,510 bytes with tree `9e750068...`; `071_can` contributes 19 members and 54,880,614 bytes
with tree `9c8aeee4...`. Every member and both materialized read-only trees match.

The workflow-scoped MCP resource returned the replay MP4 as a BlobResourceContents. The copied file
is:

`/home/jingxiang/bingsheng/golden-mcp-real-20260911.3CZQqP/evidence/view/observer_runtime-a41d05366050.mp4`

It is 61,285 bytes with SHA-256
`a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e` and is byte-identical to
CAS. Independent ffprobe/ffmpeg decoding reports H.264 High/yuv420p, 320×240, 12 fps, 10 seconds,
120 frames and 114 decoded-unique frames; the decoded raw frame-stream SHA-256 is
`634793034997be99789ec6f0f05fbaee0263ff75d3b34631a02e6c6584f54c6e`.

Runtime stderr contains one OIDN CUDA-unsupported line, 123 OIDN invalid-handle lines and one PyTorch
warning. They were non-fatal: process exit was zero, the event transcript closed, physics passed and
the complete video decoded. They remain recorded as renderer diagnostics rather than being hidden.

## Gates and boundaries

- New MCP/Golden/builder core: 919 statements / 244 branches, 100%.
- Focused controlled MCP/Golden tests: 238 passed.
- Qualification landing: 61 passed.
- Repository root with MCP/demo/dev/build dependencies: 3,782 passed, 20 skipped.
- Changed Python Ruff, format and diff checks: pass.
- Dashboard portfolio/tasks/project URLs: HTTP 404; no dashboard sync is claimed.

The exact qualified delegated cgroup is machine-local and disappears after the systemd scope ends.
The CAS, SQLite journals and evidence remain durable, but another real replay must explicitly recreate
`golden-r3-q-h6ng8i.scope`; a different locator requires replay and then validate requalification.
Process A stderr was retained as an empty file; process B retained only the empty-content digest.
The successful official client exchange demonstrates clean protocol framing, but raw stdout frames and
the pre-restart manifest were not separately archived.

This is a real MCP transport and legacy RoboTwin/SAPIEN compile→replay→validate closure. It is not an
external Codex planner run, Genesis-native validation, promotion, `genesis.robot_policy@1`, image2env,
video2env, cross-machine provisioning or real-robot evidence.

# External Codex controlled diagnosis and prompt revision — 2026-09-11

## Result

PASS for the controlled external-Codex/MCP diagnosis slice. Codex CLI `0.153.4` connected to the
repository's exact MCP server, requested four workflow-scoped resources, retained their durable
delivery receipts, recorded an advisory at revision 3, and recorded an explicit prompt revision at
revision 4. The trusted world-state hash and the two existing child runs did not change.

This is not a Genesis runtime result. The observation fixture states `simulator_executed=false`;
the advisory and prompt revision state `external_agent_executed=false`; no physical-validity,
publishability, promotion, or robot-policy authority was granted. A resource-access receipt proves
that the server delivered bytes for a request. It deliberately does not claim transport
acknowledgement or model understanding.

Machine-readable summary: `golden-external-codex-diagnosis-20260911.json`.

## Fixed identities

- Runtime compatibility fix: `6e0314461c536215a3d66e42781f67e397b260fa`.
- Final exact-read verifier: `bff09d2cba072f5fec73a972c069c4f2a7042169`.
- Evidence root:
  `/home/jingxiang/bingsheng/golden-codex-diagnosis-20260911.jyB0nn`.
- Codex protocol: `2025-06-18`; Python MCP restart verifier protocol: `2026-07-28`.
- Final workflow: `71000000-0000-4000-8000-000000000001`, revision/turn `4/4`.
- State before and after:
  `34f7a8216a05a0894a2349721b03938f59eedc02921c7db73c24f7647e60ed1d`.
- Advisory receipt:
  `a7aff8bd46d994397097ac32448aba1b63cbf60b0940ae00e432b3d003c7b43e`.
- Prompt revision:
  `75353517f00af4c8af159f9310aff7739dde6fb5739ab6b9e51bce5bb4b2be37`.

## What actually ran

Codex used only the project MCP server to request the source prompt, blocked workflow-operation
receipt, controlled Genesis-observation receipt, and diagnostic. Each response included the
original resource plus a server-owned `harness.resource_access_receipt.v1`. The four receipt targets
exactly cover the four evidence sources cited by the advisory.

The first advisory call was rejected because the receipt URIs were not in canonical order. Codex
retained the failure, sorted the same four receipt URIs, and resubmitted without rereading. The
advisory then committed revision 3. The next exact control recorded a full prompt replacement and
the explicit video-frame-to-image lineage at revision 4. Neither control created a child run or a
StateDelta.

The successful event log is
`codex-events-attempt-06.jsonl` (SHA-256
`d9b4108007412194703bd4c69feb0b9c1ac6938d541a7071a26ca4d6df4cc1f3`). The raw client-to-server
and server-to-client streams have SHA-256
`6e5989906d2b8bcd0daa3461c80b6de6abb31ca684bb7138fde2e7a4f7180b98` and
`2f75058c8dc4e4fa740692031182b01d75dbd2c7e333a25faf0929a5cf5d6550`; server stderr was empty.

## Restart and integrity verification

A new official MCP 2.2 client/server process, with no live application from the Codex process,
resubmitted the exact advisory and prompt-revision requests. Both returned the original terminal
records. Before/after SQLite row counts and the complete CAS file manifest were identical:

- starts 1, Skill operations 2, control records 2, turns 4, resource accesses 4;
- CAS 45 files / 75,985 bytes;
- revision/turn remained 4/4, state hash unchanged, child-run count remained 2.

`restart-verification.json` has SHA-256
`2595885c2037de6d9e0a792e06b0435c4946334ab832eff20706fa726046c04a`; its stderr is empty.
Independent focused acceptance also exercised complete, partial, unrelated, and zero-read sets.
Only the complete exact set can commit; every rejection leaves the workflow head and shared-turn
journal unchanged.

## Preserved failed attempts

1. Attempt 1 failed before reads with an invalid MCP handler result.
2. Attempt 2 added resource discovery but still failed at `tools/list`.
3. Attempt 3 captured the wire and isolated the incompatibility: the `2025-06-18` serializer requires
   the root `outputSchema.type` to be `object`.
4. Attempt 4 successfully read all four resources, then the client approval policy blocked mutation;
   no control record was written.
5. Attempt 5 used incompatible approval/sandbox CLI flags and exited before a turn.
6. Attempt 6 used the corrected schema and client mode; its first non-canonical advisory was rejected,
   then the two exact controls committed.

The committed stdio regression replays the Codex `2025-06-18` initialize/initialized/tools-list
frames (including `progressToken: 0`) and proves that the server advertises exact tools, no generic
invoke, and object-root output schemas.

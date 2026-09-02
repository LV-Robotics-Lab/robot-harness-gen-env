# System 2 compile turn evidence — 2026-09-02

Companion structured report:
[`system2-compile-turn-20260902.json`](system2-compile-turn-20260902.json), SHA-256
`28b9c0d33fb7c4bec11302e82b9c7b70faf67b0cbb1cb488fe5d86b3c392a431`.

## What was exercised

The public `System2CompileTurn.execute()` seam received a user objective, seed,
operator-trusted catalog path, and generate-on-miss policy. The test used the real packaged
`CompileApplication`, Registry, `text2env.compile@1.0.0` handler, CAS, dispatcher, state-delta
application, trusted ToolResult receipt, and history-authority verifier. The planner provider was a
deterministic local test provider; this run is not evidence that an LLM planner was qualified or
executed.

The successful case produced a terminal `succeeded` ToolResult, a version-2 trusted world state,
and a history authority whose complete two-state lineage and single receipt entry were reread from
CAS. A catalog miss with generation disabled produced a normal `blocked` ToolResult, preserved the
version-1 world state, and returned `history_authority=None`.

That null is deliberate. The current history-v1 contract only advances time through a succeeded,
receipt-derived state mutation. Inventing a no-op state transition for a blocked or failed tool
would make the next planner context look more authoritative than the committed state allows.

## Reproduction

Run the public-seam tests with the module coverage gate:

```bash
pytest -q tests/self_improving/test_system2_compile_turn.py \
  --cov=self_improving.system2_compile_turn \
  --cov-branch --cov-report=term-missing --cov-fail-under=100
```

Expected result: 6 passed; 85 statements and 6 branches, with no missing or partial lines.

Run the adjacent System 2 and compile path:

```bash
pytest -q \
  tests/self_improving/test_system2_compile_turn.py \
  tests/self_improving/harness/test_system2_context.py \
  tests/self_improving/harness/test_system2_domain.py \
  tests/self_improving/harness/test_system2_dispatcher.py \
  tests/self_improving/harness/test_system2_history.py \
  tests/self_improving/harness/test_system2_planner.py \
  tests/self_improving/harness/test_system2_qwen_local.py \
  tests/self_improving/harness/test_application.py \
  tests/self_improving/harness/test_text2env_compile_handler.py
```

Expected result: 462 passed.

Static gates:

```bash
ruff check self_improving/system2_compile_turn.py \
  tests/self_improving/test_system2_compile_turn.py
ruff format --check self_improving/system2_compile_turn.py \
  tests/self_improving/test_system2_compile_turn.py
git diff --check
```

The implementation file is 10,053 bytes with SHA-256
`7f8e89ba78a44d052dc53b68d5f331590e9b246adab678dae90e2c75a9896d31`; its test file is
9,752 bytes with SHA-256
`fcbf916f96a67b1a54deeffb4e7473de451a152f02c135cc78212699318e39c0`.

## Claim boundary

This slice proves one initial compile-oriented System 2 turn can connect trusted input, planning
evidence, the qualified compile Skill, a ToolResult, a state mutation, and reusable history without
letting the planner alter the typed compile input. It does not:

- qualify the planner model;
- dispatch replay or validate;
- resume a blocked or failed history;
- produce a validation decision, promotion receipt, or `publishable` claim; or
- change the checked-in replay qualification source identity.

The implementation and test are outside `self_improving/harness/**`, `scene_gen/**`, and the ledger
contract tree hashed by the replay qualification. An isolated baseline plus these two files passed
the checked-in replay source-identity test.

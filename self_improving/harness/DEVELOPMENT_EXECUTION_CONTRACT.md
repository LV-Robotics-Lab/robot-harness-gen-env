# Deployment-authorized development execution

This extends the existing `GoldenRunHarness` and child `SkillRegistry` execution kernel.
It creates no new workflow database, simulator, qualification issuer or publication path.
The S1 seam remains `start/submit/read/read_workflow_artifact`.

The operator calls `capture_development_authority` with explicit implementation files and a
JSON configuration containing both `execution_mode: development` and
`allow_changed_skill_closure: true`. Source scope is the declared file set, not an assertion
of a complete Python runtime closure. The context records the absolute locators and their
SHA-256 values. Authority checks re-read those bytes, including configuration, before
admission/execution and after the child runs. A changed file requires a new authority and
workflow. It cannot reinterpret the old workflow's history.

`DevelopmentSkillDescriptor.implementation_sha256` must equal the captured authority's
`implementation_sha256`. `authority.registry(descriptors)` produces an explicit
`harness.development_registry_snapshot.v1` with no qualified entries. The existing
workflow envelope can reference this distinct registry schema, but only an
operator-configured `GoldenRunHarness(development_authority=...)` may consume it and only
in an `ephemeral` workspace. A request cannot supply the authority.

`create_development_execution_binding` accepts a descriptor, actual handler, existing
evidence policy, Registry and child SQLiteRunStore. Its application adapter retains the
normal Golden execution interface. `SkillRegistry.invoke_development_with_run_id` uses
the same strict input/output validation, artifact resolution, dependencies, caller-owned
UUID reservation, event journal, terminal persistence and refusal to repeat an incomplete
handler as registered execution. Events use `development.*`, not qualification-candidate
or production labels. The private existing SQLite `qualification_sha256` column stores
64 zeroes solely as a compatibility sentinel. It grants no authority and is never a
qualification ArtifactRef or report.

The operation's historically named `qualified_execution` locator identifies
`harness.development_skill_execution.v1` for this path. Its payload contains the actual
descriptor, implementation and evidence-policy identity, development context and fixed
`qualification_status=development_unqualified`; it has no qualification/report refs.
The terminal evidence has its own `harness.development_tool_receipt.v1` schema.
Existing state-delta, ToolResult and workflow-commit records bind that explicit receipt.
The qualified execution model and qualified admission checks keep their original meaning
and reject development payloads. Development does not install entries in the release
Registry and provides no publish handler.

The supplied evidence policy still owns output interpretation and physical/media checks.
The adapter does not grant a simulator or physical pass merely because a handler finishes.
The controlled echo test exercises durable execution only and supplies no simulation claim.

Validation: S1 tests cover explicit authority, ephemeral restriction, source drift,
unqualified receipt, child terminal commit, restart idempotence and resource reachability;
existing Registry persistence/candidate tests remain green. Changes to Registry source
correctly invalidate old fixed compile qualification manifests; this feature does not
re-sign or bypass those manifests.

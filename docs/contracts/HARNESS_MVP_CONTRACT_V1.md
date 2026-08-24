# Harness MVP Contract v1

- Status: Proposed
- Contract version: `1.0.0`
- Scope: minimal Harness registry and the existing Text2Env Stage 0-5 route
- Authority: this contract defines the Harness-facing interface; existing `scene_gen` schemas,
  package manifests, and validation reports remain authoritative for their own payloads.
- Implementation evidence: [PR1 core-schema report](HARNESS_MVP_PR1_IMPLEMENTATION_REPORT.zh-CN.md)

## Decision And Boundary

The in-process `SkillRegistry` is the only execution, type, version, and audit authority. MCP is
a stateless protocol adapter generated from registered descriptors. It must not own business
types, select versions, retry calls, alter results, or decide publication.

This contract covers only Text2Env compilation, RoboTwin/SAPIEN replay, and validation. It does
not define Anchor2Env, network asset search, a second simulator, Transfer, plugin hot loading,
distributed scheduling, policy execution, data collection, training, or evaluation.

## Skill Identity And Versioning

A Skill identity is the tuple `(skill_id, version)`. Its canonical display form is
`<skill_id>@<version>`. `skill_id` must match
`^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$`. Registered versions use stable SemVer
`MAJOR.MINOR.PATCH` with no leading zeroes, prerelease suffix, or build suffix. Every invocation
must name an exact version; ranges, implicit `latest`, and silent fallback are forbidden.

| Canonical Skill ref | MCP tool name | Input schema | Output schema |
| --- | --- | --- | --- |
| `text2env.compile@1.0.0` | `text2env_compile_v1_0_0` | `harness.text2env_compile_input.v1` | `harness.text2env_compile_output.v1` |
| `text2env.replay@1.0.0` | `text2env_replay_v1_0_0` | `harness.text2env_replay_input.v1` | `harness.text2env_replay_output.v1` |
| `text2env.validate@1.0.0` | `text2env_validate_v1_0_0` | `harness.text2env_validate_input.v1` | `harness.text2env_validate_output.v1` |

The MCP name is mechanically derived by replacing `.` in `skill_id` with `_`, prefixing the
version with `_v`, and replacing version dots with `_`. The canonical Skill ref remains the
identity stored in all records.

Version changes follow these rules:

- `MAJOR`: remove or rename a field; add a required field or any output field; narrow valid input;
  change defaults, state/error meaning, determinism, or a publication gate.
- `MINOR`: add an optional input with an unchanged default, widen accepted input, add an artifact
  inside an existing artifact collection, or add a fail-closed error code without changing
  existing outcomes.
- `PATCH`: fix an implementation defect without changing public schemas or declared semantics.

Skill SemVer is independent of the Python package version, `robotwin.*.v1` schema identifiers,
package-manifest version, and `compiler_version`; every one is recorded separately. A registered
`(skill_id, version)` is immutable. Re-registering it with a different descriptor or
implementation digest must fail with `HARN_VERSION_UNSUPPORTED`. Every public JSON-schema shape
change receives a new integer schema identifier; a breaking shape change also requires a Skill
major version. Consumers must preserve unknown blocker codes and fail closed, which makes an
additive code a minor change.

## Common Records

All common records are strict: unknown fields are rejected, required unknown values are never
guessed, and JSON uses UTF-8 with sorted keys and compact separators for canonical hashing.

| Record | Required fields |
| --- | --- |
| `SkillDescriptor` (`harness.skill_descriptor.v1`) | `skill_id`, `version`, `mcp_tool_name`, `input_schema`, `output_schema`, `implementation_name`, `implementation_version`, `implementation_sha256`, `deterministic=true`, `max_attempts`, `qualification_artifact` |
| `Invocation` (`harness.skill_invocation.v1`) | `run_id`, `skill_id`, `skill_version`, `effective_parameters`, `dependencies`, `max_attempts`, `invocation_digest` |
| `RunState` (`harness.run_state.v1`) | `run_id`, `invocation_digest`, `skill_id`, `skill_version`, `status`, `attempt`, `max_attempts`, `started_at`, `ended_at`, `events`, `artifacts`, `output`, `blocker` |
| `Event` (`harness.event.v1`) | `seq`, `timestamp`, `stage`, `attempt`, `from_status`, `to_status`, `artifact_refs` |
| `ArtifactRef` (`harness.artifact_ref.v1`) | `name`, `uri`, `media_type`, `sha256`, `bytes`, `schema_version` |
| `Blocker` (`harness.blocker.v1`) | `code`, `message`, `stage`, `retryable`, `details`, `unknowns`, `artifact_refs` |

`run_id` is an audit identifier and is not a content identity. `ArtifactRef.uri` is a locator and
is not trusted for identity; consumers verify `sha256`. `schema_version` is a string for typed
artifacts and `null` for untyped media. `unknowns` contains records with exactly `field`, `reason`,
and `source`.

Nullable fields remain present: `ended_at=null` while running; `Event.from_status=null` on the
first event; `invocation_digest=null` for a preflight skill/version/input/dependency blocker;
`output=null` until a typed output exists; and `RunState.blocker=null` unless the run is blocked or
failed. An `Invocation` record is created only after preflight has resolved the exact descriptor,
effective parameters, and dependencies. `qualification_artifact.schema_version` must be
`harness.skill_qualification.v1`; its payload contains exactly `skill_ref`, `status="pass"`,
`deterministic_case_id`, `regression_command`, and `report_sha256`.

`dependencies` is sorted by `name`; each entry contains exactly `name`, `version`, and `sha256`.
Defaults are expanded before hashing. `invocation_digest` is:

```text
sha256(canonical_json({
  "skill_id": skill_id,
  "skill_version": skill_version,
  "effective_parameters": parameters_with_artifact_uris_replaced_by_content_identity,
  "dependencies": dependencies_sorted_by_name,
  "max_attempts": max_attempts
}))
```

An artifact's content identity is exactly `media_type`, `schema_version`, and `sha256`. The digest
excludes `run_id`, timestamps, event data, artifact locations, and output directories. The request
text is hashed exactly as received; the Harness performs no linguistic normalization.

`max_attempts` is fixed in each descriptor: compile is 1, replay is 2, and validate is 1. Only a
result whose blocker has `retryable=true` may start another attempt under the same `run_id` and
`invocation_digest`. Every attempt emits events. Callers and MCP cannot change this policy.

## Text2Env Skill Contracts

| Skill | Required input | Required output |
| --- | --- | --- |
| `text2env.compile@1.0.0` | `request`, `seed`, `asset_catalog: ArtifactRef`, `config.generate_missing_assets` | `scene_spec: ArtifactRef`, `resolved_scene: ArtifactRef`, `environment_package: EnvironmentPackage`, `static_validation: ArtifactRef` |
| `text2env.replay@1.0.0` | `environment_package`, `runtime_config` | `runtime_evidence: ArtifactRef`, `replay_artifacts: tuple[ArtifactRef, ...]` |
| `text2env.validate@1.0.0` | `environment_package`, `runtime_evidence`, `gate_profile="robotwin.scene_validation.v1"` | `validation_report: ArtifactRef`, `validation_status`, `publishable`, `blockers: tuple[Blocker, ...]` |

`seed` is an integer from 0 through 2,147,483,647. The compile request may contain semantic intent
only; paths, asset identifiers, positions, and quaternions remain forbidden by `SceneSpec`.
Trusted asset locations are resolved from `ArtifactRef` and dependency records, never from request
text. Compile `config` contains exactly `generate_missing_assets: bool`, defaulting to `false`.
Replay `runtime_config` contains exactly `precheck_steps`, `settle_steps`,
`contact_window_steps`, `video_frames`, and `fps`, with effective defaults 0, 900, 120, 120, and
12 respectively; all effective values participate in `invocation_digest`.

`EnvironmentPackage` (`harness.environment_package.v1`) is an immutable reference to the existing
hash-bound package, not a duplicate package format. It contains exactly `package_id`,
`route_id="text2env"`, `producer_skill_ref`, `seed`, `scene_spec_sha256`,
`resolved_scene_sha256`, `asset_catalog: ArtifactRef`, and `package_manifest: ArtifactRef`.
`package_id` equals `resolved_scene_sha256`, and `asset_catalog.sha256` must equal the catalog
digest stored in both the resolved scene and package manifest.

Compilation invokes the existing parse, grounding, bounded solve, builder, and static validator
behavior. Its run is `succeeded` when typed compile outputs were produced, even though the static
validation status is normally `incomplete` because runtime evidence is absent. Replay produces
sequential physical evidence. Validate verifies the package, hash binding, runtime evidence, and
all gates; it is the only one of these three Skills that emits `publishable`.

## State, Errors, And Publication

`RunState.status` is one of `running`, `succeeded`, `blocked`, or `failed`. The only transitions
are `null -> running -> succeeded|blocked|failed`. A retry keeps the state `running`, increments
`attempt`, and appends events. `blocked` is an expected domain, dependency, or gate refusal;
`failed` is an unexpected Harness or implementation fault. Both terminal states require a
`RunState.blocker`; `succeeded` requires `RunState.blocker=null`. A successful Skill execution is
not by itself a validation pass. In particular, validate is `succeeded` when it produces a typed
`fail` or `incomplete` report; the output `blockers` explain why publication is refused.

Existing validation status remains `pass`, `incomplete`, or `fail`; no parallel gate-status enum
is introduced. `publishable=true` if and only if all of the following hold:

1. The compile, replay, and validate runs succeeded at the exact registered versions.
2. `verify_package` passed and every referenced artifact digest matched.
3. Runtime evidence is bound to the package's `resolved_scene_sha256`.
4. The final validation status is `pass` for physical, collision, stability, visibility, and video
   checks.
5. The registered Skill descriptors have passing qualification artifacts for their deterministic
   and regression tests.
6. Request provenance, versions, seed, dependency digests, and solver trace are present.

Any false condition yields `publishable=false` and at least one structured blocker in either the
validate output or, when no typed output could be produced, `RunState.blocker`. This contract
defines eligibility only; it does not define a `published` state or an external publication side
effect.

| Error code | Meaning | Default location | Default retryable |
| --- | --- | --- | --- |
| `HARN_SKILL_NOT_FOUND` | No descriptor for the requested `skill_id` | run blocker: `blocked` | `false` |
| `HARN_VERSION_UNSUPPORTED` | Exact version absent, conflicting, or mutated | run blocker: `blocked` | `false` |
| `HARN_INPUT_INVALID` | Invocation or Skill input failed strict schema validation | run blocker: `blocked` | `false` |
| `HARN_DEPENDENCY_UNAVAILABLE` | Required dependency identity, version, or digest is unavailable | run blocker: `blocked` | `false` |
| `T2E_REQUEST_REJECTED` | Request failed the bounded prompt or `SceneSpec` boundary | run blocker: `blocked` | `false` |
| `T2E_ASSET_UNAVAILABLE` | Catalog or generated asset cannot satisfy grounding requirements | run blocker: `blocked` | `false` |
| `T2E_SOLVER_EXHAUSTED` | Bounded solver ended without a valid resolved scene | run blocker: `blocked` | `false` |
| `T2E_PACKAGE_INVALID` | Package construction, manifest, schema, or hash binding failed | run or validate-output blocker | `false` |
| `T2E_REPLAY_FAILED` | RoboTwin/SAPIEN replay did not produce complete evidence | run blocker: `blocked` | `true` |
| `T2E_VALIDATION_INCOMPLETE` | Required runtime checks were not run | validate-output blocker | `false` |
| `T2E_VALIDATION_FAILED` | One or more required validation checks failed | validate-output blocker | `false` |
| `T2E_REGRESSION_FAILED` | Registered qualification or deterministic regression did not pass | registration blocker | `false` |
| `HARN_INTERNAL` | Unexpected Registry, adapter, or handler defect | run blocker: `failed` | `false` |

Exception text is diagnostic detail, never an error code. Existing `SceneSpecError`, pydantic
validation errors, `SceneSolveError` reports, package verification, replay exit status, and
validation reports map to the table by stage and recovery action.

## Registry And MCP

The MVP Registry is statically assembled at process start. It provides `register(descriptor,
handler)`, `list()`, `resolve(skill_id, exact_version)`, and `invoke(skill_id, exact_version,
parameters)`. Registration
rejects an invalid descriptor, a conflicting immutable identity, or a non-passing qualification
artifact. Invocation validates and expands inputs, computes the invocation digest, enforces the
descriptor's attempt bound, resolves dependency records, creates the `Invocation`, calls the
handler, hashes artifacts, and appends state events. Registry code must not contain Text2Env
branching; the three handlers adapt the existing Text2Env functions and CLIs.

MCP `tools/list` is generated mechanically from the registered descriptors and exposes the three
versioned tool names above. MCP `tools/call` resolves that name to the exact canonical Skill ref,
passes the Skill-specific JSON parameters to `Registry.invoke`, and returns the same typed output,
`RunState`, artifacts, and blocker as an in-process call. Malformed JSON-RPC remains an MCP
transport error; after a registered tool call enters the Registry, all outcomes use this contract.
Parameter defaults, dependency resolution, and run metadata are added only by the Registry. MCP
adds no defaults or protocol-only business fields.

## Acceptance

This contract is accepted when the project owner confirms the exact fields, names, state
transitions, error codes, version rules, and MCP mapping; all repository tests pass; and the RFC
header records `Status: Accepted`, the approver, and the approval date. Implementing the contract
must preserve the current Text2Env behavior and the existing hash-bound and physical-validation
acceptance rules.

Sources: REQ-4.1, REQ-4.3, `scene_gen/schema.py:13-14,186-275,451-492`,
`scene_gen/builder.py:39-105`, `script/generate_scene.py:22-93`,
`scene_gen/solver.py:34-40,492-505`, and `repo-docs/walkthroughs/one-real-run.md`.

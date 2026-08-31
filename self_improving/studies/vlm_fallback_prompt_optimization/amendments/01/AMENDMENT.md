# VLM fallback study amendment 01

## Status and claim boundary

This is a **pre-execution protocol amendment**. It closes ambiguities found after the original
preregistration was frozen and before any VLM or routing-provider inference was allowed. It does
not contain experimental results, does not report a model evaluation, and does not authorize a
physics claim. Creating and validating the study bundle did not load either Qwen model, call a
provider, or run a simulator; the study-execution network-call count remains zero.

The annotation state remains `pending_blinded_annotation`. In addition, this repository does not
yet contain a root-bound production provider adapter: the existing injected
`ProviderIdentity.production_eligible` value is self-reported and is not qualification evidence.
Therefore `production_provider_state=not_yet_qualified` and `execution_authorized=false`. Every
visible, routing, oracle, format-repair, and model-inference operation remains forbidden. A later
amendment must bind sealed annotation evidence, the exact visible adapter or executable and
processor, the exact routing sandbox provider, and journal events tying those identities to the
qualified model-content manifests. It must also root-bind the frozen experiment-B prompt contract
and a complete authoritative evaluation closure. Injected test providers can never supply
production promotion evidence.

## Frozen source identity

The contract binds:

- original `experiment_spec.json` SHA-256
  `ad19d38204f20c42d8070785994fe749b8db41364e002e382e938cb92ae5bf6c`;
- the original six-line `run_log.jsonl` prefix SHA-256
  `141bc995ba391a11cea3461180f51936f5829aca0c13ef44e10b48d3ef27f6a4`;
- canonical 3B model-content manifest digest
  `dd904e42c13f7a47296e1aff8ce8a78090a86451d24b5ccd0d2807d29e6e4cad`;
- canonical 7B model-content manifest digest
  `9686327d73f5f373917d25577fc06244c69b132927af1c586fbcbc23f3301208`;
- the separately canonicalized English/Chinese surface table and the exact 36-case routing roster.

The contract intentionally does not contain its own digest and does not bind implementation or
test source. This amendment's `manifest.json` binds the contract and the full effective spec v2
without a digest cycle. The spec v2 is a complete effective specification that preserves every v1
case and artifact binding; an overlay is not sufficient.

## Experiment A clarification

A0 has five checks; A1 and A2 each have the same complete eight-check, within-arm, and status
contract. A2 inherits A1's selected prompt bytes, case-input projection, processor, sealed case
roster, and scorer source. Its only permitted differences are the explicit model identity/content
and resource reservation fields. Between-arm inference is permitted only on three honestly
comparable visible domains, for each of A1-A0, A2-A0, and A2-A1:

| A0 | A1 | A2 |
| --- | --- | --- |
| `object_presence` | `object_presence` | `object_presence` |
| `penetration_or_floating` | `visible_penetration_or_floating` | `visible_penetration_or_floating` |
| `overall_prompt_match` | `overall_prompt_match` | `overall_prompt_match` |

All other checks are arm-internal descriptive metrics. They are never filled, projected, or
imputed into the other arm. An A0 `warning` maps to `abstain`, not pass or fail.

Schema validity has two required disclosures. `base_valid_rate` is measured before repair;
`final_valid_rate` is measured from the final response after zero or one format-only repair. Its
denominator is never the subset of successful provider calls. The sealed test roster has exactly 14
cases per arm and therefore exactly 42 A0/A1/A2 terminal rows keyed by `(arm, case_id)`. A missing
response, invalid response, provider exception, timeout, or no-response outcome must retain its row
and counts invalid. Base calls and format-only repairs are bound in a separate provider-event budget
roster. Only the final rate is a promotion gate, and repair may not change visible facts.
Typed-correction precision must be backed by a blind-adjudication manifest and roster. If its
prediction denominator is zero, the metric is null and promotion fails. Digest strings alone are
not evidence: promotion validation resolves the provider event, adjudication manifest, and
adjudication roster from an exact content-addressed document map and checks their canonical
SHA-256 digests, schemas, counts, and cross-bindings. Under this amendment that proves content
integrity only; it does not override the pending annotation or unqualified-provider execution
gates.

The A confidence interval resamples `group_id` clusters. For A1-A0, A2-A0, and A2-A1, every
bootstrap replicate reconstructs both arms' raw rows for the three mapped checks, recomputes
group-weighted macro-F1 separately, and then takes the named contrast. Averaging or bootstrapping
precomputed scalar deltas is forbidden because macro-F1 is nonlinear.

## Experiment B clarification

Provider-visible routing input has two layers. The common runner-owned layer is limited to the
original prompt, blinded case and invocation identifiers, the exact frozen routing instruction and
template version, seed and attempt, allowed routes, and resource reservation. It contains no gold,
annotation, or repository path.
Those forbidden field names are rejected recursively, so nesting a gold label inside a typed
failure or trusted-state object does not bypass the projection.
The exact failure-context projections are:

- B0: `failure_code` only;
- B1: `untyped_failure_summary` only;
- B2: `typed_failure`, `trusted_state`, `asset_availability`, and `visible_report`;
- B3: the complete B2 context plus `gold_route`, using a disjoint oracle provider only.

B3 is never deployable or promotion-eligible. B0 and B1 never receive a visible report. B2 and B3
carry `visible_report=null` under amendment 01. Non-null A1 evidence requires both a later amendment
and a journal binding; a caller cannot unlock it.

The contract embeds each arm's complete prompt boundary. B0 has null instruction/template and no
provider. B1, B2, and B3 have exact UTF-8 instruction bytes, template ID, ordered input fields and
types, ordered route vocabulary, closed output object schema, and deterministic decode parameters.
The shared output contains exactly `route`, `revised_prompt`, `intent_before`, and `intent_after`;
the two typed-intent objects must remain equal. `validate_arm_projection` rejects any caller-chosen
instruction or template. These frozen bytes still do not authorize execution under amendment 01.

`routing_case_inputs.json` is the only accepted case roster. It classifies the 36 frozen records as
30 clean controls, zero recoverable baseline failures, and six unrecoverable failures. The clean
controls are runner no-op `accept_existing_compile` outcomes for B0/B1/B2: no provider and no retry.
They must not be relabeled as recovered failures merely because the v1 sample field said that the
task was recoverable. The completion denominator contains only observed
`recoverable_baseline_failure` cases. It is currently zero, so completion delta is null, the reason
is `insufficient_recoverable_failure_groups`, no completion-superiority claim is allowed, and B2
cannot be promoted.

The independent `B2_repeatability_audit` covers all six non-control cases in four audit groups with
repeat indices 1, 2, and 3. The exact typed input must be identical and both route and revised-prompt
SHA must agree across all three receipts. The typed-input digest is recomputed from the frozen
roster's case, group, prompt, seed, typed failure, trusted state, asset availability, null visible
report, routing instruction, and allowed-route vocabulary. Each receipt resolves a canonical
journal-event body, and an exact 18-event journal manifest binds the set. Its 18 route evaluations
do not consume the regular two-attempt recovery budget and are forbidden from compiling or
replaying. Promotion derives the six-case result from these bound per-repeat events; it rejects a
caller-supplied aggregate, a missing case, a forged typed-input digest, or an unbound event digest.

## Language and intent preservation

`surface_canonicalization.json` freezes registered English and Simplified Chinese surfaces for six
protected dimensions: entity, count, color, relation, articulation, and region. A surface rewrite
may substitute a registered synonym only. The trusted typed projections before and after rewriting
must be canonically identical, including multiplicity, entity roles, relation direction,
articulation target, and region target. Deletion or insertion is forbidden. An unknown surface
requires the exact typed `abstain_unsupported_language_surface` route.

## Numeric and promotion semantics

Every rate, delta, confidence bound, p-value, and latency ratio threshold is a plain base-10 JSON
string parsed with `decimal.Decimal`; binary-float conversion and exponent notation are forbidden.
Counts and budgets are JSON integers, with booleans explicitly rejected. The normative contract
lists every A1, A2, and B2 gate rather than relying on prose inheritance. All gates are conjunctive.
Ratio gates compare the exact integer fraction against the Decimal threshold; a rounded Decimal
quotient is display-only and can never turn a value infinitesimally below a threshold into a pass.
The future authoritative result is a one-way, acyclic closure. It must reverse-bind the current
amendment root, effective spec v2, canonical contract, both model-content manifests, runner-source
manifest, sealed annotation, provider qualification journal, and A1 prompt-selection manifest.
It contains exact typed terminal-row schemas for all 42 visible and 144 routing arm/case keys, raw
journal-event references, scorer/source identity, and bootstrap receipts. Every promotion gate has
an explicit source-row/field/recompute-function mapping; caller-supplied Decimal or integer metrics
have no authority. The result closure references those older inputs, then a later journal event
references the completed result; the current amendment root never references future results.

`promotion_decision` in amendment 01 is explicitly a non-authoritative dry run. It always returns
`authoritative=false`, `eligible=false`, and the global failure
`authoritative_evaluation_evidence_not_bound_by_amendment_01`, even if a hypothetical caller metric
object reaches every numerical threshold. Pending annotation, unqualified provider, and disabled
execution remain additional independent failures.

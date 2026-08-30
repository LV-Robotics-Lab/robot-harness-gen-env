# ASPIRE harness experiment protocol

Frozen on 2026-08-31 before the first production-code change.  The study asks
whether ASPIRE's *mechanisms* improve this project; it does not use the paper's
reported success rates as a local baseline.

## First-principles outcome model

A robust generated environment is not one that looks plausible.  It is a
specific `ResolvedSceneSpec` whose replay produces complete, identity-bound
runtime evidence and passes every authoritative static and physical gate.  An
agent can propose or diagnose, but cannot turn missing or failed evidence into
a pass.

For a held-out case `i`, define robust completion as:

```text
R_i = 1 only when
      the intended case is processed within the fixed action budget,
      the authoritative validator returns pass,
      the evidence is bound to the exact resolved-scene digest,
      and no forbidden publication or oracle leakage occurred.
```

The primary aggregate for the multi-family experiment is macro held-out robust
completion (mHRC): mean success within each failure family, then the unweighted
mean across families.  This prevents a large easy family from hiding a weak
safety boundary.

## Fixed invariants

- Static geometry and runtime physics gates remain the authority.
- Rendered images, agent text, learned scores, and test counts are not success
  labels.
- Development cases may create memory; held-out outcomes may not edit it.
- All arms receive the same case input, action vocabulary, and action budget.
- A result with an unsafe publication is a failure even if another case later
  succeeds.
- Existing clean cases must remain accepted and the full repository regression
  guards must not lose a pass.

## E0 — deterministic compiler headroom (exploratory baseline)

This baseline was run during discovery, before this protocol was frozen, so it
is explicitly exploratory rather than preregistered.

- Catalog: actual RoboTwin checkout at commit
  `266f3aadf505a4f7fe9af0faa41a20f5f47cd123`.
- Matrix: all 33 committed prompt-matrix cases, static validation only.
- Seed sweeps: 100 seeds each for `can_on_plate` and `apple_in_basket`.
- Metric: static compile-and-validation completion.
- Interpretation rule: 100% static completion establishes no static headroom;
  it is not evidence of runtime physics robustness.

## E1 — runtime evidence binding falsifier

### Hypothesis

Fine-grained traces are useful only if their provenance cannot be detached from
the state they claim to validate.  The current producer writes `scene_id` and
`resolved_scene_sha256`; the consumer must reject a missing or mismatched value.

### Dataset and cases

Use one deterministically solved committed-fixture scene and one otherwise valid
runtime-evidence object.  Evaluate five mutations:

1. matching scene id and digest (must pass);
2. missing digest (must fail);
3. mismatched digest (must fail);
4. missing scene id (must fail);
5. mismatched scene id (must fail).

The mutations change only provenance fields.  The exact same probe is run
before and after the candidate change.

### Metrics and keep rule

- Primary: correct decisions / 5.
- Safety: unsafe accepts among the four invalid mutations; target is zero.
- Regression: focused validator tests, `pytest -q`, and
  `script/run_self_improving_tests.sh` must pass.
- Keep only if accuracy rises to 1.0, unsafe accepts fall to zero, the valid
  evidence still passes, and all regression guards pass.

Because this changes a runtime acceptance contract, a real RoboTwin/SAPIEN
replay is also required.  If the host cannot execute one, the code result is not
reported as fully verified until an existing compatible environment or replay
path is found and run.

## E1b — pending-review state integrity

Protocol extension frozen after the harness audit and before changing Stage 5.
The critic already distinguishes `pending_visual_review` from `pass`; the
pipeline must preserve that state rather than minting final-pass fields.

Use one fixed placement-shaped input and a fixed pending critic report.  Measure
five decisions: the derived artifact must not use stage
`final_render_accepted`, decision `accept_final`, validation `scene_critic=pass`,
or `render_visibility=pass_visual_review`; and the pipeline exit policy must not
return success code 0 for pending review.  The valid-pass control must retain
all four final-pass fields and exit 0.

Keep only if all pending decisions are correct, the pass control is unchanged,
batch code can distinguish the explicit review-required exit from failure, and
the full platform guard remains green.  A review candidate may be retained for
human/VLM work, but it must not be exposed under the `final_placement` artifact
key.

## E2 — held-out diagnosis and validated-memory benchmark

### Failure families

The benchmark contains development examples and independently parameterized
held-out variants for:

1. target-local support footprint;
2. target-local containment;
3. dynamic nested contact and declared support target;
4. runtime completeness (visibility, sequential video, motion, drop, and
   penetration evidence);
5. identity/integrity binding;
6. bounded feasibility or derived-proxy decisions.

Synthetic cases are contract tests, not simulator rollouts.  They can support a
harness-level claim only.  Any family whose authoritative outcome cannot be
constructed without leaking a label is removed and recorded before execution,
not replaced with an agent opinion.

### Matched arms

- **B0 frozen:** one fixed default action, no diagnostic trace or memory.
- **B1 reactive:** typed failure trace may choose one bounded repair for the
  current case; no cross-case memory.
- **H-ASPIRE:** the same action policy and budget as B1 plus only
  development-validated, trigger-specific, provenance-bearing skill memory.
- **Oracle diagnostic ceiling:** may see the fixture label; reported only as a
  ceiling and never compared as a deployable arm.

Action budgets and ordering are fixed before held-out evaluation.  Policy input
must omit fixture `expect` values and human-readable test names.

### Metrics and keep rule

- Primary: held-out mHRC.
- Secondary: per-family completion, worst-family completion, clean-control
  acceptance, unsafe-publication count, evidence-binding coverage, actions and
  attempts per case.
- An ASPIRE-style memory claim requires `H-ASPIRE - B1 >= 0.05`, a paired
  bootstrap 95% confidence interval above zero, mHRC at least 0.95, zero unsafe
  publications, 100% clean-control acceptance, 100% identity binding, and no
  worse worst-family completion.
- If the finite deterministic fixture set cannot support a meaningful
  bootstrap interval, report effect sizes and exact case outcomes but make no
  superiority claim.

## E3 — upstream reproduction boundary

Upstream no-service mechanism tests are a partial code-path reproduction.  The
official ten-task LIBERO-Pro Goal-Swap run is reproduced only if its named
environments, perception services, credentials, eight-GPU topology, development
seeds, held-out seeds, and budgets are all satisfied.  A reduced local check is
never relabelled as that result.

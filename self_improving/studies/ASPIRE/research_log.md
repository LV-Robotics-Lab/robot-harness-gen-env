# ASPIRE study: chronological research log

This document is updated throughout the study. Entries report observations and
decisions; they do not retroactively convert missing evidence into success.

## Autoresearch setup

The user's request supplied the setup choices and explicitly overrode two
defaults of the `autoresearch` skill: no interactive confirmation and no new
branch. Work remains on `worktree/bingsheng`.

| Parameter | Fixed value |
| --- | --- |
| Goal | Determine empirically whether ASPIRE mechanisms improve this project's agent harness, robust environment generation, or self-improving loop; retain only bounded improvements. |
| Primary metric | To be preregistered after source and current-harness audit; it must use held-out authoritative outcomes, not test count or rendered plausibility. |
| Regression guard | `pytest -q` and `script/run_self_improving_tests.sh`; no existing pass may regress. |
| In scope | `external/ASPIRE` as an upstream submodule; study artifacts under `self_improving/studies/ASPIRE`; a minimal `self_improving/` integration only if an experiment supports it. |
| Out of scope | `scene_gen/` trust-boundary weakening; real-robot code/services; credentials; generated bulk datasets/checkpoints; paper result claims without complete manifests. |
| Constraints | Current branch, no new branch; preserve user changes; development/held-out isolation; no simulator-ground-truth leakage; authoritative physical gates; simplicity preferred. |
| Stop rule | Run the preregistered baseline and all bounded, high-value falsifiers feasible on this host; stop when the conclusion is supported and remaining claims require the documented external blockers. |

## 2026-08-31T01:13–01:25+08:00 — startup and baseline

1. Read the complete `autoresearch` skill and root `AGENTS.md` instructions.
2. Attempted the three mandated dashboard state URLs and the private dashboard
   wrapper. All returned HTTP 404. No dashboard task status was fabricated.
3. Confirmed the Git worktree was clean at `732e190` on
   `worktree/bingsheng`; no branch was created.
4. Read the repo-docs overview, one-real-run walkthrough, Harness Schema
   Tranche, and Self-Improving platform boundary.
5. Recorded host resources: one RTX 5090 with 32 GiB VRAM, roughly 47 GiB
   available RAM, and roughly 710 GiB free disk.
6. Ran the unmodified top-level baseline: `pytest -q` produced `125 passed in
   0.79s`.
7. Two attempts to collect all `self_improving/alchedata` tests without the
   documented import path failed during collection (`16` then `1` import
   errors). These were invocation errors, not product regressions. With
   `PYTHONPATH=self_improving/alchedata:self_improving/alchedata/scripts`, the
   suite produced `35 passed, 3 skipped in 0.24s`.
8. Ran the canonical complete platform guard
   `script/run_self_improving_tests.sh`: `591 passed, 6 skipped`, zero failures;
   `self_improving.harness` retained 100% statement and branch coverage. This
   differs from the older `564 passed, 6 skipped` reader-facing documentation
   and triggers a documentation sync check before final reporting.

## 2026-08-31T01:18–01:25+08:00 — source acquisition and protocol read

1. Opened the arXiv abstract, NVIDIA project page, and official GitHub repo.
2. Used `git ls-remote` to resolve official HEAD to
   `7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`.
3. Added the upstream repository at `external/ASPIRE` as a Git submodule,
   following this repository's rule that external projects must not be copied
   into the first-party tree.
4. Read the complete upstream root `AGENTS.md`, simulation workspace guide,
   simulation constitution, suite registry, LIBERO constitution, Goal-Swap
   Quick Start, and Fix Loop instructions/coordinator/worker protocol.
5. Performed the read-only preflight captured in `preflight.md`. The official
   full-suite topology is impossible on the current one-GPU host and required
   credentials, environments, nested submodules, and services are absent.
6. Decision: do not install or launch the paper-scale workflow and do not call a
   smaller substitute the paper reproduction. Continue with no-service
   upstream checks and a preregistered project-native experiment.

## Initial first-principles hypotheses

- **H0 (core null):** ASPIRE cannot improve the validity of scenes that already
  pass the deterministic solver and runtime gates; an LLM loop has no authority
  to overrule physical evidence.
- **H1 (trace value):** fine-grained, typed tool/validator traces can improve
  failure localization and the choice of a bounded next action compared with a
  generic retry.
- **H2 (memory value):** only validated, trigger-specific repairs with explicit
  provenance can improve held-out failures; unfiltered free-form memory risks
  negative transfer.
- **H3 (search value):** diverse candidate generation can improve recovery only
  when selection is made by the same authoritative gates under an isolated
  validation split. Diversity alone is not improvement.
- **H4 (project fit):** ASPIRE's strongest near-term contribution is likely an
  execution/diagnosis/promotion layer in `self_improving/`, not changes to
  `scene_gen/` schemas or physical thresholds. This remains a hypothesis until
  the current-harness audit and experiments finish.

## 2026-08-31T01:25–02:10+08:00 — upstream mechanism check and static headroom

1. Ran the upstream tests that do not require simulator services with
   `PYTHONPATH=../..`.  Of 24 selected outcomes, 18 passed, 4 skipped, and 2
   failed.  Both failures were exact environment-pin checks: this host has
   NumPy 2.4.4 rather than 1.26.4 and SciPy 1.17.1 rather than 1.15.3.  The
   generation-progress, LIBERO fix-loop, skill-promotion receipt, suite helper,
   and setup-helper tests otherwise executed.  This is a partial mechanism
   reproduction, not a simulator or paper-result reproduction.
2. An initial 33-case prompt-matrix invocation used the committed fixture
   catalog.  It produced 3/33 because its deliberately fake `/opt/robotwin-fixture`
   source paths do not exist on this host.  This was a precondition error, not a
   generator failure; the artifact is retained as
   `artifacts/prompt_matrix_fixture_invalid` so the failed attempt is not hidden.
3. Located the real RoboTwin checkout at commit
   `266f3aadf505a4f7fe9af0faa41a20f5f47cd123` and built a catalog from it:
   127 catalog entries, 15 currently available, catalog digest
   `6d25cbda4d4e9d203d97cf872171027331449048cfa2db17ec02b1a282564433e`.
4. Re-ran the same committed prompt matrix against that catalog: 33/33 static
   cases passed; runtime was intentionally not requested (0/0).
5. Ran deterministic 100-seed static acceptance sweeps for both `Place a can on
   top of a plate.` and `Put an apple inside a basket.`.  Each produced 100/100.
   Decision: this supplies no static-success headroom for an agent loop and does
   not establish physics robustness.  H0 is supported only at the static layer.

## 2026-08-31T02:10–02:30+08:00 — harness audit and experiment freeze

1. Audited the current harness, historical Stage 5 loop, validator, failure
   memory, and existing quantitative studies.  The typed Harness Schema Tranche
   is a contract layer, not yet a registry/handler/retry executor.  Stage 5 is a
   bounded historical prototype whose defaults are one attempt and zero repair.
2. Found a consumer-side provenance gap: the runtime runner emits both
   `scene_id` and `resolved_scene_sha256`, but `validate_resolved_scene` did not
   compare either field with the `ResolvedSceneSpec`.  Existing positive unit
   fixtures omit both and still pass.  This directly falsifies the assumption
   that emitted provenance is automatically enforced.
3. Found that the existing 12-case failure-score study has Spearman
   `r=-0.2545, p=.7273`; it is not supported as a selector truth signal.  The
   existing failure memory is also one RGB/checkpoint-specific adapter example,
   so no broad transfer claim is justified.
4. Froze `experiment_protocol.md` before production changes.  E1 isolates the
   provenance fields in five matched cases.  E2 compares a frozen policy,
   reactive trace use, and development-validated trigger-specific memory on
   held-out contract families.  The keep rules prohibit unsafe publication,
   clean regression, render-based success, and held-out memory updates.

## 2026-08-31T02:30+08:00 — E1 pre-change measurement

Ran the frozen `runtime_binding_probe.py` at commit `113e54b` before touching the
validator.  The valid control passed, but all four missing/mismatched identity
mutations also passed: 1/5 correct decisions (accuracy 0.20), four unsafe
accepts, and no binding checks in the report.  Artifact:
`artifacts/runtime_binding_before.json`.  This is a direct local reproduction of
the audit finding and satisfies the E1 condition for attempting a minimal fix.

## 2026-08-31T02:31–02:50+08:00 — E1 fix, regression, and real replay

1. At commit `795273f`, added two consumer-side checks before any runtime result
   can pass: exact `scene_id` equality and exact canonical resolved-scene digest
   equality.  Added attacks for missing and mismatched values and updated
   positive root fixtures to carry producer-shaped provenance.
2. Re-ran the unchanged five-case probe.  It moved from 1/5 to 5/5 correct
   decisions (accuracy 1.00) and from four to zero unsafe accepts while the
   valid control remained passing.  Artifact: `artifacts/runtime_binding_after.json`.
3. The focused validator file produced 11/11 passes and the root suite produced
   125/125.  A first run of the complete platform guard exposed three historical
   Stage 5 positive fixtures that omitted identity fields; it produced 3 failures
   and 59 passes in that tranche.  This compatibility failure was not hidden.
   Commit `00e4ae9` updated only those historical test inputs (including their
   negative controls), not Stage 5 execution behavior.
4. Re-ran `script/run_self_improving_tests.sh`: 591 passed, 6 skipped, with the
   Harness Schema Tranche still at 100% statement and branch coverage.  The E1
   regression guard now passes.
5. Located the existing `robotwin-5090` environment (SAPIEN 3.0.0b1) and ran a
   fresh real RoboTwin/SAPIEN replay of can-on-plate seed 7 using 900 settle
   steps, a 120-step contact window, and 120 requested sequential video frames.
   The command exited 0 and the authoritative report passed with 0 failures and
   0 not-run checks.  It retained 120 frames / 100 unique frames; `can_1`
   contacted only `plate_1` for fraction 1.0 and had 9.69 mm target-local support
   margin against the 8 mm minimum.  Both new binding checks passed on digest
   `96e995144468707f0f6e169341ce13e2330b1f42a8acaf73d9c126925e4be3ee`.
   Artifact directory: `artifacts/real_replay_can_on_plate_seed7`.
6. The renderer printed repeated OIDN CUDA-device/invalid-handle diagnostics,
   but generated nonempty PNG/MP4 artifacts and the simulator/validator exited
   successfully.  They are recorded as renderer warnings, not silently treated
   as physical failures or omitted.

Decision: E1 satisfies every preregistered keep rule and is retained.  This is a
concrete ASPIRE-aligned provenance improvement (trace evidence is now tied to
the exact state), not evidence that an LLM repair policy itself improved.

## 2026-08-31T02:51+08:00 — E1b protocol extension

The harness audit also found a state-integrity defect independent of E1:
`pending_visual_review` enters the same finalization helper as a passing review,
which writes `accept_final`, `scene_critic=pass`, and `pass_visual_review`.
Because ASPIRE's validated-skill promotion is useful only when pending and pass
cannot collapse, added and froze E1b before modifying Stage 5.  Its probe
isolates four artifact-state decisions and the CLI exit decision, with a valid
pass control.

Ran the frozen probe at commit `bbfa19d`.  Pending review scored 0/5: it was
rewritten to final stage, `accept_final`, both pass validation labels, and exit
code 0.  The genuine-pass control scored 5/5.  Artifact:
`artifacts/pending_review_before.json`.  The isolated result justifies a bounded
state-machine fix; it does not require a simulator or model call.

## 2026-08-31T02:52–03:00+08:00 — pinned upstream mechanism reproduction

1. Re-ran the exact selected upstream test set and emitted a JUnit receipt.  The
   current environment reproduced the earlier 18 pass / 4 skip / 2 exact-version
   failures.
2. Created a research-artifact-only Python 3.12 environment with ASPIRE's locked
   NumPy 1.26.4 and SciPy 1.15.3 plus pytest.  The unchanged test set then
   produced 20 passed and 4 explicit skips.  The skips were missing PyRoKi,
   missing Contact-GraspNet, and the two simulator integrations gated by
   `ASPIRE_INTEGRATION_REAL=1`.
3. Decision: classify this as a successful partial reproduction of the
   no-service harness mechanisms.  It does not exercise ASPIRE's trace logger,
   simulator, model actors, evolutionary search quality, or paper metrics.
   `reproduction_report.md` records the exact boundary and receipts.

## 2026-08-31T03:00–03:12+08:00 — E1b state-machine result

1. Commit `fa9121f` separated the two terminal artifact types.  A passing
   review still uses `final_placement`, `final_render_accepted`, `accept_final`,
   and exit 0.  Pending review now uses `review_candidate_placement`, stage
   `render_review_required`, decision `hold_for_review`, pending validation
   fields, and explicit review-required exit code 2.  Batch acceptance handles
   code 2 only when `--allow-pending-visual` is set.
2. The unchanged E1b probe moved pending accuracy from 0/5 to 5/5 while the pass
   control remained 5/5.  Artifact: `artifacts/pending_review_after.json`.
3. Added an end-to-end state test that runs the pipeline with deterministic
   mocked smoke evidence and a pending review receipt.  It verifies exit 2,
   absence of `final_placement`, and presence/content of
   `review_candidate_placement`; the focused file produced 4/4 passes.
4. A separate attempt to drive the same branch through the legacy Stage 5 real
   smoke failed before visual review because that script looks for
   `RoboTwin/task_config/demo_smoke.yml`, while the available current RoboTwin
   checkout uses `env_cfg/task_config/`.  Artifact:
   `artifacts/stage5_pending_review_e2e`.  This is recorded as an upstream-layout
   compatibility blocker, not replaced by or conflated with the mocked state
   test.  The project's authoritative runtime path was already verified by the
   independent real SAPIEN E1 replay.
5. The complete platform guard after E1b produced 594 passed, 6 skipped; Harness
   schema coverage remained 100% statement and branch.

Decision: E1b satisfies its keep rule and is retained.  It improves publication
safety and state integrity; it does not improve physical success rate.

## 2026-08-31T03:12–03:25+08:00 — E2 held-out validated-memory experiment

1. Committed the preregistered offline benchmark at `9540dd3`, then ran it twice
   with byte-identical output.  Result receipt:
   `58b7fe73b6cef35d8259a0fae165648046ba49385e126a329aafd1c5420f2c99`.
2. The promotion split contained 12 fault + 12 clean development cases (two per
   family).  Held-out contained 120 fault + 120 clean cases (20 per family),
   with zero case-id intersection.  Six trigger-specific actions passed every
   relevant development case and all development clean controls; library hash
   `0b76621e6327a6fd9f9a0a8f59260aa6b60caaa03addda8abcb83dfb339b1792`
   was unchanged before and after held-out evaluation.
3. Under the same one-action/two-validation budget, macro held-out robust
   completion was 0.00 for frozen retry, 0.50 for reactive trace/no memory, and
   1.00 for validated trigger memory.  The memory arm's worst-family rate was
   1.00.  Every arm had zero unsafe publications, zero clean regression, zero
   budget violations, and identity-binding accuracy 1.00.
4. The matched memory-minus-reactive effect was +0.50.  A fixed-seed, 10,000
   resample family-stratified paired bootstrap gave 95% CI [0.50, 0.50], with 60
   improved, 0 regressed, and 60 tied cases.  The interval is degenerate because
   paired differences are identical within these fixed synthetic families; it
   does not estimate simulator or deployment-distribution uncertainty.
5. Eight benchmark self-tests passed; ruff and format checks passed.  All
   machine keep gates passed, but the verdict is deliberately scoped to
   `keep_synthetic_harness_mechanism_candidate`.  The result itself marks both
   paper-scale ASPIRE support and simulator-physics improvement as false.

Decision: H2 is supported at the deterministic contract-mechanism layer: a
development-validated, trigger-specific frozen memory transfers across unseen
seeds/variants better than current-trace-only behavior without relaxing the
validator.  No LLM, real rollout, policy-learning, or physics-robustness
superiority follows from E2.

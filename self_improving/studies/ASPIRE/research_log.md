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

## 2026-08-31 — startup and baseline

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

## 2026-08-31 — source acquisition and protocol read

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

## 2026-08-31 — upstream mechanism check and static headroom

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
   `6d25cb2816e725e5cc4f2232ba6a3ce9dea89cf6cb8853a13e2eb1942ba8b2a7`.
4. Re-ran the same committed prompt matrix against that catalog: 33/33 static
   cases passed; runtime was intentionally not requested (0/0).
5. Ran deterministic 100-seed static acceptance sweeps for both `Place a can on
   top of a plate.` and `Put an apple inside a basket.`.  Each produced 100/100.
   Decision: this supplies no static-success headroom for an agent loop and does
   not establish physics robustness.  H0 is supported only at the static layer.

## 2026-08-31 — harness audit and experiment freeze

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

## 2026-08-31 — E1 pre-change measurement

Ran the frozen `runtime_binding_probe.py` at commit `113e54b` before touching the
validator.  The valid control passed, but all four missing/mismatched identity
mutations also passed: 1/5 correct decisions (accuracy 0.20), four unsafe
accepts, and no binding checks in the report.  Artifact:
`artifacts/runtime_binding_before.json`.  This is a direct local reproduction of
the audit finding and satisfies the E1 condition for attempting a minimal fix.

## 2026-08-31 — E1 fix, regression, and real replay

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
concrete ASPIRE-aligned declared-identity equality improvement: missing or
unmodified mismatches fail.  It does not sign the complete trace/media/run
receipt and therefore does not prove true provenance or that an LLM repair
policy itself improved.

## 2026-08-31 — E1b protocol extension

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

## 2026-08-31 — pinned upstream mechanism reproduction

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

## 2026-08-31 — E1b state-machine result

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

## 2026-08-31 — E2 held-out validated-memory experiment

1. Committed the preregistered offline benchmark at `9540dd3`, then ran it twice
   with byte-identical output.  Result receipt:
   `58b7fe73b6cef35d8259a0fae165648046ba49385e126a329aafd1c5420f2c99`.
2. The promotion split contained 12 fault + 12 clean development cases (two per
   family).  Held-out contained 120 fault + 120 clean cases (20 per family),
   with zero case-id intersection.  Six trigger-specific skill mappings (four
   unique actions) passed every
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

Decision at execution time: the development-validated, trigger-specific frozen
memory completed more of these fixed cases than current-trace-only behavior
without relaxing the validator.  The case IDs and seed offsets were unseen, but
variant classes partly overlapped (containment/feasibility fully overlapped).
The later protocol audit below supersedes any broad “H2 supported/better” wording:
no statistical-population, LLM, real-rollout, policy-learning, or physics-
robustness superiority follows from E2.

## 2026-08-31 — final integration, protocol audit, and claim correction

1. A read-only E2 audit checked the committed implementation against the result
   artifact.  All reported case counts, arm metrics, paired counts, bootstrap
   parameters, library hash, keep verdict, and false paper/physics claim flags
   matched.  It also found a preregistration deviation: `experiment_protocol.md`
   listed an Oracle diagnostic ceiling, but the implementation and result ran
   only B0/B1/H and did not record a pre-execution reason for omitting Oracle.
   The missing ceiling does not change any H-minus-B1 case outcome, but the
   protocol was not executed in full.  The final report preserves this defect
   instead of inventing a retrospective reason or adding a post-hoc arm as if it
   had been part of the original experiment.
2. The same audit enforced the preregistered meaningful-interval boundary.  The
   CI [0.50, 0.50] is mechanically degenerate on these fixed families, so the
   earlier “H2 is supported/better” wording above is superseded by a descriptive
   claim only: completion is 1.00 versus 0.50 on these 120 fixed synthetic fault
   cases.  No population, simulator, or deployment-distribution superiority is
   claimed.
3. A separate primary-source review corrected final-report terminology and
   scope: `N=90` means skills sourced from 90 LIBERO-90 tasks, not 90 skill
   entries; the official component is `skill library`, not `skill package`;
   Algorithm 1 does not specify crossover; the programming API is curated but
   not a sandbox; and possible stale/misleading entries are a risk rather than
   independently proven negative transfer.  The conclusion now says that an
   ASPIRE-inspired audit led to two local fixes, not that ASPIRE as a full system
   was proven to improve this harness.
4. The benchmark has two distinct hashes.  Its canonical payload receipt,
   computed before adding the self-referential field, is
   `58b7fe73b6cef35d8259a0fae165648046ba49385e126a329aafd1c5420f2c99`;
   the current formatted JSON file SHA-256 is
   `5a3d8c2d8decad3c456b054809ed43877486a17c690c07beed21dfa0a34f967b`.
   Both are now labelled explicitly.
5. Ran the repo-docs foreground sync gate manually because no repo-docs skill or
   local validator was available.  Updated runtime identity binding, pending
   review semantics, the source-evidence traversal, the `1180aef` 595/6 platform
   snapshot, and the previously incorrect rotation-drift 5° prose (source default
   is 3°; resolved rotation error is 5°).  A read-only relative-link audit over
   `repo-docs/` and this study found zero broken links; `git diff --check` and the
   source receipt JSON parse passed.
6. E0–E2 verification snapshot at `1180aef`, before later behavior follow-ons:
   root `pytest -q`
   was 125 passed; `script/run_self_improving_tests.sh` was 595 passed / 6
   skipped with Harness 350/350 statements and 74/74 branches; the unchanged E1
   probe was accuracy 1.0 / zero unsafe accepts; E1b was pending accuracy 1.0
   with pass-control accuracy 1.0; and the E2 self-tests were 8 passed.
7. During this initial final-integration pass, three unrelated untracked Harness files appeared
   from another concurrent workspace actor:
   `self_improving/harness/IMPLEMENTATION_LOG.md`,
   `self_improving/harness/artifacts.py`, and
   `tests/self_improving/harness/test_artifacts.py`.  The completed coverage
   output did not list that new module.  They were not read as study evidence,
   modified, staged, or included in any ASPIRE commit.
8. The required dashboard portfolio/tasks/project endpoints and the private
   dashboard wrapper were checked again before reporting; all returned HTTP 404.
   No task id could be read and no status update is claimed.

Decision at this checkpoint: the E0–E2 scope was complete.  Keep E1 and E1b as
bounded contract fixes; keep E2 only as a synthetic mechanism candidate.  A
subsequent entrypoint audit found additional state paths and therefore extended
the same preregister-before-change discipline as E1c/E1d below.

## 2026-08-31 — E1c follow-on: all active pending entrypoints

1. A final source audit proved that E1b's five decisions covered only the main
   scene-generation helper.  Standalone `scene_critic.py` still returned exit 0
   for pending, and `run_scene_batch.py --allow-pending-visual` could aggregate
   pending candidates into `status=pass`/exit 0.  The legacy placement entrypoint
   also wrote `final_placement.json` before the visual decision, so a crash or
   directory watcher could observe a transient false final.
2. Before production changes, commit `1f1064b` froze E1c's eight entrypoint
   decisions in `experiments/pending_entrypoints_probe.py`.  Running it into
   `artifacts/pending_entrypoints_before.json` measured accuracy 0.625: pass
   controls were correct, but pending critic/batch status and exits were unsafe.
3. Commit `bd4f64f` changed standalone critic and batch aggregate to
   `review_required`/exit 2.  The unchanged probe written to
   `artifacts/pending_entrypoints_after.json` measured 1.00 with pass controls
   unchanged.
4. An initial legacy final-state cleanup (`2f05387`) still allowed a pre-review
   crash to leak transient final files.  Source review rejected that partial
   fix.  Commit `fda13b8` instead writes candidate artifacts from the start and
   performs per-file atomic rename only after visual pass.  Pending, pre-review
   crash, and pass-promotion tests all passed; this is not a multi-artifact
   transaction.

Decision: retain E1c.  It closes concrete publish-state false positives under
the frozen entrypoint probe; it neither proves physical success nor rolls its
results into the earlier E1b 5/5 number.

## 2026-08-31 — E1d follow-on: default static-only state

1. Source review then followed the default main CLI path.  Because
   `--run-smoke` is opt-in, the default path skipped smoke/visual evaluation but
   still called the final helper and minted `final_render_accepted`,
   `accept_final`, `pass_smoke`, `pass_visual_review`, and
   `final_placement.json`.
2. Commit `52fef1e` froze nine expected decisions in
   `experiments/static_only_state_probe.py` before any production change.  Its
   first execution exposed a probe-lifetime defect: path existence was sampled
   after its temporary directory had been destroyed.  Commit `ed5197b` moved
   only the sampling point inside the context; it did not change production or
   expected outcomes.  The corrected baseline artifact
   `artifacts/static_only_state_before.json` measured 2/9 = 0.2222: only exit 0
   and compatibility status were correct.
3. Commit `72b35c4` replaced the default final helper with an explicit static
   candidate.  The unchanged probe produced
   `artifacts/static_only_state_after.json` at 9/9 = 1.00.  Output is now
   `static_scene_candidate_placement.json`, stage `static_scene_candidate`,
   decision `render_next`, and smoke/visual `not_run`; no final key/file exists.
4. `python -m pytest -q
   self_improving/stage5/tests/generate_scene/test_review_state.py` produced
   10 passed after E1d.  Exit 0 retains its narrow compatibility meaning:
   static-stage completion, not visual or physical acceptance.

Decision: retain E1d.  It removes a default-path false pass without changing a
physical threshold.  The before/after probe, probe-method correction, focused
test, production commit, and documentation are recorded separately rather than
post-hoc folded into E1b/E1c.

## 2026-08-31 — concurrent Harness follow-ons and source boundary

The earlier untracked Harness work was subsequently committed by an independent
implementation track as `f5d1bab` (artifact resolver), `5ccf779` (event
recorder), `d5aa185` (compiler module), `567969e` (generated-asset admission),
`568c3b6` (generic SkillRegistry), `f9d6ad5` (durable event publication),
`315524f` (SQLite event journal), `9af9db5` (actual compile-stage events), and
`28333de` (effective-catalog file validation).
These commits occur after experiment snapshot `1180aef`; they are not E0–E2
interventions and cannot be used to reinterpret its baseline or outcomes.

Read-only inspection records two current-source boundaries rather than silently
certifying them.  `LocalArtifactStore.resolve(file://...)` has no allowed-root
and returns a mutable original path after checking it, so it assumes trusted
orchestrator input and has a verify/use TOCTOU window.  Generated-asset admission
reports `physical_qualification=pending_settle` but writes a ledger verification
entry `backend=sapien`, `check=generation_qc`, `verdict=pass` without running
SAPIEN settle; that entry is not physical evidence.  The generic Registry exists,
but Text2Env compile/replay/validate handlers and MCP adaptation remain absent at
this checkpoint.

## 2026-08-31 — historical source-verification snapshot `f9d6ad5`

1. A read-only verifier pinned HEAD to
   `f9d6ad5c844f51b34c882e8d563f99e19208bd07` before running tests; HEAD did not
   move during the commands.
2. `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider` exited 2
   during collection.  `tests/self_improving/harness/test_registry.py` and the
   older `tests/self_improving/test_registry.py` live in non-package directories
   and both import as `test_registry`, producing pytest's import-file mismatch.
3. The canonical `script/run_self_improving_tests.sh` stopped on the same first
   collection error.  A diagnostic rerun with `--import-mode=importlib` got past
   collection and produced 145 passed / 1 failed.  The failure is a real public
   behavior drift: an invalid prompt now reports compiler stage `parse`, while
   `tests/scene_gen/test_generated_scene.py` expects the historical
   `scene_spec_validation`.  Harness coverage also missed one branch
   (`artifacts.py` destination-already-exists) and reached 99.89%, below the
   enforced 100% gate.
4. The ASPIRE-specific checks remained reproducible on that same commit:
   E1 runtime binding 5/5 and zero unsafe accepts; E1b pending/pass 5/5 each;
   E1c 8/8; E1d 9/9; E2 tests 8 passed.  A fresh default E2 benchmark was
   byte-identical to `artifacts/heldout_harness_benchmark.json`.
5. After all commands completed, another concurrent actor created untracked
   `tests/self_improving/harness/test_event_journal.py`.  It did not exist in the
   verifier's initial status and was not exercised or treated as evidence.

Decision: do not report the `1180aef` 125/595 green counts as current source.
The study interventions themselves retain their focused before/after evidence,
but the later concurrent Harness/compiler track has three independent regression
blockers (collection, behavior contract, branch coverage) at this snapshot.

## 2026-08-31 — timestamp and E2 receipt correction

A final machine-receipt audit found that earlier section headings used wall-time
ranges later than the current clock and inconsistent with the Git timestamps of
`9540dd3`/`1180aef`.  Those ranges had no trustworthy receipt and were removed;
document order, commit IDs, `preflight_captured_at`, and the committed-at field
remain the trace authority.  The same audit corrected “six actions” to six
promoted trigger mappings backed by four unique action implementations.  No E2
metric, artifact, protocol deviation, or decision changed.

## 2026-08-31 — later historical source-verification snapshot `28333de`

1. To avoid an impossible moving-target claim on the shared branch, the final
   source auditor pinned a clean archive at
   `28333dee6871ac6a217973c78ad691ea4ffabcf1` and ran read-only tests there.
2. Default root pytest still stopped during collection because the two
   non-package `test_registry.py` files import under the same module name.
   Diagnostic `--import-mode=importlib` completed with 155 passed / 1 failed;
   the only failure remained the public compiler-stage drift (`parse` versus
   the historical `scene_spec_validation` expectation).
3. Harness coverage on that archive was 895/895 statements and 196/196
   branches, both 100%.  This supersedes only the f9 snapshot's coverage
   blocker; it does not cure the collection or behavior-contract failures.
4. The source audit also verified that SQLite journaling, actual compile-stage
   events, Registry qualification boundaries, and effective-catalog file
   validation were present by this snapshot.  They are independent follow-ons,
   not E0–E2 interventions and not evidence for the ASPIRE keep decisions.
5. After this frozen verification point, commit `dc81ade` and uncommitted
   Text2Env handler TDD appeared from the parallel implementation track.  They
   were neither modified nor certified by this study.

Decision: use `28333de` as the latest historical source-verification snapshot,
not as a green full-repository result or a claim about later moving HEAD state.

## 2026-08-31 — compile follow-ons and E1e snapshot/use attack

1. The independent implementation track committed `284ffb`, the first
   explicitly assembled `Text2EnvCompileHandler`; a clean archive of that
   commit ran its ten focused tests successfully.  `1a1f3d8` then added CAS
   package publication/materialization, `e98e8c1` passed effective parameters
   to dependency resolvers, and `5915315` added compile-specific mutable
   dependency receipts.  These are post-E0–E2 source follow-ons.
2. A directed source audit falsified the implementation log's stronger input-
   freeze claim at `284ffb`: `_ArtifactCollector` copied the catalog to CAS,
   but both trust validation and `compile_scene` still consumed the original
   mutable path.  Replacing a digest-valid, allowed-root catalog after the
   check with another valid catalog pointing at sibling outside-root assets
   produced `RunStatus=succeeded` and outside-root `resolved.source_files`.
3. Before production changed, E1e was added to `experiment_protocol.md` and a
   direct mutation regression was committed at `ef5e29e`.  At stable source
   commit `5915315` the isolated test failed exactly at the allowed-root
   assertion (`mutation_guard=0/1`, pytest exit 1); no validator threshold or
   expected outcome was changed after seeing the failure.
4. Commit `910ccb1` changed `_ArtifactCollector.input_catalog_path` to the
   digest-verified CAS path immediately after `put_file` + re-resolve.  The
   original locator remains an audit alias only.  The unchanged E1e test then
   passed (`mutation_guard=1/1`).  The complete Harness directory produced
   74 passed with 1291/1291 statements and 312/312 branches, both 100%, while
   HEAD remained `910ccb1` for the run.
5. A second, deliberately separate attack forced `solve_scene` to fail after
   generation-QC admission.  The run correctly became
   `blocked/T2E_SOLVER_EXHAUSTED`, but the generated asset remained under the
   shared library.  Admission is therefore a pre-solve side effect, not a
   compile transaction.  The receipt also remains `pending_settle` while its
   ledger incorrectly says `backend=sapien/verdict=pass` without settle.
   This P1 requires stage–commit–abort/concurrency semantics; deleting a shared
   directory from a catch block was rejected as an unsafe pseudo-rollback.

Decision: keep E1e because the preregistered mutation changed from fail to pass
with full Harness coverage and without relaxing core validation.  Do not claim
atomic asset promotion, physical qualification, or a complete compile→replay→
validate spine; the admission transaction and replay/validate/MCP remain open.

## 2026-08-31 — final command-path corrections and focused reruns

1. The four frozen state/binding probes were rerun in the shared tree and each
   reported accuracy 1.0; the E2 benchmark output was byte-identical to the
   committed artifact.
2. An initial combined pytest command invoked the Stage 5 test from repository
   root without its required import context and failed collection with
   `ModuleNotFoundError: generate_scene`.  This was a command-path error, not a
   product failure.  Rerunning E2 alone from the repository root gave 8 passed;
   rerunning Stage 5 from `self_improving/stage5` gave 10 passed.
3. At stable commit `284ffb`, both default root pytest and the platform script
   exited 2 on the known duplicate `test_registry.py` import mismatch.  This
   preserved the distinction between focused green checks and a non-green
   full-repository snapshot.  Later source verification is recorded separately
   rather than silently replacing the `1180aef` E0–E2 counts.
4. The independent track then committed `50e8f18`, using a single streaming
   hash+copy, fsync and atomic rename for coherent CAS capture, and `51447da`,
   requiring a qualification report digest to resolve to matching CAS bytes.
   Source review limits the latter: Registry still does not interpret report
   domain content or reconcile the report/source/actual handler implementation.
5. An attempted current-tree Harness rerun at stable HEAD `51447da` stopped in
   collection because a concurrent, uncommitted `test_run_store.py` imported an
   incomplete `run_store.py`.  This red TDD did not exist in the clean commit
   receipt and was not modified or treated as an ASPIRE regression.  The report
   therefore cites 76 passed/100% for `51447da` only as its implementation-log
   claim; the latest independently completed Harness run remains 74 passed and
   100% statement/branch coverage at stable `910ccb1`.

## 2026-08-31 — clean `51447da` verification supersedes the implementation-log claim

1. To separate committed source from the shared tree's uncommitted TDD, commit
   `51447da0e982875d46222ed1a25c533925b4a300` was exported with `git archive`.
   `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider` exited 2
   on the known duplicate `test_registry.py` import mismatch.
2. On the same archive, adding `--import-mode=importlib` completed collection and
   produced 184 passed / 1 failed.  The sole failure remained the public CLI
   contract drift: actual failure stage `parse`, expected
   `scene_spec_validation`.
3. The directed Harness command ran 76 tests successfully but exited 1 at the
   100% coverage gate: 1313 statements had one miss and 322 branches had one
   partial branch, for 99.88%.  The uncovered production path is
   `handlers/text2env_compile_dependencies.py:154`, which ignores
   `__pycache__`/`.pyc`/`.pyo`; existing tests do not deterministically create
   that case.  A dirty environment can cover it accidentally, so the
   implementation log's 100% result is not a clean reproducible receipt.
4. A pure archive cannot run `script/run_self_improving_tests.sh` because the
   script first asks Git for the repository root.  A separate detached shared
   clone of the same commit was therefore used, with the two required external
   submodules initialized.  Its module audit reported `ready=true`; the canonical
   platform script then exited 2 on the same duplicate-test collection error
   before later suites ran.
5. No current-worktree source or concurrent test was edited.  The clean archive
   and detached-clone results are recorded in `source_receipt.json`; the
   temporary paths are execution aids, not durable evidence identities.

Decision: supersede only the `51447da` implementation-log coverage claim.  Keep
the independently green 74-test E1e receipt at `910ccb1`; classify `51447da` as
the latest clean but non-green source diagnosis with three independent blockers:
default collection, CLI behavior drift, and the Harness coverage branch.

## 2026-08-31 — fixed follow-on snapshot `148001d`

1. Two source follow-ons landed after the preceding fixed diagnosis: `e6ed0ff`
   binds an adaptive replay's video tail to the actual extended endpoint, and
   `148001d` maps the importable compiler's `T2E_REQUEST_REJECTED@parse` back to
   the historical CLI report stage `scene_spec_validation` without changing the
   typed compiler stage.  They are independent implementation work, not E0–E2
   interventions.
2. A clean archive was fixed at
   `148001dd4049346b9deadca1fec3bb8f588fc8d5`; no later HEAD was followed.
   Default root pytest still exited 2 on the duplicate `test_registry.py`
   collection mismatch.
3. Diagnostic `--import-mode=importlib` completed with 201 passed / 0 failed.
   This is direct evidence that the CLI behavior-contract failure recorded at
   `51447da` is closed at this source snapshot.
4. Directed Harness coverage again ran 76 tests successfully but exited 1:
   1313 statements / 1 miss, 322 branches / 1 partial, 99.88%.  The same
   `text2env_compile_dependencies.py:154` cache/bytecode exclusion branch remains
   uncovered in a clean environment.
5. The committed SAPIEN timeline receipt in `e6ed0ff` was inspected as an
   independent follow-on but was not rerun or counted as the study's N1 matched
   physical repair experiment.  It does not alter the E1/E2 claim boundary.

Decision: use `148001d` as the final fixed source-diagnosis snapshot.  The CLI
drift blocker is removed; default collection and the Harness 100% coverage gate
remain.  Do not reinterpret the independently green E1e receipt at `910ccb1` or
the time-pinned E0–E2 snapshot through these later commits.

## 2026-08-31 — study closure and control-plane receipt

1. Commit `4f289a15e06c609adf1b13879dc38e88d842dec5` froze the final report, research
   ledger, machine receipt, reader-facing repo docs, and the source-boundary
   corrections above on the existing `worktree/bingsheng` branch.  No new branch
   was created.
2. Final structural checks before that commit passed: staged diff check; JSON
   parse; five-column TSV shape; all 17 inspected local Markdown link sets;
   ASPIRE submodule pin/clean status; held-out artifact file SHA and canonical
   result SHA.  The official arXiv, NVIDIA project, and GitHub pages were also
   reachable during final source refresh.
3. The mandatory dashboard preflight was repeated at
   `2026-08-31T03:34:17+08:00`.  All three configured public JSON endpoints
   (`portfolio`, `tasks`, project `umi-world-model`) returned HTTP 404; the
   `clawcross-harness-agent dashboard` wrapper returned the same tasks endpoint
   404.  Therefore no dashboard/TODO state update is claimed.
4. Immediately after the report commit, the shared worktree contained one
   untracked concurrent test, `tests/self_improving/harness/test_runtime_events.py`.
   It was not authored, staged, tested, or committed by this study.  Its presence
   does not change the fixed-source receipts and prevents claiming a globally
   clean moving worktree.

Decision: close the ASPIRE autoresearch task.  The retained production changes,
negative findings, reproduction limits, fixed synthetic benchmark, latest clean
source diagnostics, and dashboard failure are all traceable without treating
concurrent uncommitted work as evidence.

## 2026-08-31 — post-close concurrent Harness boundary at `ab03859`

1. Three independently authored commits landed around the report/seal commits:
   `4836ebf5a7ee757219b7c40d1730e81909876ed0` added a production qualification
   bundle loader, `38518e5b423abf69bf59d737e51e93209b69d0c5` fixed ledger wheel packaging,
   and `ab0385967531737b4ae075f65af1fd9d224e03dc` added a SQLite RunStore.  They
   were frozen at `ab03859`; later untracked application/runtime-events TDD was
   not followed or treated as committed behavior.
2. A clean archive of `ab03859` ran 133 Harness tests successfully.  The 100%
   gate still failed at 99.91%: 1673 statements / 1 miss and 436 branches /
   1 partial, again the bytecode/cache exclusion at
   `text2env_compile_dependencies.py:154`.  Default root pytest still exited 2
   on the duplicate `test_registry.py` collection mismatch.
3. Source review found no production callsite for `load_qualification_bundle`
   at this commit.  The loader verifies an already packaged pass receipt/report,
   a manifest's listed implementation files and two source trees; it does not
   execute its regression command or a development/clean qualification suite.
   Its two sequential CAS writes are not an atomic promotion transaction, and
   implementation-root helpers omitted from the manifest are not rejected.
4. The packaging fix includes the runtime ledger namespace and declares a
   future `qualified_skills/**/*.json` resource glob, but the fixed tree contains
   no actual qualification bundle.  Its packaging test proves the ledger wheel
   path; the glob assertion does not prove an installable pass bundle.
5. RunStore durably stores Invocation and terminal RunState and cross-checks the
   event journal, but the fixed tree has no Registry callsite.  It accepts the
   caller's invocation digest instead of deriving the complete candidate/spec/
   simulator/split/gate identity, stores no running state for resume, and does
   not resolve artifact bytes.  ReplayOutput still lacks package/run reverse
   bindings and permits media refs without a payload schema.

Decision: retain all three modules as independently authored prerequisites, but
do not revise the final P0 gaps to “closed.”  Qualification/promotion transaction,
identity-frozen resumable EvaluationRun, and complete package→run→media binding
remain open at the fixed post-close snapshot.  This addendum does not change any
ASPIRE keep rule, E1/E2 metric, or physical-robustness conclusion.

## 2026-08-31 — moving-branch cutoff after the post-close audit

While the fixed `ab03859` source audit was being written, the shared branch
advanced through `c365874`, `8d9a01c`, `587b49f`, and `0e6716a`, with additional
uncommitted compile-CLI TDD visible afterward.  These states arrived after the
frozen archive and were not read into, tested by, or credited to this study.

Decision: stop the moving-target chase at `ab03859`.  Every P0 statement in the
post-close addendum is explicitly scoped to that immutable commit; later commits
may change implementation status and require their own clean archive review.
This cutoff preserves empirical reproducibility instead of continuously
rewriting conclusions from a concurrently mutating worktree.

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

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


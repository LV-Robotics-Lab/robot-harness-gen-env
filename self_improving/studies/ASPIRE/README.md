# ASPIRE × Robot Harness study

This directory is the evidence ledger for the study requested on 2026-08-31:
whether [ASPIRE](https://arxiv.org/abs/2607.00272) can improve this repository's
agent harness, robust simulation-environment generation, or self-improving
learning loop.

The study follows four non-negotiable rules:

1. The deterministic `scene_gen/` compiler and its runtime physics gates remain
   authoritative. A render, agent opinion, or learned score cannot promote a
   scene that those gates reject.
2. Paper claims are separated from source-code observations, local
   reproductions, and project-specific hypotheses.
3. Development evidence and held-out evaluation remain disjoint. Held-out
   outcomes never edit the candidate skill memory being evaluated.
4. Every retained change needs a measured improvement under a fixed protocol;
   otherwise it is discarded or left as a clearly labelled study artifact.

## Study map

- `final_report.md` — integrated Chinese conclusion, evidence table, retained
  changes, claim boundaries, and recommended next architecture.
- `research_log.md` — chronological decisions, commands, observations, and
  deviations; this is the main audit trail.
- `preflight.md` — ASPIRE upstream reproduction requirements versus this host.
- `source_receipt.json` — machine-readable upstream source and local baseline
  receipt.
- `paper_and_official_sources.md` — primary-source paper/project/code reading.
- `upstream_source_audit.md` — pinned-code harness and security/reliability audit.
- `current_harness_audit.md` — current-project harness boundary and gap audit.
- `reproduction_report.md` — exact upstream/local reproduction boundary.
- `experiment_protocol.md` — preregistered hypotheses, metrics, and keep rules.
- `results.tsv` — autoresearch experiment ledger.
- `experiments/` — benchmark code, fixtures, and experiment descriptions.
- `artifacts/` — generated, reviewable result bundles.
- `logs/` — bounded command outputs needed to reproduce conclusions.

## Current status

Research is complete for the preregistered local scope. The retained outcomes
are producer-declared runtime scene/digest equality, nonfinal pending-review and
static-only semantics, and a development-validated trigger-memory candidate
that reached mHRC 1.00 versus 0.50 for reactive/no-memory on 120 fixed synthetic
fault cases. One fresh RoboTwin/SAPIEN replay passed the declared-equality gates,
but no matched physical rollout
experiment tested memory-driven scene repair; therefore no simulator-robustness,
policy-learning, or paper-scale superiority claim is made.

Full LIBERO-Pro reproduction remains an explicit infrastructure boundary: the
official protocol requires eight GPUs, gated perception weights, three services,
dedicated environments, fixed splits, and model access, while this host exposes
one GPU and lacks that complete identity. The unchanged pinned no-service test
subset reproduced as 20 passed / 4 explicit skips in a locked environment.

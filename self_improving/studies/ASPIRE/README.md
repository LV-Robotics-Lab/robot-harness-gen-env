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

- `research_log.md` — chronological decisions, commands, observations, and
  deviations; this is the main audit trail.
- `preflight.md` — ASPIRE upstream reproduction requirements versus this host.
- `source_receipt.json` — machine-readable upstream source and local baseline
  receipt.
- `paper_and_official_sources.md` — primary-source paper/project/code reading.
- `current_harness_audit.md` — current-project harness boundary and gap audit.
- `results.tsv` — autoresearch experiment ledger.
- `experiments/` — benchmark code, fixtures, and experiment descriptions.
- `artifacts/` — generated, reviewable result bundles.
- `logs/` — bounded command outputs needed to reproduce conclusions.

## Current status

Research is in progress. Full paper-scale LIBERO-Pro reproduction is not being
claimed: the official protocol requires eight GPUs, gated perception weights,
three services, and dedicated environments, while this host currently exposes
one GPU and none of those services or environments. The study continues with
upstream source/unit reproduction and a project-native, held-out harness
experiment rather than weakening or silently changing the official protocol.


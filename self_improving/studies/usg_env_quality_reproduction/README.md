# USG /gen-env Quality Reproduction

This directory is an independent, evidence-first reproduction for one narrow
question: can the released Universal Scene Graph (USG) project directly close
the physical-quality gaps observed in `/gen-env` scene generation?

The experiment never modifies `/gen-env`, RoboTwin, or machine configuration.
It uses the checked-out RoboTwin cabinet and basket assets, SAPIEN/PhysX, the
released USG source, and the current `/gen-env` validator as read-only inputs.

## Evidence rules

- Every quantitative claim is derived from committed scripts and raw CSV/JSON.
- Physics labels come from the actual RoboTwin collision assets in SAPIEN.
- A dynamic-shadow actor is used where PhysX cannot report static-static
  contacts. Production static-static behavior is measured separately.
- Random-weight USG output is used only to verify the executable tensor
  contract, never as semantic-accuracy evidence.
- Missing checkpoints are reported as an evaluation limitation, not filled in
  with guessed or substitute weights.
- The final report distinguishes native USG capability from what a future
  adapter or a separate physics validator could add.

## Reproduce

Use the existing Python environment; no package installation is required:

```bash
PY=/home/jingxiang/miniconda3/envs/env-gen-sc311/bin/python

$PY scripts/run_physics_experiments.py --config config/experiment.json
$PY scripts/run_sweep_sensitivity.py --config config/experiment.json
$PY scripts/run_validator_attack.py --config config/experiment.json
$PY scripts/run_usg_contract_probe.py --config config/experiment.json
$PY scripts/build_evidence.py --config config/experiment.json
$PY scripts/audit_results.py --config config/experiment.json
```

The canonical machine-readable summary is `report/artifact.json`. The portable
technical report is `report/report.html`; raw observations remain under
`data/raw/`, derived tables under `data/derived/`, and visual evidence under
`media/`.

## Scope of inference

The experiments can establish whether the released USG implementation natively
produces the state needed for geometry and physics gates, and whether the
current `/gen-env` failure is observable from its own runtime contract. They
cannot establish the semantic accuracy of unreleased USG weights. They also do
not claim that scene graphs are useless: a trained USG parser could improve
semantic object/relation grounding, but that is a different claim from physical
feasibility or stable support.

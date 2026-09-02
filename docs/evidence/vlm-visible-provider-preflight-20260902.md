# Local typed Qwen visible-provider candidate preflight — 2026-09-02

Companion structured report:
[`vlm-visible-provider-preflight-20260902.json`](vlm-visible-provider-preflight-20260902.json),
SHA-256 `b593a63b63ca2b1a0eb74f72db4c7627e68e0648a7a2740f27cf2e7ed3b61e74`.

## Result

The frozen candidate implements the runner-shaped visible-provider seam for only the typed study
arms. `A1_typed_abstaining_critic_3b` is bound to `primary_local_vlm`, and
`A2_typed_abstaining_critic_7b` is bound to `confirmatory_model_size_ceiling`.
`A0_current_critic_3b` fails before the typed backend and still requires its independent frozen
baseline provider.

This is a code and contract preflight, not a model run. No real model was loaded, no inference or
GPU work occurred, and no network access occurred. The candidate is not production-eligible and
study execution remains unauthorized.

## Frozen files

| File | Lines / bytes | SHA-256 |
| --- | ---: | --- |
| `visible_qwen_provider.py` | 1,141 / 46,185 | `c8705c6c01c34a7a130194b1c4072fd398ca8a7c24e933b1d770574226d9f788` |
| `test_visible_qwen_provider.py` | 2,005 / 70,305 | `07a298516c70ca160f3937b8a4c8299400a636e7a067d13440db999e0d22eff8` |

## Checked behavior

- `LocalQwenVisibleProvider` binds the exact arm/model role, revision, snapshot manifest, model
  content manifest, roster, local snapshot, processor bounds, provider source, and backend identity.
- Image inputs are rebound in declared artifact order and checked for canonical relative identity,
  stable bytes, size, digest, supported format, dimensions, and decoded-pixel limits. The candidate
  allows at most six images, 32 MiB each and 64 MiB total. Raw model output is capped at 1 MiB.
- The concrete `TransformersQwenVisibleBackend` requires `HF_HUB_OFFLINE=1` and
  `TRANSFORMERS_OFFLINE=1` before dependency discovery and again before every generation. Model and
  processor loading use `local_files_only=True` and `trust_remote_code=False`.
- The backend records and live-rechecks selected Python, package entrypoint, CUDA, and configured
  device facts. This fingerprint is explicitly partial: `dependency_closure_complete=false`.
- CUDA timing and peak-memory reads are scoped to the configured device and its current stream.
  The backend does not qualify bitwise GPU parity and does not turn the request seed into a claim of
  deterministic kernels.
- Every returned provider outcome keeps `claims_physical_pass=false`. Visible review remains an
  advisory and cannot replace static or runtime physics gates.

## Reproduction

Provider contract, attacks, and coverage:

```bash
python -m pytest -q \
  self_improving/studies/vlm_fallback_prompt_optimization/test_visible_qwen_provider.py \
  --cov=self_improving.studies.vlm_fallback_prompt_optimization.visible_qwen_provider \
  --cov-branch --cov-report=term-missing --cov-fail-under=100
```

Result: `143 passed`; 571 statements and 200 branches, both 100% covered.

Adjacent runner regression:

```bash
python -m pytest -q \
  self_improving/studies/vlm_fallback_prompt_optimization/test_runner.py
```

Result: `230 passed`.

The adjacent study suite is reproducibly green only with the two known frozen-inventory checks
explicitly deselected:

```bash
python -m pytest -q self_improving/studies/vlm_fallback_prompt_optimization \
  --deselect self_improving/studies/vlm_fallback_prompt_optimization/test_protocol.py::test_frozen_spec_and_inventory_are_valid \
  --deselect self_improving/studies/vlm_fallback_prompt_optimization/test_protocol.py::test_cli_validate_and_inventory
```

Result: `665 passed, 2 deselected`.

The unfiltered command is **not green**:

```bash
python -m pytest -q self_improving/studies/vlm_fallback_prompt_optimization
```

It returns `665 passed, 2 failed`. Both failing protocol checks traverse the frozen inventory and
stop at protected PEARL image artifacts absent from this working tree. The filtered result is useful
for adjacent regression coverage, but does not replace that missing evidence or make the full suite
green.

Static gates:

```bash
python -m ruff check \
  self_improving/studies/vlm_fallback_prompt_optimization/visible_qwen_provider.py \
  self_improving/studies/vlm_fallback_prompt_optimization/test_visible_qwen_provider.py
python -m ruff format --check \
  self_improving/studies/vlm_fallback_prompt_optimization/visible_qwen_provider.py \
  self_improving/studies/vlm_fallback_prompt_optimization/test_visible_qwen_provider.py
git diff --check
```

All three static gates passed.

## Claim boundary

The annotation manifest remains `pending_blinded_annotation`; `execution_authorized=false`.
This slice changed no amendment, runner, experiment spec, runner source manifest, run log, or sealed
annotation manifest. It provides no quality or effectiveness result, no complete dependency closure,
no bitwise GPU parity qualification, and no production eligibility. A future authorized experiment
must first close the annotation and study-authority gates and then record real model, GPU, network,
and inference receipts independently.

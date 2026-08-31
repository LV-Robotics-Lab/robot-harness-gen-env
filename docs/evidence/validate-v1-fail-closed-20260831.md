# Validate v1 fail-closed promotion boundary — 2026-08-31

`text2env.validate@1.0.0` can deterministically recompute the existing physical validation report,
but its public input contains only an environment package and runtime-evidence JSON. It cannot bind
the replay receipt, decoded media, immutable runtime-asset snapshot, compile/replay run receipts,
qualification artifacts, or request provenance required for publication.

An independent counterexample supplied a seven-key runtime-evidence document and an injected
eligibility policy returning no blockers. Before this patch the v1 handler returned
`validation_status=pass` and `publishable=true`, despite having none of the promotion evidence above.

The v1 handler now treats injected eligibility policies as diagnostic blocker contributors only.
Every physical pass also carries `T2E_VALIDATION_INCOMPLETE` with reason
`validate_v1_cannot_bind_promotion_evidence`; therefore v1 can produce a validation report but can
never authorize publication. This is a safety patch, not a claim that validate v2 is complete.

Verification: 27 targeted tests passed; `text2env_validate.py` reached 129/129 statements and 30/30
branches. The new regression proves that a custom policy returning an empty blocker set still yields
`publishable=false` and a report-bound incomplete blocker.


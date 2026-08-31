# System 2 → compile application boundary — 2026-08-31

## Decision

`CompileApplication.invoke_typed()` is the only CAS-native application entry for a
System 2 dispatcher. It accepts an exact `Text2EnvCompileInput`, revalidates the
retained model, requires the catalog locator to equal
`artifact://sha256/<ArtifactRef.sha256>`, and resolves the object from this
application's own CAS before invoking the qualified Registry entry.

The existing path-based `compile()` entry remains the operator boundary. It
snapshots an explicitly trusted external catalog and then delegates to the same
typed entry. Therefore an agent cannot use `file://`, a foreign CAS identity, or
a retained model mutated after construction to bypass the snapshot boundary.

## Verification

Command:

```text
python -m pytest -q tests/self_improving/harness/test_application.py \
  --cov=self_improving.harness.application --cov-branch \
  --cov-report=term-missing --cov-fail-under=100
```

Observed result: 21 tests passed; `application.py` reached 129/129 statements and
20/20 branches.

The attacks cover a matching `file://` object, a missing foreign CAS object, a
mutated retained Pydantic model, and an untyped mapping. Ruff, formatting, and
`git diff --check` also passed for the two implementation/test files.

## Honest boundary

This is an application-interface qualification, not a new physical or production
compile qualification. The checked-in `text2env.compile@1.0.0` qualification must
be regenerated after the concurrently changed asset-admission, Registry, and
ledger implementation bytes are frozen. Until that happens, a real
System 2 → production compile run must fail closed at application assembly.

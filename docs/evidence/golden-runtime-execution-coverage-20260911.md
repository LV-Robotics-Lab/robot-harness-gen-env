# Final fixed launcher / child coverage

Production sources remain fd50576. Exact-source byte equality is checked before relocating
measured arcs; `coverage-provenance.json` records each real measurement. No fake child or
Harness private-helper replacement was used. This supersedes coverage-boundaries.md.

- Launcher: 134/137 statements (97.81%), 42/46 branches (91.30%).
- Child: 142/145 statements (97.93%), 31/34 branches (91.18%).
- The requested 100% gate remains unmet.

New public negatives cover missing members, byte drift, symlinks, explicit host-native
mismatch, and real socket-denied/runtime-writable constraints. Seven isolated unchanged
child processes additionally exercised wrong ABI/environment and kernel-returned failures
for Landlock query/create/add-rule/restrict and network-filter installation. Those kernel
errors originate from real inherited seccomp restrictions, not substituted return values.
Another actual child allocated 150000 mappings and completed physics before exceeding its
8 MB maps bound. These diagnostic instruments issue no production authority.

The public Producer postflight case copies a private backend root and adds a member only
after the real child writes partial-probe.json. Deployment verification rejects that drift;
stdout/stderr, all six physical refs, and 1001 rows remain accessible after cleanup. It
passed in 234.73 s. Approved source and relocated roots were not modified.

Remaining launcher paths:

- 92 false: storage/open failure before one output stream exists. Both are opened for normal
  subprocess failures; no disposable failing filesystem or deterministic public storage
  fault input is available. This remains untested, not unreachable.
- 263: a dependency changing terminal environment/thread state after the child's startup
  checks. Wrong supplied identity rejects earlier. No observation replacement was used.
- 272: host GL bytes changing after their initial check. Testing requires a disposable
  system exposing those exact aliases; shared host libraries were not modified.
- 292: a real dependency loading a new run-output module/native member. Changing an existing
  manifest file rejects at the earlier deployment check. A fake result.json would not be
  meaningful evidence, so this remains an explicit real-runtime test gap.

Remaining child paths:

- 118 is shielded by local construction: ArchiveBackend receives roots['genesis'] and
  compares that unchanged source-root with the same mapping. No internal mutation is exposed.
- 120 needs real dependency initialization to mutate the fixed process environment.
  Supplying a wrong environment already rejects at line 99; it would not exercise this gate.
- 171 needs a real different Torch namespace implementation. The pinned distribution
  creates the expected types. No type replacement was performed merely for coverage.

All other originally listed missing paths were executed naturally. Initial test regex
assumptions were corrected to actual specific errors; production already failed closed.
The bad-XML test's original overly narrow two-stream assertion and its preserved failure
log remain historical evidence; its corrected form also verifies the partial-CAS index.

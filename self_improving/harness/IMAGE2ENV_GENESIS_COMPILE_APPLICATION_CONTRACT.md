# Native compile application and qualification

`Image2EnvGenesisCompileApplication(state_root, compiler)` uses the existing
`SkillRegistry`, `SQLiteRunStore` and `SQLiteEventJournal`. It creates no second run
table or state authority. `evaluate(request)` executes the unregistered native
`image2env.compile@2.0.0` candidate through the same typed kernel. Its output is the
versioned native compile receipt, retained in CAS with the exact request and scene.

`qualify(candidate_run_ids)` consumes successful candidate records from this trusted
SQLite authority. At least three distinct image contents and seeds must share one
compile implementation. Every candidate binds its invocation, exact request, compile
receipt and the existing durable asset qualification. Application source and kernel
source identities are independently retained in CAS. The resulting typed report and
small `SkillQualification` authorize only deterministic static scene compilation.
They do not replace the separate scene replay runtime manifest or validate physics.

Each durable candidate's actual `Invocation.dependencies` must equal the current
application and kernel source identity. A self-consistent old output does not qualify
a changed application. Qualification metadata fixes the exact Skill version, case ID,
regression command and canonical CAS JSON reference before its report is consumed.

On restart, an explicitly supplied qualification reference is re-read against the
trusted candidate journal, installed implementation and full compile closure before
registration. `invoke_typed(request, run_id=UUID4)` delegates identity, pending intent,
terminal recovery and failure persistence to the existing shared execution kernel.
Terminal success additionally requires native compile deep consumption in the live
application and after restart; a missing nested representation member must reject
without a second compilation or a new state transition.
No generic invocation or new MCP projection is introduced here.

The initial three cases use Gujie's historical simulated mouse overview/top/side
images with operator-authored frozen interpretation proposals. They verify real image
decoding and exact asset reuse. They are not evidence of fresh reconstruction or
automatic image understanding. Registry's newly exercised UUID content identity is
the canonical string representation; previous non-UUID invocation identities retain
their existing semantics.

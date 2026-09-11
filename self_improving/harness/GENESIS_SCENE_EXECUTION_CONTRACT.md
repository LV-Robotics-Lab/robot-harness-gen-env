# Genesis finite-table scene execution

The public request is `GenesisSceneExecutionRequestV1`: the native image compile
receipt, its exact scene and asset qualification, and an independently approved
scene runtime manifest. The asset qualification retains its own historical
runtime scope; it does not qualify the renderer runtime.

`GenesisSceneExecutionProducer.produce(request_ref, roots=...)` first uses the
real `Image2EnvGenesisCompiler.read` and its trusted asset qualification SQLite
application. It then calls only the installed fixed scene launcher. That launcher
executes fresh baseline and half-dt children. The producer rechecks the complete
launch closure and the scene report before writing the execution receipt as its
last authoritative CAS object. Only this path returns `LiveGenesisSceneExecution`.

`GenesisSceneExecutionVerifier.read_receipt` is pure-read and always returns
`execution_authority=False`. It verifies exact request/compile/asset relationships,
both profile observations, complete runtime membership, the actual raw versus
typed interpreter and process environment facts, six-root deployment mapping,
module/native file identities, real denial probe results, thread counts, streams,
loaded scene, complete traces, 41 sequential PNG frames per profile, and exact
decoded lossless-video correspondence. The mathematical report remains
`trace_consistency_only`. Self-consistent JSON cannot recreate a live capability.

The raw observation records absolute deployment locators separately from logical
content identity. Only the fixed Genesis addition to `LD_LIBRARY_PATH` is allowed
between the actual startup and final environments. The static ffmpeg executable
is locked by distribution bytes and inherits the filesystem/network restrictions;
the contract does not claim an encoder native-mapping snapshot.

Failures retain the request, exception type/message, any lower-level diagnostic,
and all launcher failure artifact references. They do not publish a pass receipt.

## Durable replay Skill

`GenesisSceneApplication` uses the existing `SkillRegistry`, `SQLiteEventJournal`
and `SQLiteRunStore`, with a shared SQLite database. `evaluate` runs the actual
producer as an unregistered `image2env.replay@2.0.0` candidate. The candidate
invocation binds its exact typed request and fixed implementation dependency;
the terminal binds its output and execution receipt.

`qualify` accepts two distinct trusted local successful candidate run IDs, never
caller-supplied reports. Both must bind the same request, current implementation,
approved scene manifest, all 34 checks and the two complete media sequences.
The resulting qualification is a Registry `SkillDescriptorV2` with
`evidence_invariant_repeatable` semantics. It does not promise bitwise-repeatable
physics or videos. `bind` rechecks the trusted candidate records, CAS closure and
current implementation before restoring registration in a fresh application.

After registration, `invoke(parameters, run_id=UUID4)` uses the existing durable
kernel. Exact retries recover the terminal; changed canonical inputs conflict;
pending reservations cannot silently run Genesis again. A recovered success is
deep-read again, so damaged receipts or referenced CAS cannot become authority.
This module does not register image compile, validate, observation, robot policy,
or promotion capabilities.

## Verification status

Public RED logs and real runtime attempts are retained outside the repository.
The first formal candidate failed closed on runtime member inventory/byte drift;
its SQLite terminal, invocation and diagnostic remain evidence. Registration and
production success must be established by subsequent real candidate and durable
execution tests; the existence of this contract is not an acceptance result.

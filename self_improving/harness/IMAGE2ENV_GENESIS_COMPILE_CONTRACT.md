# Native image exact-reuse compile seam

`Image2EnvGenesisCompiler(store, qualification_application).compile(request_ref)` produces
an immutable native compile receipt; `read(receipt_ref)` reconsumes the complete input,
trusted qualification journal and candidate scene without running Genesis or writing state.
The compiler owns no mutable workflow state. It is a compile implementation awaiting the
same M1's fixed implementation/Skill qualification and existing Harness application wiring.

New `harness.image2env_genesis_compile_request.v1` embeds the unchanged
`Image2EnvCompileV2Input` plus a durable qualification run UUID, Gujie source envelope and
finite-table parameters. It does not alter old v2 semantics or the controlled application.
New `harness.image2env_genesis_compile_receipt.v1` binds that exact request, resulting
`GenesisSceneSpecV1`, approved asset qualification, source/proposal object mapping and
stable implementation file identities. Scope is `static_scene_candidate`, never replay pass.

Before output, the trusted qualification application's public read must return a qualified
terminal for the exact selected representation/runtime. Existing exact-reuse preflight
re-decodes the actual image, verifies the proposal/image/prompt/catalog/qualification closure
and requires the initial exact-only single-object on-table profile. Gujie source identity
must match the proposal object/category and selected representation. Placement comes from
the measured full geometry adapter, not the old fixed 0.74 m position.

This first slice deliberately does not grant Registry/MCP authority, infer scene content
from a media filename, execute an LLM, or reuse old Gujie physics reports. Actual Genesis
scene replay/dual-dt validation and new media consume its exact scene artifact separately.
Stable implementation identity covers executable source and schemas, not mutable provenance
ledgers or deployment paths. Relocation requires the trusted qualification SQLite and CAS,
whose existing application remains the asset authority.

The asset qualification's runtime lock proves its particular backend-load/contact probe.
The downstream scene replay has a separate renderer/runtime manifest and must prove that
execution independently. Compile does not silently equate those identities or transfer
old asset runtime authority to the renderer. Native Skill contracts use the new exact
`image2env.compile@2.0.0` namespace version; the existing `@1.0.0` remains unchanged.

The first real input is a fixed Gujie historical simulated mouse overview image, with an
explicit operator-authored proposal, seed 17 and known exact asset reuse. It demonstrates
actual image decoding and closed input references, not autonomous image interpretation
or fresh reconstruction. Top/side views are separate inputs and must retain distinct
media/proposal/request identities even when deterministic exact reuse yields the same scene.

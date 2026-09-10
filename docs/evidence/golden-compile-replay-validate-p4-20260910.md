# Golden compile → replay → validate P4 evidence (2026-09-10)

## Outcome

The production Golden workflow completed three exact qualified Skills in one workflow and one
frozen RegistrySnapshot:

```text
revision 0
  -> text2env.compile@1.0.0  succeeded, revision 1
  -> text2env.replay@1.0.0   succeeded, revision 2
  -> text2env.validate@2.0.0 succeeded, revision 3
```

The successful evidence root is
`/home/jingxiang/bingsheng/golden-p4-real-20260910.v8`. Its canonical `summary.json` has SHA-256
`dd43c3d7e7e84e5b9e006b7265e3b0e27c875a31cec3b123dc574dc5d6606a72`. The implementation
commit recorded by the runner was `63dfac7f0b84c4208bc3731cd4f6fca5cb0a4142`; the exact replay
qualification/report bytes used by the run were subsequently committed as `c78be99` without
changing the implementation closure.

## Trusted lineage

- The workflow advanced exactly `0 -> 1 -> 2 -> 3`; all three operation statuses are `succeeded`.
- Validate consumed the package, catalog, runtime evidence, runtime validation report, runtime
  asset snapshot, media verification, event transcript, compile/replay portable receipts,
  qualifications, and request provenance bound to revision 2.
- The validate trusted receipt has base revision 2 and base state
  `a3b0051e298fe0d1a99eadd24710e64009071c724b8b644179b93e5b562bd7ba`.
- Its workflow receipt points back to revision 2 receipt
  `ad09466b3101e62c670e54247c696044e47f6ee6a8699aae7527f05d49e1569d` and advances to
  revision 3 state `fa74699b534f95ae5065d1f635ef57d6f75163c4e049fd36c3e87449cf84d4f0`.
- The state delta contains only `environment.validation`; `fresh_observations` is empty.
- The decision is `validation_status=pass`, `publishable=true`, with no blocker. The validator
  independently regenerated report
  `054bb51927c95308a182773870cb2ccd68e07eb47233a0d031b8a989b94c713b` and issued decision
  receipt `f5a36aaf1abf6f556c343b7c5e486b9d5c4c7669a6545f67e1cc428bacf5785c`.
- A factory-free restart reconstructed the three bindings from durable child authorities and
  returned the identical terminal validate operation on duplicate submit. No fourth child was
  reserved.

The run imported 66 replay-qualification CAS objects and 114 validate-qualification CAS objects
into the single workflow evidence CAS before assembly. An independent recursive check recomputed
all 187 CAS filenames/digests and resolved 3,735 ArtifactRef occurrences (176 distinct digests),
including URI, byte length, and content digest. Every reference closed.

## Real physics and media

Replay used the fixed can-on-plate case with `precheck=0`, `settle=900`, `contact-window=120`,
`video=120`, and `fps=12`. The qualified runtime report remained pass with no failed or not-run
gate. The validate Skill did not trust that result alone: it reconstructed the snapshot and
recomputed the physical report before issuing its decision.

The retained MP4 is:

```text
/home/jingxiang/bingsheng/golden-p4-real-20260910.v8/view/observer_runtime-a41d05366050.mp4
```

Its SHA-256 is
`a41d05366050ba0c2af635e69299a12a4b2a0e78ed27490517d27b122fa12a4e`. An independent imageio
decode read 120 frames, 114 decoded-unique frames, 320×240 pixels, 12 fps, and 10 seconds. Seven
PNG views were also copied and independently identified as valid 320×240 or 640×480 images.

## Fail-closed attempts retained

Failed evidence roots were deliberately retained instead of overwritten:

- `v2` and `v3`: replay stopped before execution because the launch environment produced a
  capability digest different from the qualified capability.
- `v4`: the real simulator and both capability probes succeeded, but the process was not inside
  the delegated `supervisor` leaf, so the media verifier returned
  `HARN_DEPENDENCY_UNAVAILABLE / sandbox_unavailable`.
- `v5` and `v6`: attempts to substitute a new cgroup root were rejected during production
  assembly because operator roots differed from the qualification.
- `v7`: a direct cross-cgroup move was denied by the kernel.
- `v8`: the user systemd manager attached the child to the exact qualified scope's `supervisor`
  subgroup; the same fail-closed media verifier then passed and the workflow reached revision 3.

These failures show that a successful simulator exit, a playable MP4, or a newly created sandbox
root cannot bypass the qualified dependency and topology checks.

## Boundaries

This proves the legacy RoboTwin/SAPIEN compile → replay → validate line on this host. Here,
`publishable=true` means only that the legacy validation profile passed for the exact closed
lineage. It does not mean promotion occurred, does not satisfy a Genesis profile, and is not
`genesis.robot_policy@1` ready. This branch predates the portable replay integration, so the final
integration must regenerate both replay and validate qualifications at the integrated
implementation commit and repeat the relocated revision-3 run.

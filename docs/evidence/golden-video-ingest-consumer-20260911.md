# Golden X1 complete-sequence video ingest / consumer evidence — 2026-09-11

## Result

`PASS`, fixed at implementation commit
`13e8f01bd963375bdf65d8940434b240cbbb6117`.

This is a controlled decoder-protocol and CPU video-evidence result. It does not claim a sealed
production decoder, sandbox or qualification, a video2env compile handler, Registry/MCP exposure,
Genesis/GPU execution, physics, publishability, promotion, or robot-policy readiness.

## TDD and schema boundary

1. `506ad84` froze the public full-sequence ingest/consumer seam as RED.
2. The RED review found that `harness.video_frame_sequence.v1` was already a strict prompt-fallback
   frame-extraction lineage. It was therefore not redefined as a source-video manifest.
3. `13e8f01` added source-specific `harness.video_decoded_frame_sequence.v1`,
   `harness.video_media_probe.v1`, and `harness.video_source_media.v2`, then made the public seam
   GREEN.

The old fallback schema and `VideoSourceMedia` v1 remain byte-identical. Existing x2env descriptors,
contracts and checked qualifications were not rewritten. In particular, the current unqualified
`video2env.compile@1.0.0` input still consumes v1 media; wiring v2 into compile requires a later input
contract before first qualification.

## Implemented evidence boundary

`VideoMediaIngestor` accepts an exact `video/mp4` CAS source and a `VideoContainerDecoder`. The
controlled tranche permits one complete CFR `MP4 + H.264 + yuv420p` stream. The decoder must return
every raw frame and assert `fully_decoded=true`; the ingestor verifies source/frame/pixel limits before
publishing derived evidence.

Every decoded-frame record binds index, DTS, PTS, duration, byte count and SHA-256. The manifest and
probe bind reduced reciprocal FPS/timebase fractions, even dimensions, exact yuv420p frame bytes,
duration derived from the entire sequence, total/unique frame counts, and a domain-separated sequence
digest. `VideoMediaConsumerVerifier` is pure-read: it re-resolves the source/manifest/probe, performs a
second complete decode, rebuilds the expected documents and compares the whole `VideoSourceMediaV2`
closure.

## Executed gates

- Focused implementation/tests: `74 passed`.
- New core coverage: `343/343` statements and `104/104` branches, 100%.
- Adjacent x2env/prompt/compile/replay qualification regression: `155 passed`.
- Checked-in compile/replay/validate qualification identity gates: `3 passed`.
- Public schema catalog: `72` committed snapshots; all passed Draft 2020-12 schema checks and exact
  exporter comparison.
- Full repository suite: `4080 passed, 20 skipped in 261.29s`.

## Independent actual-decoder acceptance

A fresh git archive at `/tmp/golden-video-ingest-13e8f01.6jezas` used system FFmpeg/ffprobe 6.1.1 as
an acceptance-only decoder adapter. It actually generated and fully decoded an H.264/yuv420p MP4:

- source: 2,517 bytes,
  `9aad9a27be8d91a4f7f68dbf447e1bfa18974cc21ff67675466604fe8d170e97`;
- 18 ordered frames, 6 unique frames;
- FPS `6/1`, timebase `1/6`, duration 3,000 ms;
- sequence digest
  `a70265aea01ec383f2975dacba11f2d331bc885893292e3b701f03293e7a31b8`;
- final CAS: 5 files / 12,853 bytes.

A fresh ingestor retry returned identical refs without growing CAS. The consumer performed a second
complete real decode and left the CAS byte manifest unchanged. A 15/18-frame sample, reversed order,
duration drift and `fully_decoded=false` were rejected before derived publication. Source, manifest,
probe, width, count, sequence and missing-CAS consumer perturbations also failed closed.

Acceptance artifact identities:

- `acceptance-summary.json`:
  `b1c8b05ad031c89e11bf6d6db20198288e0b6dceae53fa8d8d367294bfe0cc4b`
- `pytest.xml`:
  `195cc9e2e3c483878e519f7a09199d2ae050565e283e283ebcbda8eb723d40d0`
- `coverage.json`:
  `a8182f2a9676c7e4d38aeed212f47811511f9bb81da3ad7e2ba437e259c1944c`

## Independent compatibility acceptance

A second independent archive acceptance confirmed all 72 schema/snapshot identities, 100% focused
coverage and the checked qualification gates. It also compared the parent/head blobs for the old
fallback parser and `VideoSourceMedia` v1, and verified that `x2env_contracts.py`, the complete
`qualified_skills/` tree and existing x2env schemas were unchanged. It found no tranche-local blocker.

## Provenance

The implementation is Harness-native. No Gujie runtime source bytes were copied. Gujie's fixed source
documented general media probing and a 15-frame sample, but that sample is explicitly insufficient for
this full-sequence identity. The system FFmpeg invocation above is independent acceptance evidence,
not a qualified production dependency.

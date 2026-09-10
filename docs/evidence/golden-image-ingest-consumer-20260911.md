# Golden X1 image ingest / consumer evidence — 2026-09-11

## Result

`PASS`, fixed at implementation commit
`fe271db55992c6d0880ba2a8903dc233bce2bb3d`.

This is a CPU/Pillow X1 media-ingest result. It does not claim an image2env compile handler,
qualification, production Registry/MCP exposure, Genesis execution, physics, publishability,
promotion, or robot-policy readiness.

## TDD and acceptance history

The slice deliberately preserves the failure history instead of treating the first GREEN as final:

1. `45e0153` froze the public ingest seam as RED.
2. `ae05517` implemented deterministic source ingest and first passed the focused tests.
3. Concentrated functional acceptance then found three real gaps:
   - the public probe did not require `source.media_type == declared_media_type`;
   - Pillow could preserve ICC/text/EXIF metadata in the alleged canonical PNG, so identical pixels
     could have different canonical digests;
   - downstream code had no pure-read deep consumer that re-read source/canonical/probe CAS and
     cross-bound every inline `ImageSourceMedia` field.
4. `3fd03f4` committed those findings as RED tests.
5. `fe271db` made them GREEN with strict source-MIME binding, metadata-free pixel
   canonicalization, and `ImageMediaConsumerVerifier`.

## Implemented evidence boundary

`ImageMediaIngestor` accepts an exact raw-media `ArtifactRef` from the same
`LocalArtifactStore`. It enforces configured source-byte and decoded-pixel bounds, fully decodes a
single JPEG/PNG/WEBP image, rejects animation and MIME disagreement, applies EXIF orientation, and
copies normalized pixels into a fresh image. It then publishes:

- a metadata-free deterministic PNG;
- a canonical `harness.image_media_probe.v1` document;
- an `ImageSourceMedia` whose source, canonical image, probe, dimensions, mode, and frame counts
  all point to that closure.

`ImageMediaConsumerVerifier` is pure-read. It resolves and hashes all three artifacts, decodes the
source again, recomputes normalized pixels, canonical PNG bytes, pixel digest, decoder identity and
canonical probe JSON, then compares every mirrored field. It does not repair or write CAS objects.

## Executed gates

- Focused implementation/tests: `38 passed`.
- New core coverage: `178/178` statements and `44/44` branches, 100%.
- Adjacent image/schema regression: `76 passed`.
- Public schema catalog: `69` committed snapshots, exact exporter check passed.
- Full repository suite: `4014 passed, 20 skipped in 258.63s`.

Independent archive acceptance used the fixed implementation from a fresh source export and wrote
only external evidence under
`/tmp/golden-image-consumer-fe271db.FwjByH/evidence`:

- `L`, `RGB`, and `RGBA` inputs each had clean, ICC-only, text-only, EXIF-only, and combined
  metadata variants. Within each color mode the five distinct source digests converged on one pixel
  digest and one canonical PNG digest.
- Every canonical PNG contained only `IHDR`, `IDAT`, and `IEND`; `iCCP`, `tEXt`, and `eXIf` were
  absent.
- Independently generated JPEG, PNG, and WEBP closures passed the consumer. Exact CAS manifests
  before and after every call were equal.
- Source, canonical, probe, width, height, color-mode, noncanonical-probe, and missing-CAS
  perturbations all failed closed. CAS stayed at 41 objects / 21,992 bytes.
- All 69 schemas passed Draft 2020-12 validation and matched the 69 committed snapshots.

A second independent archive acceptance exercised the contract/qualification boundary and also
passed:

- 38 image ingest/probe/consumer/schema tests and 192 x2env contract plus compile/replay
  qualification tests passed.
- The project venv, which lacks `pip`, ran the non-packaging root as `4012 passed, 20 skipped`;
  the two packaging cases passed separately under the same machine's Python with `pip`. Together
  this agrees with the direct full-root result of 4014 passed / 20 skipped.
- Both unqualified x2env compile descriptors stayed on input v2 with
  `handler_registered=false` and `qualification_available=false`.
- Existing qualified text compile/replay identity and strict qualification tests stayed unchanged.

Machine-readable external acceptance identities:

- `acceptance-summary.json`:
  `0092b1d3b1aa1ab6c237642003dca3bf1745604cb83c1211e52c7ebf37eb96d0`
- `pytest.xml`:
  `87b7be4472b5853b26633caea06b751a4601e2d930c513fc1b889ace2a9b848a`
- `coverage.json`:
  `f5b2647c382247a72d3c43ac9f07a50207670e046fee812d38b6766abd46ff62`

## Provenance

The code is a Harness-native implementation using Pillow and the existing Harness CAS. No Gujie
runtime source bytes were copied. Gujie's fixed source remains relevant to the wider x2env media and
Genesis adapter contract, but this pass proves only the independent image ingest/consumer boundary.

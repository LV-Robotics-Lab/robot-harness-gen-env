# PEARL evidence portal migration

This directory is a history-preserving subtree import of the PEARL
Self-Improving Agents evidence portal.

- Source tip: `49a5e57e235dcb0292f6fa109a6af5cec074be2f`
- History merge: `96655164e10d50d5c56b4a0086cfca6e2cc28f3e`
- Destination: `apps/pearl_evidence_portal/`
- Preserved: all 12 source commits, including the six commits that were ahead
  of the source remote, portal source, build/test code, and the bounded hosted
  report subset under `public/reports/`
- Excluded because they were untracked generated state: `node_modules/`,
  `dist/`, `.next/`, `.vinext/`, `.wrangler/`, `outputs/`, and `work/`

The portal is a presentation layer. Its text and media do not replace the
machine-readable acceptance gates in `scene_gen/` or `self_improving/`.

Validate it from this directory with:

```bash
npm ci
npm test
```

# Yuxin ignored workbench snapshots

The original `env-gen-dev` repositories ignored `work/`, so these scripts and
notes were absent from normal `git status` and from every preserved branch.
They were recovered before deleting any contributor checkout.

- `yuxin-web/work/`: ignored asset-spike, nightwatch, and one-off files from
  `/home/jingxiang/yuxin/env-gen-dev` at web worktree head `ec477bc`.
- `yuxin-main/work/oneoff/`: ignored one-off files from
  `/home/jingxiang/yuxin/wt-main` at main head `ecea99f`, including the
  interrupted 44-case attribute matrix driver.

This is a read-only source snapshot, not a supported execution entry point.
Its files retain historical absolute paths and assumptions so the original
experiments remain auditable. Current runtime configuration belongs in
`../active/runtime_config.py`; reusable logic should be promoted into
`../active/` with tests before use.

The capture contains 42 text/source files totaling about 328 KiB. The SHA-256
of the sorted per-file SHA-256 stream is
`7a076638f632021cad8831b4be4e37c3cb00cc626d5115984b30ae24e5b56ae9`.
Compiled bytecode, a copied texture, an Isaac installer log, meshes, rendered
media, and run outputs were excluded.

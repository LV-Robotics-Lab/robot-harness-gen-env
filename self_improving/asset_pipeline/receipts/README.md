# External asset receipts

`acquired_manifest.json` and `asset_library_metadata/` preserve the catalog,
aliases, source channel, sizing policy, ledger state, and model metadata for the
eight uncommitted assets recovered from `jingxiang:/home/jingxiang/yuxin/env-gen-dev`.

The GLB files and PNG snapshots are not redistributed in this Git repository.
They are third-party/downloaded assets rather than source code, and several
carry source-specific terms. The canonical cache paths and exact visual-mesh
digests at capture time were:

| Asset | Cache-relative visual mesh | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `317_tissuebox` | `data/asset_library/317_tissuebox/visual/base0.glb` | 24,348 | `d33395ffe7dfc14b9cff3ac2bb0cdd22d889734966181183afee66d16811377d` |
| `318_scissors` | `data/asset_library/318_scissors/visual/base0.glb` | 3,959,120 | `984ea1b51ce020deb4b1094cf774b1e5dd04e93d6ecf133fba0de6c908f8073e` |
| `319_snowman` | `data/asset_library/319_snowman/visual/base0.glb` | 117,924 | `660017706d3726872d78477aaf3e168ffff968a3175b5da8bfc09230108078cb` |
| `320_teddy_bear` | `data/asset_library/320_teddy_bear/visual/base1.glb` | 420,460 | `a9fdc4cf3f296bec333ee573ab9f808003665ef452b984fea764d9c42610bf7e` |
| `321_dustbin` | `data/asset_library/321_dustbin/visual/base0.glb` | 438,372 | `eac9c505ae89875273a26aeb48926b2e76b3d408950b8bd9eed51dfa32d14e3c` |
| `322_raccoon` | `data/asset_library/322_raccoon/visual/base0.glb` | 7,154,688 | `702fa8707e858224cfb662a32b9ed916e7e4d7a1ae3a034bbdf17e5e58a5cb29` |
| `323_pallets` | `data/asset_library/323_pallets/visual/base0.glb` | 393,148 | `25af8404b04c411fa659edb51f9b1d2c5bf19c8771f7c0b662d22e84602cca00` |
| `324_knife` | `data/asset_library/324_knife/visual/base0.glb` | 682,992 | `d78f121e541e39f82e4cd26bfa3821e6faa20d9b52c5e38c291c1ccffd0f5ce0` |

Each visual mesh was also used as the collision mesh and therefore had the same
digest. Regeneration and acquisition code lives in `../active/1_asset_reuse/`.

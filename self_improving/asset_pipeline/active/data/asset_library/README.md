# asset_library/ — 外部资产权威真库

本目录是**我们自己采购、转换、验证、上账的资产本体**（RoboTwin 布局）。`data/`
下只有它和 `robotwin_assets/` 装着真的网格与纹理，其余目录要么是软链拼装的运行时
根、要么是可重建的索引缓存。**丢了它没有任何地方能恢复**——`storage_uri` 仍为
null，凭据只在 `../../../receipts/` 记了哈希，哈希证明得了丢的是什么，找不回来。

上游 RoboTwin 自带的资产**不在这里**，它们留在 `external/RoboTwin/assets/objects/`
原地，由 s9 的影子根软链引用，账本另存 `../upstream_ledgers/`。入库的判据是
**「从外部采购进来的」，不是「被用到了」**。

## 布局：按来源分一级

```
asset_library/
├── nvidia/      41  ← NVIDIA Isaac Assets 5.1（含 ycb_axis_aligned 那批）
├── objaverse/   21  ← Objaverse（检索 tier3）
├── github/       4  ← Khronos glTF-Sample-Models 等 github tree（tier4）
└── _source/     92  ← 各采购组的源镜像 + SHA-256 清单（按 group 名，不按来源）
```

每个资产夹 `<号>_<类目>/`：

| 内容 | 说明 | 进 git |
|---|---|---|
| `ledger.json` | `asset_ledger.v3` 账本——身份、语义、几何、许可、实测、验证记录 | ✅ |
| `visual/base<N>.glb` | 视觉网格（每 model 一个） | ❌ |
| `collision/base<N>.glb` | 碰撞网格（`collision: coacd` 时是凸分解结果） | ❌ |
| `model_data<N>.json` | RoboTwin 读的几何元数据（center/extents/scale/transform） | ❌ |
| `snapshots/m<N>_*.png` | 入库时渲的复核快照 | ❌ |
| `ledger.lock` | 写账本的 fcntl 锁，运行时产物 | ❌ |

**只有账本进 git**，靠 `.gitignore` 第 83–93 行的例外链放行。分层后账本深了一级
（`*/*/ledger.json`），那三行例外是**必须的**——少了它 65 份账本会静默掉出版本
控制，而 2026-08-14 那次事故里，在 git 里的账本正是资产池唯一幸存的部分。

## 为什么按「来源」分，而不是类目 / 许可 / usable

**来源是资产唯一永不改变的属性。** 其余候选轴都会变，一变就要搬目录：

| 轴 | 会变吗 | |
|---|---|---|
| 类目 | 会——加别名、同物多类目；Objaverse LVIS 有 1,156 个类目，按它分目录会炸 | ❌ |
| 许可状态 | 会——`unknown` → `declared` 是一次审计的事 | ❌ |
| usable | 会——一次实测校准就能把一批资产盘活 | ❌ |
| **来源** | **不会**——采购那一刻定死 | ✅ |

而且来源顺带是**上游重同步的单位**和**许可族的单位**：将来要发布，按来源筛出可
再分发的那部分，比按目录挑简单；将来要合并上游 RoboTwin 资产，它天然落进一个
`robotwin/` 子夹，「哪些是上游的」自动有答案。

**会变的属性一律不进路径，进索引。** 查资产不要遍历本目录——查
`../scene_gen_ext/asset_catalog.json`（有什么、在哪、能不能用）和各资产的
`ledger.json`（来源、许可、实测、验证）。目录结构只负责存放和搬运，不是查询接口。

## 新资产落哪

`scripts/3_materialize/import_materialize.py` 自动决定，规则和它写进账本
`source.library` 的分支是同一处判断（`_provider_dir()`），两者不可能对不上：

- staging 记录的 `group` 以 `web_` 开头 → 看 `source_provider`（`objaverse` /
  `github_tree`）
- 否则 → `nvidia`

**新增检索层必须在 `_WEB_PROVIDER_DIRS` 里登记**，否则直接报错退出——悄悄落进
`github/` 会让账本声明的来源和它的路径不一致。

已存在的资产**保持原位**（`ledger.asset_dir()` 先查现址），重复引入不会在另一个
来源下分叉出第二个目录。

## 读取约定

代码不要拼 `<library>/<asset>`，用 `lib/ledger.py` 的两个函数：

- `asset_dir(library_dir, asset)` — 按 id 找目录，平铺与分层都认，找不到返回 None
- `iter_assets(library_dir)` — 遍历全部资产目录，按 id 排序，重复 id 直接抛错

两者同时支持平铺布局是有意保留的：迁移期任何一半都能单独回滚。

## 什么时候再往下分

现在 66 个，平铺在三个来源夹里完全健康。参照同类项目：ManiSkill 按「用途 + 来源
数据集」分是因为它同时管场景/机器人/任务/物体四类；Objaverse 到 80 万个物体才按
哈希分片（`glbs/000-000/ … 000-159/`），且那一层纯粹是切分、零语义。

**触发点约 250 个**（`ls` 一屏塞不下、开始靠 `grep` 找东西）。届时在来源夹内再按
号段分片（`nvidia/3xx/`、`nvidia/4xx/`…），不要引入语义轴。迁移用
`work/oneoff/` 里那两个一次性脚本的同款做法：搬目录 + 改账本相对 uri + 同步
`.gitignore` 深度。

## 已知例外

- **`303_boombox` 没有账本**。它的源已不可达，无法补齐 v3 必需字段；`ledger_audit`
  把它记在 `no_ledger` 里作为信息项，不算 violation。归入 `github/` 的依据是采购
  清单里的 group prefix `Models/BoomBox/glTF-Binary`，与三个已确认的 github 资产同形。
- **`320_teddy_bear` 有一条 `file_missing`**：`representations[2].uri` 指向已被清空
  的 `/tmp/teddy_acq2/webcache/...`。路径卫生（2026-08-16）刻意保留 `/tmp` 字符串
  （不含用户名，是诚实的临时运行指针），所以这条要靠重新取源修，不是路径问题。
- **`_source/` 不按来源分**。它按采购 group 名组织（`acq_*` / `ycb_axis_aligned` /
  `web_*`），账本里 144 处 uri 指着它；分层迁移一处未动。

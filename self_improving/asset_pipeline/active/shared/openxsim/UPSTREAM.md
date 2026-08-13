# openxsim — 来历与 vendored 依赖 (UPSTREAM)

## 引擎来历
- **搬入自**：`/home/jingxiang/workspace/openxsim-validation`（lv-5090 上的初步实现）。
- **搬入日期**：2026-08-02。
- **原目录状态**：**非 git 仓库**（无 commit hash 可 pin）；本副本即当时快照。整棵 `source/ configs/ scripts/ tests/ third_party/ deps/` 原样复制，未改内部结构（follow 其自有约定）。
- **验证**：搬入后 `openxsim/tests` 36 passed（env-gen-yuxin，PYTHONPATH 见 `../README.md`）。

## Vendored 依赖（不入 git，需自行填充）
| 路径 | 是什么 | 恢复方式 |
|---|---|---|
| `third_party/MetaSim/` | 多后端仿真框架（RoboVerse MetaSim，含 IsaacSim 工具） | 从原目录 `openxsim-validation/third_party/MetaSim` 拷回；或从 MetaSim 上游获取对应版本 |
| `deps/metasim_core/` | openxsim 依赖的 metasim 核心 | 从原目录 `openxsim-validation/deps/metasim_core` 拷回 |

> 这两处约 5.8M，为保持 repo 精简未入 git（见项目 `.gitignore`）。**本地已存在、可直接跑**；只有 fresh clone 到别处才需按上表恢复。
> 若日后偏好"自包含可 clone 即跑"，把它们改为入 git 即可（体量小）。

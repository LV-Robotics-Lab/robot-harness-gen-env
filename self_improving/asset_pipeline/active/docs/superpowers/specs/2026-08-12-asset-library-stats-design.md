# Pipeline Studio — 资产库统计视图 spec

日期：2026-08-12
前置：web studio v2（feat/web-studio-v2 分支）已上线；本功能在同分支继续。
边界不变：只改 `web/`；pipeline/上游代码只读；`results/` 可写数据（缩略图缓存）。

## 目标

一眼看清资产库家底与开发瓶颈：量级、可用性（和"修什么解锁多少"）、来源构成、
检索梯队、类别深度、标注覆盖；任何统计都能下钻到具体资产（带缩略图）。

## 数据事实（2026-08-12 实测）

- catalog 143 资产 / 131 类别 / 模型变体 1–23 个每资产；available 仅 30。
- 不可用原因：stable_pose 113、scale 32、dimensions_m 32、model_metadata 6、
  supported_loader 2、collision_mesh 1、visual_mesh 1（一个资产可多原因）。
- 来源：RoboTwin 原生 125（asset_path 在 external workspace）vs 引进入库 18（data/asset_library）。
- load_type：rigid 131 / urdf 10 / unsupported 2。
- 标注：materials 15/143、colors 1/143。
- 引进资产自带 `snapshots/m0_default.png`；原生 rigid 可用 sapien 离屏烘焙（已探针验证）；
  urdf/unsupported 无法便宜出图 → 占位图标。
- 检索梯队（providers.json）：tier0 本地 catalog → tier1 NVIDIA（索引 2k + CLIP 语料）→
  tier2 glTF-Sample-Assets → tier3 GitHub discovery。

## 后端（app.py 只增不改）

1. `GET /api/library/stats` — 服务端聚合，按 catalog mtime 缓存：
   ```json
   {
     "generated_at": 1723..., "catalog_mtime": 1723...,
     "kpis": {"assets": 143, "categories": 131, "model_variants": N,
              "available": 30, "imported": 18},
     "retrieval": {"tiers": [{"tier": 0, "name": "robotwin_local", "enabled": true, "scale": "143 资产"}, ...]},
     "availability": {"available": 30, "total": 143,
       "reasons": [{"reason": "stable_pose", "count": 113, "asset_ids": [...]}]},
     "sources": [{"key": "robotwin_native", "label": "RoboTwin 原生", "count": 125, "asset_ids": [...]},
                 {"key": "imported", "label": "缺口引进", "count": 18, "asset_ids": [...]}],
     "load_types": [{"key": "rigid", "count": 131, "asset_ids": [...]}, ...],
     "category_depth": {"buckets": [{"depth": "1", "categories": N1}, {"depth": "2", "categories": N2}, {"depth": "3+", "categories": N3}],
       "singletons": N1, "top": [{"category": "bottle", "count": 3, "asset_ids": [...]}]},
     "annotation": {"total": 143, "materials": 15, "colors": 1, "aliases": N},
     "recent_imports": [{"asset_id": "315_shears", "category": "shears", "mtime": ...}],
     "assets": {"<asset_id>": {"category": "...", "available": true, "load_type": "rigid",
                "models": 4, "thumb": true}}
   }
   ```
   tier1 规模从 index_path JSON 长度与 thumbs 目录计数得出（缓存内）。
2. `GET /api/library/thumb/<asset_id>` — 解析顺序：`results/web_thumbs/<id>.png` →
   `data/asset_library/<id>/snapshots/m0_default.png` → 404（前端画占位图标）。
   asset_id 走 ID_RE 白名单，路径含 resolve 防穿越。
3. 烘焙工具 `web/tools/bake_thumbs.py`（web 层代码，随分支提交）：
   遍历 catalog 中 rigid+glb 资产的 model 0，trimesh 算 bbox → sapien 离屏 look-at
   渲 384×384 PNG 到 `results/web_thumbs/`；幂等（已存在跳过）；单资产失败仅告警不中断。
   手动运行一次（~131 张、约 2–4 分钟 GPU），后续引进资产自带 snapshots 无需再烘。

## 前端（index.html 内新增视图）

- header 加视图切换「运行 │ 资产库」，hash 路由（#library），刷新保持视图；
  首页空态加一张资产库入口卡（显示 3 个 KPI 缩影）。
- 资产库页自上而下：
  1. **KPI 行**：资产总数 / 类别数 / 模型变体 / 场景可用（绿，附占比）/ 引进入库。
  2. **可用性 & 解锁清单**：占比大条（30/143）+ 不可用原因横向条形图（按数量降序，
     注"补齐解锁 N 个"）；点任一条 → 下钻弹层。
  3. **来源与检索梯队**：原生/引进构成条 + tier0→3 四级卡片（复用 stepper 视觉语言，
     名称/enabled/规模）；来源条可下钻。
  4. **类别深度**：1/2/3+ 直方图 + 单点风险数 + top 类别 chips（可下钻）。
  5. **标注覆盖**：materials/colors/aliases 三根覆盖率条。
  6. **最近引进**：时间倒序列表（缩略图+asset_id+类别+相对时间）。
- **下钻弹层**：缩略图网格（thumb 132px + asset_id + 类别 + available/load_type 徽章），
  lazy 加载，无图走占位 SVG 图标；复用现有 modal/lightbox 机制。
- 图表全部手写 SVG/CSS，零依赖；沿用双主题 token；实现前先过 dataviz skill 校准
  图表形式与配色（该 skill 对 stat tile/条形图/占比条有硬性规范）。
- 运行页不受影响；资产库数据进入页面时拉一次，不轮询（提供手动刷新按钮）。

## 不做（YAGNI）

- URDF/unsupported 资产不渲染缩略图（占位图标 + load_type 徽章）。
- 不做资产搜索/编辑/删除；不做多 model 变体逐个出图（只烘 model 0）；
- 不做趋势历史曲线（引进时间线已覆盖"增长"叙事）。

## 测试

- pytest：stats 聚合正确性（fixture 小 catalog：available/原因/来源/深度桶）、
  thumb 端点解析顺序与 404、路径穿越拒绝。
- bake 工具：真实 catalog 跑一遍，抽查 6 张图（不同 size 量级）。
- 浏览器：四组截图（light/dark × 1440/900）+ 下钻弹层 + 空 thumbs 目录降级占位。

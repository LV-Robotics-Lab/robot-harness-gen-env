# 守夜报告 — 资产复用冒烟测试（2026-08-02，新加坡时间）

## 一句话结论

**冒烟全绿：RoboTwin 的瓶子（刚体）和抽屉柜（关节体）已成功转换为 Isaac Sim 可用的 USD，
在 SAPIEN 和 Isaac 双后端都通过静置物理验证，并注册进 openxsim 的 AssetBundle（Transfer
侧的 "no existing USD representation" blocker 确认消除）。** 证据与产物在
`~/yuxin/env-gen-dev/results/_test/20260802_smoke_bottle_cabinet_glb2usd/`。

## 运行画像

- **被盯脚本**：`/home/jingxiang/yuxin/env-gen-dev/work/asset_spike/run_smoke.sh`
  （7 步：bundles → 瓶子转换 → 柜子转换 → SAPIEN 验证 → Isaac 验证 → IR 检查 → 内容判定）
- **数据量**：2 个资产（001_bottle 视觉+碰撞 GLB 对；036_cabinet/46653 mobility.urdf，94 个几何名）
- **关键参数**：Isaac Sim 5.1.0.0（新装，conda env `isaac-smoke`，py3.11）；SAPIEN 3.0.0b1
  （现有 `env-gen-yuxin`）；质量默认 0.1kg（结构化未知）；urdf importer fix_base=T /
  convex_decomp=T / distance_scale=1.0；静置 300/120 步 @ 1/100s
- **阶段 × 耗时**：
  | 阶段 | 起 → 止 (SGT) | 耗时 |
  |---|---|---|
  | Isaac Sim pip 安装（后台） | 17:20 → 18:08 | ~48 分钟（16G 磁盘，45G→29G） |
  | SAPIEN 侧脚本先行开发+验证 | 安装并行窗口内 | 0 额外等待 |
  | Isaac 首启验证（着色器编译） | 18:09 → 18:10 | 55 秒 |
  | 转换/验证脚本调试（6 个修复） | 18:10 → 18:25 | ~15 分钟 |
  | 最终端到端全链路 | 18:25:22 → 18:25:51 | **29 秒** |
- 最慢环节是 pip 下载安装（网络带宽决定）；调通后整条链路 29 秒可复跑。

## 事件记录（出了什么问题 → 怎么处理 → 对结果的影响）

1. **转换器单位墙（本次最大发现）**：asset_converter 产出的 USD 是"厘米制图层"（
   metersPerUnit=0.01、几何放大 100 倍），而 USD 的 reference 机制不做单位换算 →
   组合出的瓶子是 25 米巨物、缓慢沉入地面。→ 装配改为**自校准**：实测转换产物包围盒，
   按"目标米制尺寸 ÷ 实测尺寸"算缩放，不硬编码任何转换器约定。→ 已回灌 s1 脚本
   （试验夹内 canonical），批量阶段对任意转换器输出都稳健。
2. **尺寸的权威数据源不是 model_data 的 extents**：001_bottle 的 extents 注释与网格实测
   包围盒比例对不上（初次校准做出 25cm vs 9.5cm 的分歧）。实测确认 GLB 场景图带均匀
   变换，**权威尺寸 = trimesh scene.bounds × scale**（与 SAPIEN 加载行为一致，extents
   注释偏差 2%）。→ bundles 增加 `mesh_bbox_m` 实测字段，三个脚本统一以它为准。
3. **PartNet URDF 重名几何**：mobility.urdf 里 visual/collision 重名（如
   `vertical_side_panel-32` 出现两次），Isaac 导入器报 "Used null prim" 崩溃。→ 生成
   规范化副本（94 个名字唯一化 + mesh 路径绝对化），**上游文件零改动**，副本哈希入账。
   → 这就是批量关节体管线必备的"URDF 规范化器"雏形。
4. **两引擎接触参数差异**：同样从 5cm 跌落，SAPIEN 站稳、Isaac 翻倒（89°）。→ 静置测
   试本义是"能否站稳"，两边统一改贴地生成（5mm）；跌落鲁棒性留给晋升版做参数化对照。
   → 这是 Transfer 阶段"迁移损失量化"要正面处理的真实差异样本。
5. **Kit 吞退出码**：Isaac 脚本失败时进程仍退出 0，run_smoke 曾误报 SMOKE PASS。→ 新增
   s6 按**产物内容**判定（读双侧验证 JSON + bundle 注册）。→ 教训回灌：以后所有接 Isaac
   的自动化，判定一律以产物为准、不信退出码。
6. **小修**：numpy bool JSON 序列化（s3/s4）；瓶子网格原点在瓶底（穿模判据 0.005→-0.002）。
   均已回灌试验夹脚本。

**遗留化妆问题**：Isaac 截图取景未对准物体（相机朝向约定还没调对）；物理判定不受影响，
晋升版修。SAPIEN 侧截图正常（瓶子直立带纹理、柜子落地）。

## 交付物

- 冒烟产物：`results/_test/20260802_smoke_bottle_cabinet_glb2usd/`（bundle JSON×2、
  bottle.usd、cabinet.usd、双侧验证 JSON、截图×4、规范化 URDF）
- 试验夹脚本：`work/asset_spike/`（README + 8 个脚本，绿灯后待晋升）
- 新环境：conda `isaac-smoke`（Isaac Sim 5.1.0.0，16G，磁盘剩 29G）
- 监督日志：`work/nightwatch/20260802/log.md`
- **资产复用初步执行方案**：`work/nightwatch/20260802/asset_reuse_plan_draft.md`（另附）

## 待拍板

1. **晋升 + git**：spike 脚本参数化后移入 `1_asset_reuse/`、feature 分支 commit+push——
   按约定等你 ok（我会先出晋升方案）。
2. **质量默认值策略**：统一 0.1kg vs 按类别默认表（瓶/柜/锅…）——批量前定。
3. **关节体米制策略**：distance_scale=1.0 假设待定（PartNet 归一化单位，柜子现约 1m 高，
   合理但未经真值核对）。
4. **批量范围**：先跑 catalog 里 available=18 的子集、还是全 134 物体。
5. 安装 Isaac 后磁盘剩 29G——**共享盘 97%→98%**，批量转换前建议群里同步一声。

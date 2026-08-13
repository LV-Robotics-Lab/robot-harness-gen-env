2026-08-02 17:51:35 SGT
# nightwatch 2026-08-02 — Isaac minimal install + asset smoke

## 17:53:01 检查点1开始
- 磁盘基线: 45G free (97%)
- 版本决策: isaacsim 5.1.0.0 (cp311, 支持Blackwell; 弃6.0.x太新cp312风险不明)
- 假设记录: 用户未答预计时长->按安装1-2h+冒烟1h; 修复授权->仅机械/infra
## 18:02:10 步骤1+4
- bundles PASS(bottle 2reps/cabinet 1rep); s3首跑2个bug已修: numpy bool序列化 + 瓶子网格原点在瓶底(z判据0.005->-0.002)
## 18:11:49 检查点1通过(isaac 5.1无头启动55s, 双扩展加载, 装后剩29G)
## 18:27:42 冒烟全绿(按内容判定)
- 修复时间线: s1双xformOp冲突->包一层geom; s2 PartNet重名visual致Used null prim->规范化副本(94名); s1单位墙(converter产出cm层100x, reference不换算)->自校准缩放; 尺寸权威=glb scene.bounds x scale(model_data extents偏差2%); s4跌落翻倒->贴地生成对等测试; Kit吞退出码->s6按产物内容判定
- 上游参数核对: asset_converter默认上下文; urdf importer fix_base=T convex_decomp=T self_collision=F distance_scale=1.0(米制策略待定); sapien fix_root_link=T
- 终态: VERDICT PASS x6; 磁盘剩29G; isaac相机取景未对准(物理为准,待晋升修)

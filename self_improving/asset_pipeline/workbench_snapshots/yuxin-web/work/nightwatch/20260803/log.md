# nightwatch 2026-08-03 — B/C线打样
## 15:34:47 B+C 全绿
- s7: 反向导出三格式OK; NVIDIA服务器无6个缺货类别实体->选YCB 025_mug(红)走cup类别+颜色限定词剧本
- 修复链: up-axis启发式失灵->源USD元数据驱动; 原点规范化到底部中心; 影子根漏普通文件(same.json); 缺stable_orientation_wxyz致躺着生成->overrides补X+90
- 负样本: mug放盘子被求解器正确拒绝(footprint 11.7>10cm), 留档
- 终态: S10 PASS(grounding选中301_cup, 回放fail=0, 全量fail=0 not_run=0); catalog 131条/19可用; 磁盘剩29G
## 16:21:18 晋升+批量完成
- 批量: 25模型(YCB21+SM4) 17过/8淘汰(banana原点判据保守; scissors/clamp/SM mugs ~90度=源约定问题, 全部记录并隔离出资产池)
- 修复: 电钻原生崩溃->driver/worker进程隔离; 淘汰资产泄漏进catalog->物理隔离
- catalog: 139条 available 18->27(9个外部资产); e2e红杯回归PASS
- git: feature/asset-reuse-abc-batch 已推送(3af5403); 注意: 并行会话(Opus4.8)在main上提交了2_sim_migration桥接文档并顺带提交了1_asset_reuse脚本(ec9fd2e), 内容一致无冲突, 但需用户知晓双会话并行写同一仓库
## 16:45:15 s11运行时扫描 + git碰撞事故
- s11: 17/17外部模型经RoboTwin create_actor真实路径加载+静置全过(runtime_sweep.json)
- 事故: 并行会话切到feat/env-gen-ir-bridge, 我的s11提交(446e716)落其分支; 修正时对方又提交(709603d)被我误软回退; 已用reflog完整恢复709603d, 零丢失; s11提交留在其分支历史中(内容无害), 我的远端分支仍在3af5403
- 决定: 本会话停止一切git操作直至用户协调双会话分工
## 16:57:04 屏蔽RoboTwin对照e2e
- 外部专用catalog(14条): can/box/bowl 全链路选中外部资产+回放+全量验证 PASS; cup此前已过 => 4类别全绿
- bottle: 选中304_bottle但settle门边缘失败(位移抖动1.0mm, 支撑接触100%, 旋转0.5度) => 根因整瓶凸分解接触微抖, 精化项=CoACD碰撞体
- block: 上游对派生几何代理+3加分(106>103)抢走选择, 设计行为非缺陷(306_block经s11可加载)
## 17:22:50 900_*代理残留治理
- s9加过滤跳过5个900_*残留; catalog 139->134/available 22, 代理条目0; block屏蔽e2e改选306_block并全过 => 屏蔽测试5/6类别绿(余bottle CoACD)
- 提交: f6a5b22(s11+s12) + 新commit(s9过滤), 均经worktree隔离, 未触碰并行会话工作区
## 17:50:07 关节类反向导入(s13)全绿
- USD articulation->URDF: Sektion柜(9链节/4关节) 导出+SAPIEN验证(dof/限位扫掠)+0.35定尺+关节dynamics+is_static惯例 => 屏蔽e2e全过fail=0
- 修复链: 全尺寸柜放不下桌面(定尺0.35入账) -> 门自荡-90度(补dynamics) -> is_static=true对齐RoboTwin家具惯例
- catalog 135条/available 23; SimReady结论: 公共桶无单一大包, NVIDIA/Assets子树按需镜像
## 18:28:36 防复发改造落地
- 8文件改造+回归全绿: 批量17/25集合不变(box/block被策略缩放并入账, 303_box继承is_static=true); s13新门柜子过(收敛+无自由关节); s9准入7 admitted/3 skipped/0拒; e2e三连PASS
- 插曲: import sys被as _sys子串骗过->修复; fragment重生成吞掉314条目->补回(已知问题:s13流的fragment条目应由s13自管, 待办)
## 18:31:26 s11扩展至URDF路径: 18/18过(含314_cabinet经create_sapien_urdf_obj)
## 18:59:06 P1+P2 落地
- CoACD(5件)+顶点采样穿模+静置定姿: 18/25(banana转正); bottle屏蔽e2e FAIL->PASS(靶心); e2e四连全绿; catalog 136/available 24
- 定姿对滚动件(SM杯/剪刀/夹钳)追不上滚动平衡->unstable_rest诚实淘汰, P3逐源覆盖
- 调试插曲: AABB盒角判穿模冤枉香蕉(-12.7mm假穿模)->改真实顶点采样
## 19:45:37 汇报artifact终版
- 证据补拍: 瓶子Isaac正面(根因=相机默认近裁剪~1m裁掉一切近物); 柜子正面抽屉驱动开合对比(全关节驱动, qpos 0->0.66跟随, joint_2被抽屉遮挡读0如实入档)
- 重大更正: cabinet.usd无defaultPrim致此前Isaac引用为空场景(8/2的sim-stable为虚证)->s2结构性修复+文件补设, 现在的驱动证据为实证
- artifact精简版发布(只讲成果/通俗化), URL b247b3c4

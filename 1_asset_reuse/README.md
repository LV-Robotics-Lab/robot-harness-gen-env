# asset_spike — 资产复用打样（试验夹，不入 git）

RoboTwin 资产 → Isaac Sim 可用 USD 的冒烟验证：1 刚体（001_bottle）+ 1 关节体
（036_cabinet/46653）走通「读取 → 转换 → 双后端静置验证 → IR 注册」全流程。
绿灯后脚本参数化并晋升到 `1_asset_reuse/`（另出方案）。

## 用法

```bash
bash run_smoke.sh    # 11 步全流程；产物落 results/_test/2026080{2,3}_*/
```

前置：conda env `env-gen-yuxin`（SAPIEN 侧）+ `isaac-smoke`（Isaac Sim 5.1.0.0, py3.11）。

## 步骤

| 步骤 | 脚本 | 环境 | 作用 |
|---|---|---|---|
| 1 | `robotwin_asset.py` | env-gen-yuxin | RoboTwin 私有目录 → AssetBundle JSON（sapien 侧表示 + sha256 + 结构化未知） |
| 2 | `s1_convert_rigid.py` | isaac-smoke | 瓶子 GLB→USD（asset_converter）+ 物理装配（碰撞 schema、烘焙 scale 0.05 与 Y-up→Z-up） |
| 3 | `s2_convert_articulated.py` | isaac-smoke | 柜子 URDF→USD（URDF importer）+ 关节数/限位核对 |
| 4 | `s3_validate_sapien.py` | env-gen-yuxin | 原始资产在 SAPIEN 静置验证 + 截图 |
| 5 | `s4_validate_isaac.py` | isaac-smoke | 转换 USD 在 Isaac 静置验证 + 截图 |
| 6 | `s5_check_ir.py` | env-gen-yuxin | openxsim AssetBundle 校验 + `representation_for("isaacsim")` 命中 |

## 绿灯标准

转换无 blocker；双后端静置位移 < 2mm、无穿透、直立（倾斜 < 15°）、截图非空；
柜子 USD 关节数 = URDF 可动关节数且限位保留；哈希链完整；IR 查询命中。

## B/C 线（外部资产引入 + 全流程复用，2026-08-03 全绿）

- **s7**（一次性探测）：asset_converter 反向导出 GLB/GLTF/OBJ 均可；NVIDIA 服务器选品 YCB 025_mug。
- **s8a/s8b**：拉取源 USD+贴图（哈希入账）→ 反向转 GLB → 规范化（upAxis 元数据驱动旋转、
  原点=底部中心）→ 物化 `data/asset_library/301_cup/` → SAPIEN 静置 PASS → 账本双表示。
- **s9**：影子根 `data/robotwin_shadow/`（symlink 镜像+注入）→ 上游扫描器出扩展 catalog
  （131 条/available 19）。overrides 必须含 `stable_orientation_wxyz`（Y-up 网格 X+90），缺则资产躺着生成。
- **s10**："Place a red mug on the table." → grounding 选中 301_cup（红色限定词 108>103 分）
  → 回放+全量验证 `fail=0 not_run=0`。
- 另有负样本证据：mug 放盘子上被求解器正确拒绝（footprint 11.7cm > 盘子稳定面 10cm），
  见 `results/_test/20260803_smoke_usd2envgen/scenes/` 的 failure_report。

## 已知假设（结构化未知，非隐瞒）

- 质量/惯量：RoboTwin 无此数据 → 记 unknown + 运行默认 0.1kg（记录在 bundle）。
- 柜子米制尺寸：PartNet-Mobility 归一化单位 → distance_scale=1.0 假设，定尺策略待定。
- 许可证：RoboTwin 逐物体来源未公布 → 记 unknown，批量前需补查。

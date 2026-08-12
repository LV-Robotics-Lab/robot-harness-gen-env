# RoboLab 资产迁移到 RoboTwin

本项目使用 `script/migrate_robolab_asset.py`，将 RoboLab 中的单网格
USD 刚体资产转换为 RoboTwin 可以加载的格式。

## 迁移内容

- 应用 USD 中的坐标变换
- 将模型底部对齐到 `z=0`
- 将纹理嵌入 `visual/base0.glb`
- 生成凸包碰撞体 `collision/base0.glb`
- 保存质量、静摩擦、动摩擦和恢复系数
- 保存来源 commit、许可证和迁移限制

## 安装迁移环境

```bash
conda create -n robolab-migrate python=3.11

conda run -n robolab-migrate \
  python -m pip install -r requirements-robolab-migrate.txt
```

## 剪刀迁移示例

```bash
conda run -n robolab-migrate \
  python script/migrate_robolab_asset.py \
  --source-repo /path/to/RoboLab \
  --usd assets/objects/ycb/scissors.usd \
  --texture assets/objects/ycb/textures/obj_000017.png \
  --mesh-prim /scissors/obj_000017_Mesh \
  --asset-id 905_robolab_scissors \
  --asset-name "YCB scissors" \
  --license assets/objects/ycb/LICENSE \
  --license-label MIT \
  --out-root /path/to/migrated-assets \
  --mass-kg 0.2 \
  --static-friction 2.0 \
  --dynamic-friction 2.0 \
  --restitution 0.1
```

## 已验证资产

| ID | 资产 | 数据集 | 许可证 | RoboTwin |
| --- | --- | --- | --- | --- |
| 901 | corn can | HOPE | CC BY-NC-SA 4.0 | PASS |
| 902 | milk carton | HOPE | CC BY-NC-SA 4.0 | PASS |
| 903 | measuring cups | HANDAL | CC BY-NC-SA 4.0 | PASS |
| 904 | cordless drill | YCB | MIT | PASS |
| 905 | scissors | YCB | MIT | PASS |
| 906 | mug | YCB | MIT | PASS |
| 907 | mustard bottle | YCB | MIT | PASS |

## 当前限制

- 只支持单网格、三角面、带逐顶点法线和 UV 的刚体资产
- 暂不支持关节资产和多网格资产
- 碰撞体使用单个凸包，孔洞可能在碰撞层中被填充
- 转换后仍需登记到 asset overrides 并运行 RoboTwin 验证

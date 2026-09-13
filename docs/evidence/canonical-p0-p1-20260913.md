# Canonical P0/P1 真实接入记录（2026-09-13）

本记录对应本轮新的部署与案例，不修改 matrix v2 四例或旧资格。当前为执行中的记录，
尚未宣称 web/reconstruction 完整 E2E 通过。执行计划见
[P0/P1](../../self_improving/golden_e2e_progress/CANONICAL_P0_P1_PLAN_20260913.md)。

## 配置与来源

- 用户入口：`/home/jingxiang/bingsheng/runtime/harness/USAGE.md`，同目录 deployment.json、state/、outputs/。
- P0 check提交a0fc1e0；web准备源轴076c801；数值mesh证据遍历5e7116d。
  固定运行源码先source-p1@076c801，后source-p1-mesh@5e7116d。结果按各次实际身份解读。
- Yuxin唯一引擎，GitHub provider固定KhronosGroup/glTF-Sample-Assets@90d7ede14c7e280af263824604b427a1ca02cb66；
  配置SHA53e35cf5127029ac463ba0394b24c617c7a707da74e68e8e9d11ebf64dc5fe65，许可证据按每资产读取。
- 重建固定外部ed3b7e2882b2f1eec0ead916d0377776aff93f7f，clean。SAM2/TRELLIS/DINO/FlexiCubes原归属保留；
  完整配置、模型pin与运行依赖说明在 `/home/jingxiang/bingsheng/runtime/reconstruction/README.md`。
- 输入媒体及派生CC-BY-4.0署名x2env1.0来自用户既有授权。canonical输入授权与原图SHA关联见
  `runtime/harness/p1-evidence/derivation-authorization.json`，不覆盖其他媒体或第三方资产许可。

## 全部尝试

原始命令、stdout、stderr、process、measurement和逐阶段summary统一在
`/home/jingxiang/bingsheng/runtime/harness/p1-evidence/<case>/`。
每次正常预算1170秒加30秒退出；未超时的失败也原样保留。

- web-01：cf91adcc-9290-49e0-a5c0-528a651659fc，113.252692秒，受阻。
  默认local未命中，真实联网搜索下载成功；两候选GLB被模型错误填源Z轴，准备拒绝。
  父最后报告source_adapter_not_connected（当时重建未配置）。
- web-02：b98d917a-945f-4ab0-82ba-e4ec647f86e2，144.246394秒，受阻。
  源轴修正真实生效，Box规范化/登记/Genesis预览；另一候选材质不支持。
  Box被视觉box/cube语义拒绝，最后text-only回退missing_reconstruction_image。
- web-duck-01：eb1ccdb4-62fb-4d31-b9c7-be548269547e，50.985014秒，受阻。
  黄色橡皮鸭检索后blocked_license；text-only重建回退缺图。
- web-cube-01：ffceeab5-5210-4f38-a79d-b537e1ce60ec，117.362010秒，受阻。
  搜索命中AnimatedColorsCube/AnimatedMorphCube/CubeVisibility，动画或GLB扩展不在当前规范化范围。
- reconstruction-01：5005d918-718f-49a3-b3eb-5ebccf9e0eee，259.105236秒，受阻。
  Codex解析89.46秒；实际建议SAM2点框、分割/TRELLIS新几何49.306176秒；规范化/登记/Genesis预览通过。
  因44MB preview receipt的数值mesh数组触发200k JSON遍历预算而停于视觉判断之前。
- reconstruction-02：f19597ef-d51a-445e-9e4f-a673fbc0c6c2，252.450571秒，受阻。
  新几何/规范化/预览/闭包通过，视觉识别computer mouse但白灰色，与pink意图不符。
- web-solid-box-01：28dffeba-3657-4815-8b3c-617efec8ec7f，60.441891秒，受阻。
- web-block-01：311cf5a3-b931-4be3-8918-85268540b661，65.751284秒，受阻。
  上两例均为模型将实心/无开口编码成specified articulation和空joint list，触发clarification。
  18ce9af提示明确刚体null；重建规划新增SceneIR绑定颜色估计并交normalize_mesh。
- web-solid-box-02：39901da2-dfac-4a4c-8b34-19553c51d46c，109.796818秒，受阻。
  刚体null修复生效；search query=cube仍仅命中三个动画/扩展候选，未进入Genesis场景。
- reconstruction-03：ad84b57d-4df1-4a52-917c-f6e856463a7b，299.796203秒，受阻。
  新几何生成48.414030秒，颜色规范化实际应用RGBA [0.91,0.68,0.72,1]，Genesis预览已产生。
  preview receipt闭包超过256MiB，视觉判断尚未执行；不能称颜色修复已被视觉验收。
  原回执纯读2.546秒复现。loaded.json/child-result.json各约51MB，result.json因缩进约128.55MB，
  preview receipt又内嵌51MB运行结果；正在修复证据存储重复，不提高字节预算。

## 检查与限制

- root第二次核验2411 passed/1 skipped，281.08秒；后续颜色改动有单独53项定向通过，
  不把多个时间点测量合并成同源冻结资格。日志 `/var/tmp/x2env-p1-20260913/`。
- lint及本地文档链接/12份归档字节检查通过，远端文档HTTP未运行。
- Dashboard三入口404，不能同步task状态。本机进度为golden_e2e_progress账本。
- 没有机器人策略/采集、任意关节、纹理恢复或正式资格；本轮失败没有从统计分母移除。

## 重建首例部分产物

原始根：
`/home/jingxiang/bingsheng/runtime/harness/state/attempts/5005d918-718f-49a3-b3eb-5ebccf9e0eee/88415f5d-db0c-4b72-bbe8-268a27a5673e/`。

- 新GLB：`reconstruction/mouse-generation/reconstruction/geometry.glb`，8,840,584 bytes，
  SHA9a7bf1b6036780a330638963bfcbe7cdb11f6342ab2d9e2ddef383e13f412f28。
- 分割RGBA：`reconstruction/mouse-generation/segmentation/rgba.png`；分割点/框由真实Codex产生，未手填。
- Genesis图片：`previews/cd706b7f-b896-4800-b6b5-adb4a92b8718/runtime/frames/step-0025.png`。
- 连续视频：`previews/cd706b7f-b896-4800-b6b5-adb4a92b8718/runtime/preview.mp4`。
- 新资产版本b79dec72ab1432b4de91c1c5a72a945d9f89685938cd6b8180392331a60b3066；
  归一化geometry b5a54b0e3b16dbadff77c0640d1ae3378b5a299ec3aea8f01da3b682a00f1958。
  运行前库中不存在的核对见 `p1-evidence/reconstruction-new-versions.json`。

预算修复后原preview receipt纯读重验26引用、3.063秒通过，原workflow保持blocked。
公共failure导出在 `outputs/p1-reconstruction-01-failure-export/`；原source router在closure失败前
未纳入该来源result，因此这份失败包不包含全部新几何/预览，以上原attempt路径仍保有产物。
这是失败证据聚合缺口，不能宣称失败包已经完整。此处媒体只证明资产预览，不证明场景物理profile。

# P1 接续：证据存储、检索修订、结构颜色

这是新的执行记录，不改写[上一批失败](canonical-p0-p1-20260913.md)或matrix v2四例。
当前P1未完成；web和重建完整工作流不因组件成功获得通过。

## 独立功能提交与契约

- `d4c6a5e`：预览receipt用ArtifactRef引用完整运行结果，runtime/portable launcher取消JSON缩进。
  不删拓扑、物理字段或原始输出；不提高256MiB预算。
  主要代码：`self_improving/harness/x2env/{asset_preview,genesis_runtime,package_loader}.py`。
  公开preview/run_scene反例先RED；102项通过。旧现场派生比较5.013秒、26引用164,125,679bytes，
  运行result语义相等，receipt 51,026,578→5,691bytes。旧CAS与终态不变。
- `e288c0b`：web resolver一次失败回执绑定的Codex检索修订，共享时限，仅未解决实体。
  原category不变、无固定同义词、无资产URL映射；重复词/候选停止，资源/许可失败不触发。
  主要代码：`self_improving/harness/x2env/{web_resolver,search_advisory,deployment}.py`。
  119项定向通过；其中真实Store/Registry与外部模型/HTTP替身不当真实联网成功。
- `6310f22`：SceneEntity可选surface_rgba只供结构支撑；保留color及provenance，模型估计不作纹理恢复。
  旧缺字段序列化不变，foreground不能覆盖资产材质。主要代码：contracts.py/compile.py/codex.py，
  JSON schema由唯一typed定义导出。颜色反例先RED；270项邻组131.09秒通过，web/search另62项通过。

契约与测试seam：[SEAMS](../../self_improving/golden_e2e_progress/SEAMS.md)、
[API](../../repo-docs/canonical-x2env-api.md)、[来源台账](../integration-provenance/LEDGER.md)。

## reconstruction-04（真实推进但失败）

- 固定d4c6a5e，workflow `15dc259c-b085-4ff8-8596-8553b88b1c36`。
- 同一已授权原图＋“只重建图片中的鼠标，忽略笔和其他背景物体，将鼠标放在桌面上。”，seed67，source reconstruction。
- 总259.017125秒。ingest0.292892、interpret62.865123、asset.resolve182.093707、ground10.684430秒成功；
  compile0.026453秒失败，`unsupported_structural_color`，table原色名light brown不能由旧Pillow规则解析。
- 新几何/颜色处理/真实Genesis资产预览/视觉候选门成功；没有完整场景replay、validate或可运行环境包。
- 命令/summary/log：`/home/jingxiang/bingsheng/runtime/harness/p1-evidence/reconstruction-04/`。
- 失败包：`/home/jingxiang/bingsheng/runtime/harness/outputs/p1-reconstruction-04/failure/`，
  manifest.json枚举资产证据、图片、视频和日志；原始geometry仍在attempt中。
- 产物根：`/home/jingxiang/bingsheng/runtime/harness/state/attempts/15dc259c-b085-4ff8-8596-8553b88b1c36/ba5c2792-0d63-48fb-819c-5137c4ec992e/`。
  新几何`reconstruction/mouse-generation/reconstruction/geometry.glb`；
  图片`previews/e30113c9-730d-4df9-a6a1-c36f2a32288d/runtime/frames/step-0025.png`；
  连续预览`previews/e30113c9-730d-4df9-a6a1-c36f2a32288d/runtime/preview.mp4`。
- ffprobe独立检查：640×480、10fps、6帧、0.6秒；保存PNG六帧均不同，只有资产预览scope。
  MP4 SHA256 `73c2ead8469871ee4d6eff3c1d080a192382ba4fdfc8a00466868d4f49c42b6d`；
  机读核验p1-evidence/reconstruction-04/media-inventory.json。
- summary SHA256 `f4b84fa1a26e6f1964797e8cf0e30b82d3f09812ef09369fe612cdff8253a4c1`；
  media inventory SHA256 `263b6261b47f65cfd9e40f456b4ff2358c6c6ce8dd8588a40cf815d818f7a62a`。

## web-solid-box-03（真实执行一次检索修订，仍失败）

- 固定e288c0b，workflow `916be468-cccf-4f16-bdcb-6bc97c09afa5`，78.564775秒blocked_license。
- 输入“在桌面上放一个红色实心方盒模型，不需要开口或内腔。”，source web，seed83。
- interpret53.340338秒成功；asset.resolve24.842415秒受阻；首次red cube因动画候选耗尽，
  模型真实修订为red block，第二次检索命中BoxTextured/BoxTexturedNonPowerOfTwo/CompareDispersion，
  许可证据unknown而停止。query_revisions=1，两次回执保留；不再重试许可失败。
- 本次证明真实失败驱动查询修订已执行，不证明资产获得或完整E2E成功。
- 日志：`/home/jingxiang/bingsheng/runtime/harness/p1-evidence/web-solid-box-03/`；
  失败包`/home/jingxiang/bingsheng/runtime/harness/outputs/p1-web-solid-box-03/failure/`。
- 两次检索回执：`/home/jingxiang/bingsheng/runtime/harness/state/attempts/916be468-cccf-4f16-bdcb-6bc97c09afa5/73f0900c-59cc-4225-bec3-df4521ee2f39/web/`内
  receipt.json、query-revision/receipt.json、query-revisions.json。
- 原模型修订理由：“Uses a different ordinary term for the same red solid box while avoiding the failed
  cube-named animated candidates.” 位于同operation的search-advisory/e1b12040-b63a-4352-b409-0475d5aa7ab8/proposal.json。
- summary SHA256 `aa088d72f679cad5a0aa24902092fa16408f85f5073463cf1217cbfccdcbc6ed`。

## 当前验证与后续

- root-compact：2417 passed/1 skipped，283.92秒；测试启动后有web改动，不称同源冻结资格。
- lint、schema、本地链接通过；远端文档HTTP not_run，dashboard三入口404，无task同步。
- reconstruction-05固定6310f22，workflow `0a481af7-2859-4545-9a1d-19a352bb92d4`，
  266.781519秒blocked / visual_assessment_failed。ingest0.282022、interpret98.606018秒成功；
  asset.resolve165.283745秒受阻。模型已保留table色名light-brown wood-grain与RGBA[0.55,0.44,0.27,1]，
  但前景要求light pink、视觉报告pink，被属性匹配器否决；没有到compile，不能称结构颜色真实场景修复通过。
- 新输出/日志分别在`/home/jingxiang/bingsheng/runtime/harness/outputs/p1-reconstruction-05/`
  与`/home/jingxiang/bingsheng/runtime/harness/p1-evidence/reconstruction-05/`；summary、media-inventory、
  geometry-and-verdict分别给阶段、媒体、几何和拒绝详情。原GLB8,596,904bytes，
  SHA63d64fd2a94851940ef6b2efd423ab300d968c4a65cdc31d9872a982c491d97c。
- 不可变资产4ebbfc1f8ec67d02b5b34dc8dcef02bc44f969198e9d763fdefce2f8d21c216b，归一化几何
  beb4714cee64802f55ea2286546479afa40d0b16de8d5a7b23ffda19b4b1994c不在生成前快照中。
  快照是在获取阶段已开始、geometry文件尚未产生时保存，不冒称提交前快照；首轮过早阶段断言失败未写文件。
- 最新预览图片/MP4根：`/home/jingxiang/bingsheng/runtime/harness/state/attempts/0a481af7-2859-4545-9a1d-19a352bb92d4/68ef920f-d28a-4e7a-827f-e09380354737/previews/0be0f453-a935-4eb6-81e4-9521b59f99e8/runtime/`，
  文件frames/step-0025.png、preview.mp4。640×480、10fps、6帧/6不同PNG、0.6秒。
  MP4 SHA43b4f58e1b2f575ddff184431038a9b5e9983efde93e08c3df4352527c141ebb；仅资产预览。
- 固定6310f22 clean源码根测试2433 passed/1 skipped，286.27秒，日志
  `/var/tmp/x2env-p1-20260913/root-surface.log`及root-surface.xml。全部本轮核验小于30分钟，无超时案例。
- 尚未完成：web完整线、真实成功查询修订、local miss→web、新重建场景包与copy-run；
  来源closure异常时的失败包完整聚合、机器人策略/数据采集、P2复杂关系、正式资格均未授予。

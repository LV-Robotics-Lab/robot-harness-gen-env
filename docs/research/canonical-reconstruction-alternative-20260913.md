# C07 重建替代后端：官方来源核验与隔离部署准备

- 检查日期：2026-09-13（Asia/Singapore）。本报告是研究/准备记录，不是重建或 Genesis 验收。
- 用户后续批准：优先替代后端，保留 Gujie 原 Hunyuan 接口为非默认；不修改 dirty Gujie，不接受新受限模型条款。
- 首选：TRELLIS 1 的真实 image→mesh＋vertex-color 输出。第二候选 Direct3D-S2；TRELLIS.2/Pixal3D 不优先。
- 不把模型 MIT 自动写成生成资产 CC0；输入授权、输出 provenance 和发布许可仍由 AssetRegistry 边界分别确认。

## 1. 固定来源和可获取性

GitHub commit API 与 Hugging Face model API 在本次联网核查返回 HTTP 200；仅读取源码、许可、配置和下载元数据，没有使用账号或读取凭据。

| 后端 | 代码 pin | 模型 pin | 官方获取状态 |
|---|---|---|---|
| TRELLIS 1 | `microsoft/TRELLIS@442aa1e1afb9014e80681d3bf604e8d728a86ee7` | `microsoft/TRELLIS-image-large@25e0d31ffbebe4b5a97464dd851910efc3002d96` | 公开、非 gated |
| TRELLIS.2 | `microsoft/TRELLIS.2@75fbf0183001ed9876c8dbb35de6b68552ee08bd` | `microsoft/TRELLIS.2-4B@af44b45f2e35a493886929c6d786e563ec68364d` | 主权重公开；另有 gated/custom 依赖 |
| Direct3D-S2 | `DreamTechAI/Direct3D-S2@a1cf235b2881cff04a91900060a9546b40e7ee5d` | `wushuang98/Direct3D-S2@8b04a8eddb7a56a0f4e89fe5f5b840c7d5610c00`，v1.1 子目录 | 公开、非 gated |
| Pixal3D | `TencentARC/Pixal3D@f7cf38429b0bd264f1995f0f8743a88b1c728b94` | `TencentARC/Pixal3D@b0cb2e1b794cab9aa0ac38a95d794a4d9337437f` | 主权重公开；默认依赖另有 gated/custom 模型 |

直接来源：[TRELLIS code](https://github.com/microsoft/TRELLIS/tree/442aa1e1afb9014e80681d3bf604e8d728a86ee7)、[TRELLIS model](https://huggingface.co/microsoft/TRELLIS-image-large/tree/25e0d31ffbebe4b5a97464dd851910efc3002d96)、[Direct3D code](https://github.com/DreamTechAI/Direct3D-S2/tree/a1cf235b2881cff04a91900060a9546b40e7ee5d)、[Direct3D metadata](https://huggingface.co/api/models/wushuang98/Direct3D-S2?blobs=true)、[Pixal metadata](https://huggingface.co/api/models/TencentARC/Pixal3D?blobs=true)。可移动 API 仅为检查方法，执行必须使用表中 pin。

## 2. 许可分层，不能只看顶层 MIT

| 后端 | 代码/权重 | 必须区分的依赖 | 输出许可结论 |
|---|---|---|---|
| TRELLIS 1 | 官方 code MIT，HF model MIT | DINOv2 Apache-2.0；FlexiCubes pinned fork Apache-2.0；spconv Apache-2.0。Gaussian/rasterizer 分支含 INRIA non-commercial 代码，不能因不渲染就假设不导入 | 已读 MIT 声明未增加 Hunyuan 式输出用途/地域限制；仍不自动授予输入内容权利或保证所有生成几何无第三方权利 |
| Direct3D-S2 | `LICENSE.txt` MIT，HF card MIT | DINOv2、TorchSparse、Triton、FlashAttention、voxelize；RGBA 可跳过 BiRefNet。原安装是非锁定依赖，须独立锁定 | 未发现额外输出限制；只有 geometry，没有纹理/物理证据 |
| Pixal3D | code/model MIT，但 NOTICE 明确保留第三方条款 | 默认 DINOv3 custom/gated、RMBG-2.0；MoGe/NAF/NATTEN 与 TRELLIS.2 栈。Gujie 固定 SimFoundry adapter 已禁 RMBG，要求真实 alpha | 不能仅以主模型 MIT 清除上游依赖条款；非首个部署候选 |
| 原 Hunyuan | 自定义 Community License | 现存权重不代表新增使用/发布授权 | 保留非默认方法，不将其输出改标 CC0。固定许可 §5(b)(c) 包含输出用途及地域限制 |

许可原文：[TRELLIS LICENSE](https://github.com/microsoft/TRELLIS/blob/442aa1e1afb9014e80681d3bf604e8d728a86ee7/LICENSE)、[FlexiCubes LICENSE](https://github.com/MaxtirError/FlexiCubes/blob/815e075a2a400d06c48d94c347674344ed6ae5c5/LICENSE.txt)、[Direct3D LICENSE.txt](https://github.com/DreamTechAI/Direct3D-S2/blob/a1cf235b2881cff04a91900060a9546b40e7ee5d/LICENSE.txt)、[Pixal NOTICE](https://github.com/TencentARC/Pixal3D/blob/f7cf38429b0bd264f1995f0f8743a88b1c728b94/NOTICE)。这里记录固定文本与工程准入，不替代法律意见。

## 3. 下载体积与磁盘预算

以下为官方远端元数据的文件字节数，不是本机实际下载量。Python wheels、CUDA扩展构建缓存及日志不计入模型数；因此不能把模型总量宣传成整个部署的确定总量。

- TRELLIS 1：stock 六个权重 3,006,922,800 bytes；配置＋pipeline 合计 3,006,927,470 bytes。mesh-only 四权重 2,664,021,360 bytes，四配置 1,444 bytes，原 pipeline 1,987 bytes，共 **2,664,024,791 bytes**；删除 Gaussian/RF descriptor 后须记录新配置 digest。
- DINOv2 `dinov2_vitl14_reg4_pretrain.pth`：官方下载元数据 **1,217,607,321 bytes**；不能复用当前 Gujie 的 DINOv2 giant 权重冒充 vit-large-register。TRELLIS 最小模型集合约 **3,881,632,112 bytes**，另加许可证/源码；实际 transferred bytes 待下载记录。
- Direct3D-S2：整个 HF repo 8,514,526,984 bytes；仅 v1.1 全部文件 **4,233,925,997 bytes**，仍另需 DINOv2。其 loader 即便选择 512 也读取 1024 和 refiner 权重，不按单输出分辨率虚减下载量。
- Pixal3D：当前整库 **46,045,208,259 bytes**；排除多视图 `_mv` 文件 **24,044,888,779 bytes**，仍另加 DINOv3、MoGe、NAF等，不是完整部署总量。
- TRELLIS.2：stock checkpoints **14,819,870,530 bytes**；加TRELLIS1 sparse decoder 147,591,972、DINOv3 1,212,559,808、RMBG2 884,878,856，共 **17,064,901,166 bytes**，不含小配置/环境。其loader eager初始化rembg，透明输入本身不能免除门控下载。[固定pipeline](https://huggingface.co/microsoft/TRELLIS.2-4B/blob/af44b45f2e35a493886929c6d786e563ec68364d/pipeline.json)、[官方loader](https://github.com/microsoft/TRELLIS.2/blob/75fbf0183001ed9876c8dbb35de6b68552ee08bd/trellis2/pipelines/trellis2_image_to_3d.py)。
- 本机检查时根盘仅约 **50 GiB available**。首选方案预留 20 GiB，不复制现有 Torch/CUDA 或 8.5 GB Genesis runtime；空间门不满足即保留现场并缩小非必需下载，而非删除用户数据。

## 4. RTX 5090 的实际工程边界

现有只读环境 `/home/jingxiang/gujie/gen-env/.cache/simfoundry/miniforge/envs/simfoundry` 有 Python 3.11、Torch `2.7.0+cu128`、torchvision `0.22.0+cu128`、xformers `0.0.30`；不是当前任务重新验证的完整 runtime。

TRELLIS 原 `setup.sh` 的 Torch/CUDA枚举较旧，不能原样执行其全安装器：它会安装不需要的 renderer。dense attention 可用 SDPA，但 sparse attention 只允许 xformers/flash-attn，故单设 `ATTN_BACKEND=sdpa` 不足。建议实际小算子 probe 先于模型下载：dense SDPA、sparse xformers，再验证 spconv 的 5090 支持。xformers 0.0.30 有 cu128 轮子不证明所有 Blackwell kernel 可用。

Kaolin 0.18 的 [固定Apache-2.0许可](https://github.com/NVIDIAGameWorks/kaolin/blob/v0.18.0/LICENSE)与[Torch2.7.0/cu128 官方wheel索引](https://nvidia-kaolin.s3.us-east-2.amazonaws.com/torch-2.7.0_cu128.html)均已核查，不安装PyPI同名占位包。FlexiCubes当前仅导入`kaolin.utils.testing.check_tensor`。

Direct3D 官方要求 Torch2.5.1/cu121、Triton3.1、TorchSparse/FlashAttention；32GB超过其说明的1024约24GB需求，但这些版本不构成5090支持证据。Pixal官方支持 `--low_vram`、SDPA，仍依赖更复杂的TRELLIS.2栈，首轮不选。

源码：[TRELLIS setup](https://github.com/microsoft/TRELLIS/blob/442aa1e1afb9014e80681d3bf604e8d728a86ee7/setup.sh)、[sparse attention selection](https://github.com/microsoft/TRELLIS/blob/442aa1e1afb9014e80681d3bf604e8d728a86ee7/trellis/modules/sparse/__init__.py)、[Direct3D requirements](https://github.com/DreamTechAI/Direct3D-S2/blob/a1cf235b2881cff04a91900060a9546b40e7ee5d/requirements.txt)、[Pixal README](https://github.com/TencentARC/Pixal3D/blob/f7cf38429b0bd264f1995f0f8743a88b1c728b94/README.md)。

## 5. 真媒体到新 mesh 的最小 seam

1. Harness Codex 解析目标/排除对象；真实分割产出有来源绑定的 RGBA，不手工预填最终 SceneIR。视频沿用完整解码，只把明确选择的真实帧用于几何重建。
2. pinned TRELLIS `TrellisImageTo3DPipeline.run(image, seed=..., formats=['mesh'])` 生成 mesh；实际模型推理不可用 primitive 或旧 mouse bytes 替代。
3. 从 `MeshExtractResult.vertices/faces/vertex_attrs[:,:3]` 用 trimesh 导出 vertex-color GLB；保留颜色但不声称 PBR texture 等价。
4. 显式避开 `render_utils/postprocessing_utils` 和当前 eager `representations/__init__.py` 的 Gaussian导入。独立外部patch只使mesh路径不导入受限分支；不删除旧Hunyuan方法。
5. 交 Gujie 单刚体normalize/import算法，新增mesh/collision/URDF/physics来源；Harness再compile/replay/validate。重建smoke只授予“新geometry生成”，不授予sim-ready。

官方seam：[pipeline.run](https://github.com/microsoft/TRELLIS/blob/442aa1e1afb9014e80681d3bf604e8d728a86ee7/trellis/pipelines/trellis_image_to_3d.py)、[mesh结果](https://github.com/microsoft/TRELLIS/blob/442aa1e1afb9014e80681d3bf604e8d728a86ee7/trellis/representations/mesh/cube2mesh.py)、[eager imports](https://github.com/microsoft/TRELLIS/blob/442aa1e1afb9014e80681d3bf604e8d728a86ee7/trellis/representations/__init__.py)。

## 6. 已开始的隔离准备与下一门

相关的受控分割替代准备见文末SAM2附节；这不是新增VLM，也不是M04通过证据。

隔离根：`/home/jingxiang/bingsheng/canonical-trellis1-20260913.1U7bmo`。

- `bounded_command.py` 为每组记录command/stdout/stderr/exit/wall，1770秒SIGINT＋30秒尾部预算；超时不立即重跑。
- `TRELLIS/` 是新的clone，固定到上述SHA；不使用Gujie dirty SimFoundry做运行来源。
- `venv/` 由现有Python创建，`--system-site-packages`只读复用依赖；所有新安装定向新venv，绝不写旧env。新source/patch/模型均另存此根。
- clone、checkout、venv、依赖安装和固定模型下载已完成；真实全图geometry smoke已通过，尚不授予对象隔离、Genesis E2E或sim-ready。
- 用户批准后开始两组有界安装：`easydict==1.13 spconv-cu126==2.3.8`；官方`kaolin==0.18.0`匹配wheel（`--no-deps`）；固定FlexiCubes submodule checkout另记日志。安装完成与否以该目录`deps-basic.json/deps-kaolin.json/flexicubes.json`为准，启动不等于成功。
- 后续每次实际操作将把独立命令回执补到本节，失败保留。Gujie/SimFoundry reconstruction-only API须在各自clean外部worktree独立提交；本仓仅thin adapter，不复制vendor。

### 6.1 实际依赖结果与外部小提交

`probe-deps.json` 保留首次失败：native CUDA SDPA及spconv SubMConv3d实际通过；xformers因现有FlashAttention 2.8.3超出其要求2.7.1–2.7.4而失败；Kaolin缺pygltflib。未关闭任何xformers版本检查。新venv补`pygltflib==1.16.5`，选择独立native SDPA backend，DINO使用官方`XFORMERS_DISABLED=1`选择非xformers路径。

独立TRELLIS分支`codex/canonical-mesh-sdpa-20260913`，基于上述Microsoft固定源：

- `de530d4`：mesh-only lazy imports与native SDPA full/cross/window attention；真实CUDA数值参考与batch/window隔离测试，6项通过。serialized模式明确unsupported，因固定源缺vox2seq源码；选用mesh decoder的`swin`不走此分支。保留原Gaussian及其他backends。
- `750ccb6`：公共pipeline接受本地加载的固定DINO module，避免运行中拉取mutable hub默认分支；新增缺离线seam的RED，再7项GREEN。DINO源码固定`7764ea0f912e53c92e82eb78a2a1631e92725fc8`。

可审计日志均在隔离根：`red-sdpa.json`、`red-window.json`、`red-mesh-import.json`、`tests-final.json`、`red-local-encoder.json`、`green-local-encoder.json`。最近7项测试实际wall为10.005秒，不是模型推理时间。

`models-download.json`：35.935秒，固定4个mesh必要模型＋本地DINO权重已下载，逐文件大小和SHA在`models/download-manifest.json`；HF权重与公开publisher LFS SHA逐一核对，DINO官方直链只有本地实算SHA，不假称publisher校验。没有下载Gaussian/radiance模型或门控RMBG权重。

### 6.2 原图全图 smoke 的授权边界

唯一`geometry-smoke-1`使用原始`/home/jingxiang/gujie/鼠标和笔.jpg`、seed109、`preprocess_image=False`、固定模型默认25＋25步；没有手工mask、隐藏面或旧资产替换。源图含遮挡鼠标的笔，故`pen_exclusion=not_run`、`M04_qualification=not_run`，生成网格仅证明媒体→新geometry。物理、Genesis和sim-ready均未执行。

实际exit0，外层command wall **45.45335595193319秒**，脚本加载/推理/导出wall **43.81874982290901秒**，峰值CUDA allocation **10,729,634,816 bytes**。输入SHA`044e68562224788ad4e395d9c7cae49c148eb509a15f9ff11eb4aa39bc467d48`；新目录before mesh list为空，输出GLB **401,650 vertices / 803,288 faces / 16,066,872 bytes**，SHA`fca9941c20458f79e17afbff0a8e2f950d81c6bce93f161d5f6d38fe781d8d1d`。检查非空/finite vertices实际通过，但尚未做形状质量、人审、网格拓扑或接触诊断。

证据入口（均为隔离根相对路径）：`geometry-smoke-1.json`（准确命令与wall），`geometry-smoke-1.stdout.log`、`geometry-smoke-1.stderr.log`（含两段真实采样进度与Kaolin可选ipyevents/pxr导入警告），`geometry-smoke-1/result.json`，`geometry-smoke-1/generated-full-image.glb`；operator源码`geometry_smoke.py`。外部TRELLIS运行时clean HEAD为`750ccb6e7138b7592804148fa21d35961f51e036`。此smoke无额外重试；此前依赖失败保留不覆盖。

## 7. SAM2 受控分割工具的公开替代准备

本节为独立readiness研究与下载准备，不授予实际分割、笔排除或M04资格。
SAM3代码/权重是专门SAM License，本轮不接受新条款、不运行它。改用SAM2.1 tiny：
官方[源码与权重许可说明](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/README.md#license)
明确为Apache-2.0；已完整读取[LICENSE](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/LICENSE)
与可选connected-component的[BSD-3-Clause LICENSE_cctorch](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/LICENSE_cctorch)。
Demo字体OFL与数据集许可证不能混入模型许可；当前不下载demo/训练数据。
SAM1官方[完整LICENSE](https://github.com/facebookresearch/segment-anything/blob/main/LICENSE)亦为Apache-2.0，
可作为后备；本次没有获取SAM1权重。

SAM2代码新独立clone到隔离根`SAM2/`，固定`2b90b9f5ceec907a1c18123530e92e794ad901a4`。
旧Any6D里的`sam2/`属于vendored目录，其父repo有dirty文件，不将其算作独立clean来源。
公开HF模型`facebook/sam2.1-hiera-tiny@de431c4043854a71d8101e17995dfe596bf101a5`，
API确认`gated=false`；`sam2.1_hiera_tiny.pt` **156,008,466 bytes**，publisher SHA
`7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69`。
已真实下载到`models/`并核对大小/SHA，回执`sam2-weight.json`；下载wall **2.606376096秒**，
clone **5.049969280秒**、pin **0.116848667秒**，各组均低于30分钟。
来源：[固定model card](https://huggingface.co/facebook/sam2.1-hiera-tiny/blob/de431c4043854a71d8101e17995dfe596bf101a5/README.md)。

无需改旧env或升级Torch：现有隔离venv可见Torch2.7.0+cu128、torchvision0.22.0+cu128、
hydra-core1.3.6、iopath0.1.10、numpy1.26.4、tqdm4.70.0、Pillow12.3.0，满足
[固定setup要求](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/setup.py)。
直接固定source path导入，不安装包；可选CUDA connected-component扩展未构建。
未来如安装使用官方`SAM2_BUILD_CUDA=0`，必须明确后处理not_run；不能把缺核警告当后处理通过。

公开seam是`build_sam2('configs/sam2.1/sam2.1_hiera_t.yaml', checkpoint)`加
`SAM2ImagePredictor.set_image(original_RGB)`、`predict(point_coords, point_labels, box)`。
点是原图pixel XY，label1前景/0背景，box是pixel XYXY；`normalize_coords=True`由模型内部转换；
输出CxHxW原分辨率mask和模型分数，非文本理解接口。
来源：[固定predict契约](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/sam2/sam2_image_predictor.py)。

Harness中的managed Codex需提供有图像SHA/尺寸绑定的box与正负点，工具校验范围、数量、来源与预算后
调用模型；工具不能手工填mask。建议保留所有候选mask/scores、实际选择规则、原图RGB及alpha生成记录，
禁止形态学补洞假充观察到的隐藏表面。透明RGB到TRELLIS消费已随后由下节独立实现并做算子验证，
旧reconstruction-only全图RGB路径本身仍不能声称已支持masked reconstruction。

## 8. 独立 Gujie seam 与算子验证（非 M04 资格）

独立worktree在隔离根`gujie-reconstruction/`，源自固定Gujie `eb0b710`，没有修改原dirty目录。
按功能提交：`13ddc000766bbe6b4f9b44cc5a376501933a46e7` reconstruction-only；
`a3db4fb`受控SAM2；`ed3b7e2882b2f1eec0ead916d0377776aff93f7f`显式alpha消费。
公开接口与测试在`self_improving/sim_adapters/simfoundry/`近码合同；快速测试共11项通过。
旧Hunyuan/SimFoundry原接口字节未改，新默认模型是固定TRELLIS。

- `reconstruction-seam-real-1`：新公共wrapper实际生成，command wall50.326017982秒，exit0。
  result SHA`c5fde3d8f231c5083ff79bac84dfaee8fc8aa0ef1081a57bc7e9e9f5ad01aba7`。
- `segmentation-operator-probe-1`：真实原图全尺寸框（1280×1707），origin明确`operator_probe`，
  command wall3.911406710秒；模型实际3候选，原尺寸RGBA的RGB与原图逐byte相等。
  result SHA`3c5621e8875503d4b070f9ab45acb728faea53a40c72e853ef6fc318010401fc`；
  RGBA SHA`c59a08da3fc4406e467ad7114a0d6e41aa7d06648cfb6e74b81836adb21efb50`。
  模型最高分不是分割精度；这个全图框不是managed Codex建议，不能作为M04笔排除证据。
- `masked-reconstruction-operator-1`：上述模型RGBA经显式black composite、bbox squarecrop/pad，
  真实TRELLIS新网格command wall47.153765556秒，exit0；205,153顶点/410,068三角面/8,204,276B。
  GLB SHA`2acc0ba9aa1c9311f7ba06bca56bc494957a6dab86f72fa10c83cc963b58d5e7`，
  result SHA`c3b8aeda41073e0b083f6320eafc393fb283f5a044a9f6aa8385cc55bfa024ec`。
  `model_input.png`和原RGBA分开保留，mapping明确预处理图不与原像素完全相同。

每组均保留同名command回执、stdout/stderr及output/result.json；无超时重试。
物理质量0.08kg/摩擦0.4仅operator代表Harness显式注入，basis=`harness_supplied_not_measured`。
所有这些结果的normalization/physical profile/pen exclusion/sim-ready/M04资格仍未授予。

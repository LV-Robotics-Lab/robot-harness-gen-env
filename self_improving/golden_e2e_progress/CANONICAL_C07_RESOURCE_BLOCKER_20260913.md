# C07 前置：固定重建链的许可与资源阻断

## 后续批准与替代结果（优先于下列历史阻断）

用户已批准替代后端并保留原Gujie接口非默认，继续计划。TRELLIS1在独立固定源码/模型环境
首次原图→新geometry已真实通过；主会话复核result和GLB摘要。仍未授予对象排除、M04、
Genesis或sim-ready。详见[完整研究与实测](../../docs/research/canonical-reconstruction-alternative-20260913.md)。
下面的“未安装/未加载/暂停”仅为批准前现场，不能用作当前停止指令。

- 日期：2026-09-13；状态：`受阻`。
- 范围：只读固定源码、许可文件和本地资源元数据；未安装、未加载模型、未运行重建、未读取凭据值。
- 决策依据：已批准计划 §5.2、C07、§17.3。需要新增外部依赖或许可决定时暂停请求用户方向；
  不降低 Gujie 真实新 geometry / Genesis / qualification / push 门。

## 固定来源与主窗口复核

| 来源 | commit | 检查点 |
| --- | --- | --- |
| Gujie `/home/jingxiang/gujie/gen-env` | `eb0b710581fd7794bc01b447b0f77cb871c8a711` | `self_improving/sim_adapters/genesis/import_simfoundry_assets.py:23–149`、`reconstruct_media.py` |
| SimFoundry | `9e34ebefcd020583fbb755a8b57268dce78eca26` | `simfoundry/models/mesh_generator.py:257–395`、stage 11、第三方许可 |
| Genesis gitlink | `0e74bf392781884ccad765c3f344419c86b872ca` | 仅固定来源，不是本轮执行通过 |

主窗口通过 `git show <fixed-sha>:<path>` 复核以下事实；没有读取 dirty 文件冒充 fixed source：

1. `Hunyuan.generate_shape(image_path, mask, generator, ...)` 与 `generate_texture(...)` 有可提取的
   几何/纹理 seam，因而不是“必须调用旧完整 workflow 才能重建”。
2. `scripts/pipeline/A_reconstruction/stages/11_make_objects_sim_ready.py:193–245` 的
   `import_rigid_scene_object()` 仍调用 Gemini 得到 mass/friction，然后转换 mesh。
   需在外部固定提交中拆出接收已校验参数的转换，而非伪造 Gemini 返回值。
3. Gujie `import_assets()` 能消费 s11 资产并规范化引用/惯量，但将 mass/friction source 固定写成
   `SimFoundry VLM estimate`；使用 Codex 后必须修正来源记录，不能沿用错误归属。
4. SimFoundry `THIRD_PARTY_LICENSES.md` 明确其 Apache-2.0 不涵盖第三方模型/材料。
   `third_party_notices/Hunyuan3D-2.1-LICENSE.txt` 固定 SHA-256 为
   `b79ac5e11ce063b6c6570dbe9686a45a03ba08bd248aa6aa82fb342a23a81c0c`。
   该文件是自定义 Community License，§5(b)/(c) 对 Output 的其他 AI 模型用途、使用/分发地域有约束。

这不是对所有生成内容法律归属的结论，也不能把“模型代码许可”直接写成“输出资产许可”。当前缺少
将这条链用于本项目预定环境输出、后续机器人用途和 evidence prerelease 的明确许可/部署依据。
不能自行把输出登记为 CC0/CC-BY；也不能因为 SimFoundry 本身 Apache-2.0 就忽略第三方条款。
计划 §5.1 的联网资产许可白名单继续有效；本次并未擅自把它改写成所有算法源码只能 CC0/CC-BY。

## 实际资源观察（不是加载验证）

子 agent 只读发现，未全量 hash 或初始化模型：

- RTX 5090、约 32 GiB 显存；存在 simfoundry/hunyuan/da3/any6d 四个解释器环境及 Torch 安装元数据。
- SAM3 snapshot `3c879f39826c281e95690f02c7821c4de09afae7` 的 3,450,062,241-byte 权重文件存在。
- DA3 snapshot `b2359bdf726fb44ef62acca04d629dcf158053e7` 的 6,759,558,100-byte 权重文件存在。
- Hunyuan snapshot `0b94677654c57bb9a6b6845cd7b704ccf551d327` 下发现四个权重文件。
- 检查过的本地 deps/cache 未发现 Pixal3D、TRELLIS、TRELLIS.2、Direct3D-S2 所需完整安装/权重；
  这是限定目录检查，不声称整个主机或其他服务绝无这些资源。
- Gujie 与 SimFoundry 工作树仍 dirty；主窗口 `--porcelain -uall` 明确看到 stage 5、`vlm.py` 等
  修改和嵌套未跟踪运行记录。后续必须用 clean committed checkout，不能 import 这些 editable bytes。

因此既不能笼统写“没有 GPU/权重”，也不能写“资源已验证可运行”。替换后端需新的依赖、权重、
全部必要第三方许可核查和独立真实 smoke；现有计划未授权绕过该前置直接下载或接受新条款。

## 需要用户的方向

保持所有验收门不变，先决定：提供/确认现有后端适用于预定用途的授权依据，或授权替代后端的许可与
资源选型。若选择后者，先列明具体版本、完整依赖/权重、下载体积、许可与预期 seam，再决定安装。
不得用旧 mouse 包、primitive、人工 SceneIR 或 adapter 单测抵消 C07 真实重建硬门。

当前安全停止在 C00 完成、C01 冻结/保护部分完成；C02–C15 未实施，没有 qualification 或 push。

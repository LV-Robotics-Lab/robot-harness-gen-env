# VLA / WAM 到底“懂不懂物理”？可提取性与复现优先级

> 调研日期：2026-08-24。范围仅限论文、官方项目页与官方代码仓库。本文把作者展示的实验事实与我们的工程推断分开；“zero-shot”沿用各论文自己的定义，并不等于对任意对象、任意物理参数的零样本标定。

## 一句话结论

VLA 能在真实机器人上抓起新物体，说明它学到了**足以闭环控制的任务相关规律**，却不能据此断言它恢复了对象的质量、摩擦、质心或刚度。主流 VLA 输出的是末端位姿增量、关节位置或夹爪命令，通常**不直接输出力/力矩，也没有以 SI 单位监督物理参数**。它可能用视觉先验、示教中的动作—结果关联、夹爪与底层控制器的容错完成任务，内部表征还可能把质量、摩擦、几何和控制延迟纠缠在一起。

这并非说这些模型“没有物理”。更准确的说法是：它们通常具有**隐式、任务充分、未标定、可能不可辨识**的物理表征。若目标是生成可进仿真器的资产，最值得做的不是直接截取一个 VLA backbone 宣称它能预测质量，而是组合三条路线：

1. 用视频/世界模型表征做视觉和动态先验；
2. 通过有信息量的交互与可微仿真，把隐变量标定为带单位、带不确定度的参数；
3. 只在下游任务确实需要时重建相关参数，并用反事实回放而非渲染相似度验收。

## 1. 这些模型实际输出什么？

| 模型 | 论文中的输入 | 论文/代码中的动作输出 | 直接输出力或力矩？ | 对“隐式物理”的实证边界 |
| --- | --- | --- | --- | --- |
| **RT-2** | 单张相机图像 + 语言 | 6-DoF 末端位置/旋转增量、夹爪开度、终止位；连续维各离散为 256 桶 | **否** | 展示了新物体、关系与语义组合上的真实机器人泛化；作者也明确说物理技能仍受机器人数据技能分布限制。[论文 §3.2](https://arxiv.org/abs/2307.15818) |
| **OpenVLA** | 单张图像 + 语言 | 7 维控制动作；训练数据被筛为单臂末端控制，连续维映射到 256 个离散 token | **否** | 证明开源 VLM 可被训练成闭环动作策略；没有质量/摩擦读出或系统辨识实验。[论文 §3.2–3.3](https://arxiv.org/abs/2406.09246)、[官方代码](https://github.com/openvla/openvla) |
| **Octo** | RGB 历史 + 语言或目标图，可扩展本体感觉 | 小型 diffusion head 输出连续动作 chunk；预训练筛选 delta 末端控制，夹爪对齐为开/关；下游可换成关节位置 | **否**；力/力矩在论文里是可新增的**观察输入** | 展示跨机器人预训练与约 100 条示教快速微调；官方论文没有参数标定头。[论文 §III](https://arxiv.org/abs/2405.12213)、[官方代码](https://github.com/octo-models/octo) |
| **π0** | 2–3 路 RGB + 语言 + 关节角 | flow matching action expert 输出高频动作 chunk（论文 `H=50`）；不同 embodiment 是 7–17 维配置/动作向量，主要是臂、夹爪和移动底盘的配置命令 | **没有作为策略原生输出报告** | 能做接触丰富的真实任务，但动作专家从示教动作监督学习；并未展示可解释的质量/摩擦读出。[论文 §IV–V](https://arxiv.org/abs/2410.24164)、[官方代码](https://github.com/Physical-Intelligence/openpi) |
| **GR00T N1** | 图像 + 语言 + 本体状态 | embodiment-specific 编解码器 + DiT/flow matching 输出 16 步动作 chunk；任务中用相对末端位姿+夹爪或关节位置/旋转 | **否** | 混合人类视频、生成视频、仿真和机器人示教；人类视频先提取 latent action。论文未证明 latent action 等于质量/摩擦等物理参数。[论文 §2、§4](https://arxiv.org/abs/2503.14734)、[官方代码](https://github.com/NVIDIA/Isaac-GR00T) |
| **Diffusion Policy** | RGB/本体状态历史 | 连续动作序列；真实 UR5/Franka 实验主要预测期望末端位姿与夹爪宽度，再由中层运动学/位置控制器转关节目标 | **否**；底层控制器可以使用力矩约束或 torque control，但这不是学习策略的参数输出 | 表明视觉行为克隆可以完成液体、柔性体等任务；动作示教和中层控制器吸收了大量动力学细节。[论文 §7、附录 D](https://arxiv.org/abs/2303.04137)、[官方代码](https://github.com/real-stanford/diffusion_policy) |
| **DreamZero（WAM）** | 视觉上下文 + 语言 + 本体状态 | 一个 DiT 联合生成未来视频与连续动作；DROID/Franka 公开分支默认用**相对关节位置** | **否** | 证明“预测视觉未来 + 动作”能比若干 VLA 更好地泛化；论文把视频称为物理先验，但没有从隐变量回归带单位物理参数。[论文 §3–6](https://arxiv.org/abs/2602.15922)、[官方代码](https://github.com/dreamzero0/dreamzero) |

### 关键澄清：动作命令不等于实际接触力

末端位姿或关节位置命令最终经 IK、PD、阻抗控制、安全限幅、夹爪固件以及机械顺应性变成电机电流和接触力。同一个高层动作，在不同增益、控制频率、夹具和物体上会产生不同的力。RT-2/OpenVLA 的低频末端动作、π0/GR00T 的动作 chunk、Diffusion Policy 的期望位姿都不能单独反推出“模型想施加 7 N”。因此“VLA 输出具体角度和力”这个前提对上述主流模型只对了一半：它们常输出位置/姿态/关节目标，**很少直接输出牛顿或牛顿米**。

## 2. 为什么只看图片也能在仿真或真实世界行动？

这里容易混淆四件事。

1. **训练标签并不只是图片。** VLA 的输入可以是 RGB，但训练样本仍含机器人动作，很多还含本体状态；仿真数据在生成时也由完整物理状态和控制器产生。策略学的是 `图像/状态/指令 → 示教动作`，而不是从一张图显式求解牛顿方程。
2. **行为克隆可把动力学“摊还”进权重。** 若训练分布里的杯子重量和摩擦变化有限，网络可直接学一个平均有效抓法，无需输出参数。高摩擦夹爪、保守夹紧、慢速运动和反馈重规划扩大了容错区间。
3. **闭环纠错代替一次性精确参数。** 每执行一小段动作就看新图，偏差会体现在对象是否移动、是否滑落。策略可以纠正结果，却未必知道偏差是质量、摩擦、质心还是控制延迟造成。
4. **仿真随机化训练的是鲁棒性或在线适应，不必是参数恢复。** RMA 是很清楚的反例：训练时环境因子包含质量、质心、摩擦和电机强度，部署时 adaptation module 从状态—动作历史预测低维 `extrinsics`，策略据此行动；作者明确说不需要完美系统辨识，只要得到“正确动作”即可。[RMA 论文](https://arxiv.org/abs/2107.04034)

所以“能做任务”至少有三种解释：显式辨识参数、隐式估计任务相关动态、或根本不辨识而靠鲁棒策略覆盖。仅凭成功率无法区分三者。

## 3. 能否证明内部真的含有物理属性？

### 已证实的事实

- **自然视频预训练能提供对控制有用的动态表征。** V-JEPA 2 先用无动作互联网视频训练，再用少于 62 小时 DROID 机器人视频训练 action-conditioned predictor；V-JEPA 2-AC 用 7 维末端增量在新实验室里做基于图像目标的 MPC 抓取与放置。[论文 §3](https://arxiv.org/abs/2506.09985)、[官方代码](https://github.com/facebookresearch/vjepa2)
- **latent world model 可在动作空间搜索，而不解码完整像素。** DINO-WM 用冻结 DINOv2 patch features 学 action-conditioned dynamics 并做 zero-shot planning，在 PushT、绳子、颗粒等模拟任务展示泛化；论文也承认需要足够的 state-action coverage，且不证明精确物理参数恢复。[论文](https://arxiv.org/abs/2411.04983)
- **视频可以显式辨识部分参数，但需要结构和激励。** `∇Sim` 将可微多物理模拟和可微渲染连接，从视频像素反传到质量、摩擦、弹性等参数；作者同时称问题本质上 ill-posed，并展示模型失配会恶化估计。[论文](https://arxiv.org/abs/2104.02646)
- **多视角视频可联合恢复几何和连续体材料参数。** PAC-NeRF 用 NeRF + MPM 估计 Young 模量、Poisson 比、屈服应力、黏度、体积模量、砂土摩擦角等；真实实验只对四相机同步视频做了定性重建，且要求相机标定、前景分割和已知材料模型族。[论文](https://arxiv.org/abs/2303.05512)、[官方代码](https://github.com/xuan-li/PAC-NeRF)
- **主动交互显著改善可观测性。** Active Sensing Motor Policies 在仿真中因估计奖励学会用脚扫地来暴露摩擦差异，再用真实遍历得到的本体估计作为 RGB 摩擦标签。[论文](https://arxiv.org/abs/2311.01405)
- **仅关节编码器也能做接触后的参数标定。** Chen 等利用可微 robot-object simulation，从单条机器人交互的关节位置轨迹估计物体质量和弹性模量；这是“抓过以后再标定”，不是单图猜参数。[论文](https://arxiv.org/abs/2410.03920)
- **视觉模型的物理能力需要专门反事实测试。** Physion/Physion++ 发现显式物体/物理状态模型通常强于纯视觉基线；Physion++ 专门设置质量、摩擦、弹性、可变形性等潜变量的推断阶段。作者发现按标准训练的多数模型不会自发利用这段属性证据，只有 DPI-Net 在质量情形显示明显改善。[Physion](https://arxiv.org/abs/2106.08261)、[Physion++](https://arxiv.org/abs/2306.15668)

### 仍是推断，尚未由这些论文证明

- RT-2/OpenVLA/Octo/π0/GR00T 的通用 backbone 中存在可线性解码、跨对象与跨仿真器校准的 `mass_kg` 或 `friction_coefficient`。
- 直接冻结一个 VLA visual encoder 并加小头，就会优于专门的物理视频模型或系统辨识方法。
- WAM 生成“看起来物理合理”的未来，就等价于其内部参数与目标仿真器参数一致。不同质量/力同时缩放、不同摩擦/控制增益组合都可能产生近似轨迹；这属于**不可辨识性**。
- 单张静态 RGB 能可靠确定质量、内部质心、静摩擦、关节阻尼或材料刚度。视觉可以给类别先验和范围，但没有运动或交互，许多属性在因果上不可观测。

## 4. 能否把 backbone “抽出来”训练 X2Env？

可以做，但应把目标拆成三层，而不是训练一个万能回归头。

### A. 表征探针：回答“里面有没有信息”

冻结 OpenVLA、V-JEPA 2、DINO-WM、DreamZero 或 3DGSim 的视觉/时序特征，在**严格对象、材质和场景分割**上训练小型 probe，预测：

- 离散属性：刚体/关节体/柔性体、空心/实心、材料族、接触模式；
- 连续但可观测属性：质量、质心、动/静摩擦、恢复系数、关节阻尼、刚度；
- 参数区间和置信度，而不是单个点值。

必须同时放三个 baseline：类别/文本先验、只看静态帧、从轨迹做经典/可微系统辨识。若 probe 不能跨对象和跨渲染域泛化，就只能说特征含数据集捷径。

### B. 交互标定：把隐式表征变成仿真参数

最有希望的结构是：

`多模态先验 → 选择一次安全探测动作 → 观测 RGB/关节/电流/力矩 → 可微或无梯度 simulator fit → 参数 posterior → 反事实验证`

视觉先验缩小搜索空间；主动动作解决不可观测性；simulator fit 保证单位与参数语义；posterior 保存多个等价解。对已有视频，使用已知机器人动作或从轨迹估计的动作；对静态图，输出“先验 + 必须补采的 probe”，不能伪装成测量值。

### C. 策略相关的“最小充分物理”

并非所有任务都需要完整参数。建议用需求门控：

- 静态摆放/避碰：碰撞网格、尺度、稳定支撑面通常比质量精确值重要；
- 抓取与搬运：质量/质心范围、摩擦/抓取顺应性、易碎性重要；
- 推/滑/投掷：摩擦、质量、恢复系数、惯量重要；
- 开门/抽屉：关节轴/限位、阻尼、静摩擦重要；
- 布料/流体：本构模型和时变状态重要，刚体参数表不够。

资产包应同时存 `value/range + units + confidence + observation provenance + identifiable_under + simulator mapping`。参数不确定时让仿真做 ensemble，而不是硬填一个 LLM 数字。

## 5. 最有价值的复现对象（面向 Text/Image/Video2Env）

| 排名 | 复现对象 | 为什么最值得 | 首个可交付实验 | 主要风险 |
| --- | --- | --- | --- | --- |
| **1** | **PAC-NeRF → 视频物理资产 sidecar** | 现成 MIT 代码、数据和参数优化流程；最直接回答“视频能否同时恢复几何与可解释材料参数”，与 Gaussian/NeRF 资产路线互补 | 跑通官方 torus/sand/fluid；导出统一 material sidecar；在目标模拟器做未参与拟合的反事实落下/碰撞回放 | 多视角、标定、前景 mask；连续体为主，刚体接触/关节体覆盖弱；真实结果定量证据有限 |
| **2** | **V-JEPA 2 / DINO-WM 表征 + Physion++ probe** | 官方权重/代码可用，算力与工程风险低于 14B WAM；能科学回答“视频 backbone 是否含可迁移物理信息” | 冻结表征，和 OpenVLA vision encoder、ImageNet/DINOv2、从零模型比较质量/摩擦/弹性 OOD probe；加 counterfactual contact 预测 | probe 成功只证明可读信息，不等于精确参数；必须防视觉捷径 |
| **3** | **Active Sensing + proprioceptive differentiable fit** | 解决静态图不可能辨识的问题；最贴近真实机器人闭环，能生成高置信物理标签来反哺 Image2Env | 桌面物体设计安全 `lift/tilt/push/tap/squeeze` probes；用关节/电流/视频估质量、摩擦、刚度；将结果作为同对象 RGB 的监督 | 机器人安全与传感器标定；接触模型失配；对象/抓具覆盖需逐步扩展 |
| **4** | **3DGSim latent dynamics + 参数读出/scene edit** | 现成 MIT 代码、数据和 checkpoint，把多视角 RGB 变成可编辑 3D Gaussian 粒子动态；非常契合“视觉重建 + 动态资产” | 官方 elastic/cloth demo；冻结 latent 做 material probe；比较显式 3DGS 与 latent；给仿真器导出几何/轨迹/不确定度 | 论文称 latent 可容纳 mass/friction，但没有证明可标定参数；真实世界只是初步，且依赖多视角。[论文](https://arxiv.org/abs/2503.24009)、[官方代码](https://github.com/martius-lab/3DGSim) |
| **5** | **DreamZero / V-JEPA 2-AC 作为“动态判别器”，不是参数 oracle** | 能检验 WAM future 是否对资产候选排序有用；DreamZero/V-JEPA 2-AC 均有官方代码 | 给同一初始帧、动作和多个候选 physics sidecar，比较真实后续与各候选 rollout/latent distance；测试能否选中正确参数桶 | DreamZero 14B 成本高且默认动作是相对关节位置；视觉相似可能偏爱渲染而非动力学，参数仍不可解释 |
| **6** | **RMA 式 privileged-to-history distillation** | 最清晰的“从仿真参数监督出隐式 extrinsics”模板，可作为 VLA 物理适配头的上限实验 | 在 RoboTwin/Isaac 随机化质量/摩擦/质心，训练 history encoder；比较直接参数头、latent extrinsics、无适配策略 | latent 往往故意不可解释；它可提高任务成功，但不一定适合生成可移植资产参数 |

不建议第一阶段把 RT-2、OpenVLA、π0 或 GR00T 当作物理参数 backbone 主线。它们适合做**表征对照组/下游策略消费者**；其中 OpenVLA、Octo、π0、GR00T 均有代码，但论文目标是动作生成，不是参数可辨识性。先花大算力微调它们，很可能得到一个能抓但不能说明为何能抓的系统。

## 6. 建议的证伪式实验

### 实验 1：成功抓取是否意味着参数可读？

固定对象几何和纹理，独立随机化质量、质心、摩擦和底层控制增益。让 VLA 执行抓取；从不同层特征训练线性/小 MLP probe。比较：

- 抓取成功率；
- 参数 OOD 误差与置信区间覆盖率；
- 替换其中一个参数后的反事实轨迹误差；
- probe 是否对控制器变化仍保持校准。

可能出现“成功率高、参数 probe 差”，这会直接证明鲁棒动作不等于参数重建。

### 实验 2：静态先验、被动视频、主动交互各增加多少信息？

同一对象依次提供 `text/image → passive video → known-action video → active probe + proprioception`。对每一阶段输出 posterior，测负对数似然、区间覆盖率和仿真反事实误差。预期是：文本/图像缩小材料类别范围；运动揭示某些参数组合；已知输入和主动激励才让质量/摩擦可辨识。

### 实验 3：物理属性到底需不需要？

为四类任务做消融：只用视觉网格、默认参数、类别先验、标定参数、oracle 参数。以任务成功、接触错误、轨迹差、sim-to-real 排名一致性和校准成本作 Pareto 曲线。若某类任务中默认参数与 oracle 无显著差异，就不要为它重建昂贵属性。

## 7. 对 X2Env 的工程判断

我们需要的不是“每个资产都有一张完整物理身份证”，而是**物理主张有证据等级**：

- `prior`：来自文本/类别/LLM，只能用于检索和初始化；
- `observed`：从被动视频拟合，记录可观测性和多解；
- `probed`：已知动作 + 真实传感回执标定；
- `validated`：未参与拟合的反事实动作在真实/高可信回放中通过；
- `task-qualified`：只对某任务/控制器/仿真器成立。

这也回答“真的需要重建物理属性吗”：**为了视觉复用和粗粒度规划，不一定；为了接触丰富训练、可靠 transfer、可解释失败和物理规律主张，需要重建任务相关属性，或至少保存经过验证的参数分布。** 对无法观测的属性，诚实的不确定度比 LLM 的精确小数更有价值。

## 主要来源索引

- RT-2: https://arxiv.org/abs/2307.15818
- OpenVLA: https://arxiv.org/abs/2406.09246 ; https://github.com/openvla/openvla
- Octo: https://arxiv.org/abs/2405.12213 ; https://github.com/octo-models/octo
- π0: https://arxiv.org/abs/2410.24164 ; https://github.com/Physical-Intelligence/openpi
- GR00T N1: https://arxiv.org/abs/2503.14734 ; https://github.com/NVIDIA/Isaac-GR00T
- Diffusion Policy: https://arxiv.org/abs/2303.04137 ; https://github.com/real-stanford/diffusion_policy
- DreamZero: https://arxiv.org/abs/2602.15922 ; https://github.com/dreamzero0/dreamzero
- V-JEPA 2: https://arxiv.org/abs/2506.09985 ; https://github.com/facebookresearch/vjepa2
- DINO-WM: https://arxiv.org/abs/2411.04983
- RMA: https://arxiv.org/abs/2107.04034
- ∇Sim: https://arxiv.org/abs/2104.02646
- PAC-NeRF: https://arxiv.org/abs/2303.05512 ; https://github.com/xuan-li/PAC-NeRF
- Active Sensing Motor Policies: https://arxiv.org/abs/2311.01405
- Robot proprioceptive system identification: https://arxiv.org/abs/2410.03920
- Physion / Physion++: https://arxiv.org/abs/2106.08261 ; https://arxiv.org/abs/2306.15668
- 3DGSim: https://arxiv.org/abs/2503.24009 ; https://github.com/martius-lab/3DGSim

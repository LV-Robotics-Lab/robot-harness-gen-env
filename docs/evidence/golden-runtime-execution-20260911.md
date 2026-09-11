# Manifest 驱动的 Genesis CPU execution

固定实现提交：`fd50576`。这是单资产CPU执行证据，不是完整环境、机器人策略或production Skill资格。

## 真实路径

1. 从固定提交归档Harness backend，与解释器、stdlib、distributions、Genesis归档、native共六根
   生成canonical CAS manifest。部署绝对路径不进入逻辑内容身份。
2. 新runtime lock通过既有dependency字段精确引用manifest，implementation摘要绑定完整backend。
   request同时绑定representation、lock与固定runner；operator批准的manifest不能由request替换。
3. 固定launcher核对部署和已安装Harness源码，以声明的解释器启动归档child。Landlock限制运行根
   只读，单独run目录可写，socket系统调用被拒绝；两个GL部署别名逐bytes绑定native成员。
4. child真实运行initial observation及1,000 CPU/Newton steps，记录实际module/native、环境、线程、
   kernel facts、stdout/stderr和完整probe refs。动态或无文件模块单独分类，不伪装成磁盘成员。
5. parent重验部署与同一次probe的完整CAS闭包，最后写execution receipt并返回opaque live capability。
   纯读verifier可以重算闭包，但返回`execution_authority=False`。

字段合同见[execution合同](../../self_improving/harness/GENESIS_RUNTIME_EXECUTION_CONTRACT.md)；
实现见[producer/verifier](../../self_improving/harness/genesis_runtime_execution.py)、
[launcher](../../self_improving/harness/genesis_runtime_launcher.py)、
[child](../../self_improving/harness/_genesis_cpu_child.py)。公共测试与三个新增schema在同一固定提交中。

## 当前实际结果

- manifest：`6d25b8e2359b343564bf7a2e845ad559b32bfb26c14a7f433633f3af6c25c14e`；166,015成员，
  manifest 79,543,751 bytes，capture 243.432s。
- 正式Producer成功，execution receipt：
  `bc6511ad9028fc2bcaa479d4cf8a9c5287b6d453d259cccf2f9ddd317bf65d78`；1,001rows、live capability
  返回，CAS-only重读闭包相等。
- 正式坏XML用例被真实child拒绝，保留stdout/stderr及partial-CAS索引。首轮测试364.39s，
  1 passed/1 failed：旧测试只允许两份输出，遗漏新增索引。随后修正为严格验证三份输出和全部
  partial对象，真实坏XML重跑通过；原始失败日志保留。
- 原始配置、CAS、回执和日志：
  `/home/jingxiang/bingsheng/golden-runtime-bound-20260911.ndky_vr0/`。
- 第二独立六根部署的正式Producer及strict reload通过：1 passed，258.71s。目录为
  `/home/jingxiang/bingsheng/golden-runtime-bound-relocated-20260911.i7zlos7a/`。
- 源`execution-summary.json` SHA256：
  `9afc8700f2d1bfe195dba641cd26a8930b74113ea14425f6263a4949adb3cf1e`。
  第二部署同名摘要SHA256：`c701aada3e9a3c4ddb0e7e038be1efed4d6b293bb4763a55a48986cfbd977436`。
  [固定提交集中功能验收](golden-runtime-execution-acceptance-20260911.json)通过：两个新进程独立
  重读各81,971个supporting对象；物理报告一致，回执差异仅为stderr中的部署路径。

## 能力边界

97份schemas已验证。独立诊断wrapper的child覆盖为129/145 statements、21/34 branches。随后按
源码SHA相等合并真实与离线测量，launcher为129/137 statements、39/46 branches；child为132/145
statements、23/34 branches。继续以真实内核失效、ABI/env错误、150,000真实maps和部署后检漂移
补测后，最终launcher为134/137 statements、42/46 branches；child为142/145 statements、31/34
branches。诊断测量没有改变child字节或放宽运行权限，但不是production invocation，不用于签发；
尚未达到所有新核心模块statement/branch 100%。剩余逐条可达性/环境要求记录在源证据目录的
`coverage-final-boundaries.md`，SHA256为
`95245b39c71470916e16661b29d6c348b4bfe5f0555562bb8195ed685e8c015a`。

完整probe之后的部署漂移也已真实验证：仅改变私有backend副本，后检拒绝，清理后仍可重读
stdout/stderr、六refs和1,001rows；独立用例1 passed，234.73s。第二部署目录下
`postflight-drift-summary.json` SHA256为
`0854faef48a60bc390b7e1297c62729a5b66b5b4894c53d4d06bf86fe21e5232`。

资产质量与摩擦仍来自原SimFoundry估计。这条证据证明给定参数下的load/contact/settling，不证明真实
物性测量。durable asset qualification仍在实现，production image/video Registry/MCP、完整Genesis
environment replay、robot-policy及promotion不在本次通过范围。

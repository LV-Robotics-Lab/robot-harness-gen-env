# Genesis CPU runtime closure：实测与物化，2026-09-11

本次已在独立物化环境真实执行 initial observation + 1,000 个 CPU/Newton steps，并纯读重验
1,001 行证据。它仍使用已有、明确未资格化的 runtime lock v1；本笔记不授予 production asset
qualification，也不声称 environment replay、policy、promotion 或 MCP image tool 已完成。

## 执行依据

- 来源 Genesis HEAD：`0e74bf392781884ccad765c3f344419c86b872ca`，读取时工作树 clean。
  使用 `git archive` 物化固定源码，另用 `git clone --no-hardlinks` 创建真实 detached checkout，
  供旧 backend 的 `git rev-parse HEAD` 检查；没有伪造 `.git`，未改源环境。
- 正式源解释器：Python `3.12.3`、GCC `13.3.0`、ABI `cpython-312-x86_64-linux-gnu`。
  venv interpreter 软链最终指向 `/usr/bin/python3.12`。
- 独立执行目录：`/home/jingxiang/bingsheng/golden-runtime-seal-20260911.nt5e629u`。
  `stage.py`、`stage_native.py`、`launch.py`、`fresh_probe.py` 是可检查的外部验收脚本，
  bulk bytes、CAS 和缓存不进入 Git。

## 实际依赖与文件布局

| distribution | version | RECORD 项数 | 有 hash 的字节数 |
| --- | --- | ---: | ---: |
| genesis-world | 1.3.3 | 13 | 49,511 |
| torch | 2.14.0+cpu | 14,837 | 714,176,511 |
| quadrants | 1.3.0 | 400 | 123,374,828 |
| numpy | 2.5.3 | 1,336 | 56,278,840 |
| scipy | 1.18.1 | 2,421 | 111,800,814 |
| trimesh | 4.12.2 | 260 | 2,025,175 |

通过 `importlib.metadata` 读取六份 RECORD，并逐文件按记录算法/base64 digest 重算；没有 missing
或 hash mismatch。RECORD 自身及大量 `.pyc` 没有上游 hash，因此必须另外纳入逐字节 manifest；
editable Genesis 的 13 项不能代表 Genesis 源码。对 Genesis/Torch/SciPy 的非 extra requirements
递归求闭包得到 88 个已安装 distribution、零 missing；整个源环境有 132 个 distributions。
这些数字是该机器实测，不是所有可能执行路径的静态完备证明。随后在 `-S` 物化进程中对可见的
130 distributions 全量验证 RECORD：49,264 个有 hash 的 member 通过，32,387 个无 hash 的 member
另由 byte manifest 绑定，没有缺失、hash mismatch 或越出 distribution 部署根。`-S` 下未执行
editable `.pth`，因此可见 metadata 数与普通源启动的 132 不同。结果保存在
`record-verification.json`，未改 RECORD。

完整物化保留原 RECORD，包括 `../../../bin` 指向的脚本；不重写 RECORD 来掩盖安装差异。
venv 原有四条软链（`lib64` 与三个 Python entrypoints），stdlib 有三条软链，均在新目录解引用。
`lib64` 的完整物化副本使 distribution 根为 163,361 files / 7,830,629,139 bytes；stdlib 为
1,245 files / 63,334,536 bytes；Genesis archive 为 1,053 files / 229,250,380 bytes；interpreter
为 1 file / 8,020,928 bytes。以上物化根均无软链。

源 site-packages 包含指向 Gujie 工作区的 editable `.pth`，涉及 asset validator、asset retrieval
与 robot harness。因此启动使用 `-S -B`，显式给出 staged stdlib/site-packages/source path，
不让 `.pth` 执行；不能在普通 venv startup 完成后才改变 `sys.path` 并声称未导入源工作区。

## 真正执行的结果

`python3 <staging>/launch.py` 启动物化的动态 loader 与解释器，实际环境保存在
`launch-contract.json`；运行输出保存在 `fresh_probe.py.log`。

- 新 CAS 初始只复制 representation、15 个 member 与旧 v1 lock，没有复制旧 report/trace。
- fresh run 耗时约 47.96 秒，1,001 rows，deep verifier 通过；确定性的 report SHA 为
  `9e46011ecfb6364aeeed3d58ffd7d0ac81b35f4d61880a3909f88a60d866f5b9`，与原运行一致。
- 实测 Torch intraop/inter-op 为 `1/1`。仅设置 `OMP_NUM_THREADS=1` 时 inter-op 实测为 `16`，
  因此 launcher 另行调用 `torch.set_num_interop_threads(1)`。
- 固定 `QD_NUM_THREADS=1`、`QD_OFFLINE_CACHE=0`；Genesis/cache 输出使用新目录；
  `LC_ALL=C` 与 `PYTHONUTF8=0` 消除了初版 locale/gconv 系统映射。
- 最终进程有 309 个 file mappings。首次仅 import 时没有出现的九项包括 Madrona、Embree、
  NVRTC/nvJitLink libraries；完整 distribution 根已包含它们。CPU 物理、无 camera，并不意味着
  import closure 不会载入渲染/GPU 相关库。
- 两个系统定位的 `libGLX.so.0.0.0`、`libOpenGL.so.0.0.0` 仍被绝对加载，即使指定 staged
  `LD_PRELOAD`。其字节已物化 native 根，但必须分账“被声明的 bytes”与“实际加载位置”。

完整机器摘要：`fresh-probe-summary.json`，SHA-256
`5e42cf8045dfaecf1ff55e61088e3aeac2bdd3b6ef0fa58832c105bd9c85cfba`；import-only 的摘要不能替代它。
`record-verification.json` SHA-256 为
`75bbc6f7aeaf9f6a15458dceb009e6c13db3282d2fe65ce39808a6a1a2792534`。

## Declared manifest 与第二路径

使用固定提交 `3662ba6` 的两个 manifest 模块，完成实际 `capture_runtime_manifest`、
`read_runtime_manifest` 和原路径 `verify_runtime_deployment`，耗时 311.743 秒。
共 166,141 members / 8,542,495,955 declared bytes。逐 member 去重并加入 manifest 自身，精确
supporting closure 为 82,070 objects / 4,567,960,119 bytes；整个 CAS 含 82,072 objects /
4,567,968,119 bytes，额外两个共 8,000 bytes 是首次被中止 capture 的旧模块不可达对象，
不能把整个 CAS object count 当作 exact manifest closure。
最终 manifest 为 79,598,134 bytes，SHA-256
`06ed1a2fe2322903c35caceefcf2f504558c32eee0ae628ea72aa1c25deba927`。
`manifest-summary.json` SHA-256 为
`d41813a7e9177aede0fc4c034fdc5908cca1f337e1caf899c3ea38ce8a4ac1d7`，保存 exact ArtifactRef、
六根 mapping 与观察所得 process identity。runtime module SHA 为
`5676fcb2cf8b70c7db905ce44569097e42f6666037694eb3b52540d2e8d2071d`；schema module SHA 为
`79bf47a16b66455022dd3e6c664d0deb080f8196ac65796686ad7ec1f2e4b4b1`。

六个独立物化根和第二 CAS 已复制到
`/home/jingxiang/bingsheng/golden-runtime-relocated-20260911.25kyjznz`。第二绝对路径 fresh import
已实际通过：3,023 modules / 300 file maps、Torch threads `1/1`、zero Gujie module/map paths。
这不等于执行约束；同样保持上述两个系统 GL mappings 的边界。

第二 CAS 的 fresh-process `read_runtime_manifest` 与 `verify_runtime_deployment` 也已通过，
耗时 76.788 秒，166,141 members、同一 manifest SHA、重新采集并归一后的 process identity
相等，过程中不导入 Genesis 或原 Harness worktree；结果为第二目录的
`relocated-verification.json`，SHA-256
`4ba1a82e74002b5bcfa1902313c8fa66ea58b9702baf32f8e73259e8979378dc`。
这一结果证明声明字节闭包可迁移，不扩大 execution authority。

## 失败和 authority 边界

1. 第一版 staged import 在设置 `sys.path` 前加载了系统 stdlib。后续把 startup `PYTHONPATH`
   指向物化 stdlib，并设置 `-X frozen_modules=off`；`zipimport` 的 frozen origin/file label
   仍须与真正从磁盘读取的 module files 区分。
2. 第一次 fresh probe 在 import 阶段报 `ModuleNotFoundError: self_improving.registry`；
   补入同仓库固定文件后才执行第二次真实 probe。没有将第一次算为 simulator failure/pass。
3. 最小隔离命令 `bwrap --ro-bind / / --unshare-user --unshare-pid --proc /proc -- /bin/true`
   返回 exit 1、`bwrap: setting up uid map: Permission denied`。当前主机不能凭这个命令形成只读
   execution namespace；没有绕过或把事后 maps 采样写成进程隔离。
4. runtime manifest 只声明 exact 六根的 canonical CAS-backed byte closure；process identity 由
   验收进程读取，部署路径用 `${DEPLOYMENT}` 归一并检查逆映射，绝对 locator 单独保存。
   这个验收脚本的归一化不是 production launcher 合同。当前 probe 用 `genesis-checkout`，
   manifest 的源码根用 archive；两者相同固定源码并不允许忽略 checkout 的额外 `.git`。
5. 生产门仍需固定运行入口、全部 subprocess/native/config 依赖、pre/post 重验和进程加载范围；
   遗漏、额外文件、bytes 漂移、symlink、path escape、native/env 漂移必须 fail closed。
   opaque verified capability 与 durable qualification 的最终 CAS 写入未在本次审计中实现。

结构化失败/不足清单为源 staging 的 `failures.json`，SHA-256
`d779c5550a77e8b6ceb26d6e52bbf712b331b17702a3b9e69d66abf4a575a94d`。保存首次被中止 capture 的
orphan，未把中止、import pass 或 namespace 失败改写为物理成功。

repo-docs sync：已检视平台指南与本笔记对应的 backend/probe 源码；本笔记只新增运行依据，
由主窗口在同一功能提交同步 progress、provenance 与指南，不改变历史证据主张。

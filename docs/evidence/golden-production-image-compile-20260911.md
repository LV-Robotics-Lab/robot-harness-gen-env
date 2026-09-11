# Production image2env compile@2.0.0 验收

本切片把 native image exact-reuse compiler 接入现有 Registry / SQLiteRunStore / EventJournal，
以两次候选生成 v2 evidence qualification，并真实注册 `image2env.compile@2.0.0`。compile 只输出
`static_scene_candidate`，本阶段本身不执行 Genesis，也不授予 replay、environment validate、
promotion、MCP 或 robot-policy 权限。

完整小型索引：
`/home/jingxiang/bingsheng/golden-native-compile-app-terminal-closed-20260911/acceptance-index.json`
（SHA256 `f8d0ccaf108598d41e21e507df7dfe66753d51479f2de245665f564a79a814de`）。
冻结应用源码 SHA256 为
`bf145ef6d46bb3ff2df5eb04d72286f6b55e2b775780886abecb516533ac1b94`。

## 三个生产输入

三份输入均为 Gujie historical simulated mouse view，interpretation 为 operator-authored fixed
proposal，不是 fresh reconstruction 或自主图像理解。

| view / seed | source SHA256 | registered run UUID | compile receipt |
| --- | --- | --- | --- |
| overview / 17 | `c276230c…` | `7bb844b1-4921-4648-9fde-cc035628a408` | `2b241112…` |
| top / 23 | `96bcc82a…` | `186d3021-e610-4fc1-a7d9-f2b61d875db0` | `05401040…` |
| side / 41 | `60a37388…` | `a42a3a83-98ea-4c01-a35f-3003a9109c49` | `feb0be2d…` |

三条路径均由可信 ControlIntent / Invocation / RunState 公共 seam 读出成功终态，descriptor SHA
为 `d52bc4c4…`，Skill qualification 为 `57dff640…`，qualification report SHA 为
`d3dea10d…`。三者确定性得到同一有限桌面 scene `e543ef65…`，但各自保留不同 source image、
proposal、seed、request、receipt 与 invocation lineage。

## 真实执行与恢复

- 3 candidate + 3 registered invoke + deep restart + UUID冲突：16 passed / 1 skipped，379.18s；
  member loss 节点另以 1 passed / 89.54s 完成。
- same-live terminal 与 restart 都调用 compiler pure reader 深验 nested catalog、qualification、
  representation members 和 scene；独立 CAS 中删除一个副本 member 后 fail closed，原 CAS 不变，
  没有再次执行 compile。
- 第二绝对 CAS / SQLite 路径复制 281 个新增对象、126,495,118 bytes，复用已封闭 runtime；
  三个原终态 deep reload、一个 fresh registered invoke 及其 restart 196.22s 通过。
- 在独立代码副本修改 registered compiler body 或应用源码，分别被 dependency / source identity
  门拒绝；原 checkout 未修改。candidate/report 五项与 terminal 两项完整性反例通过。

早期真实 RED 包括 Registry 不能序列化 UUID、旧 candidate 可在应用源码漂移后签资格，以及成功
terminal 缺 nested member 时 same-live retry 未深验。这三项都以对应公共调用复现并修复；失败日志
与哈希列在 acceptance index，没有重写为成功记录。

最终合并实际 trace 后，application schema 为 37/37 statements、6/6 branches；应用为
142/143 statements、48/50 branches，原始覆盖率 98%。未覆盖的是源码 identity 捕获与源码 CAS
发布之间的并发漂移防御，以及已绑定同一资格的幂等分支；不宣称 core 达到原始 100%。

输出仍需由 production replay v2 自行读取上述 registered compile UUID/terminal；scene 内容相同
不能替代该权威关系。资产 runtime qualification 与 renderer scene runtime 分别绑定。

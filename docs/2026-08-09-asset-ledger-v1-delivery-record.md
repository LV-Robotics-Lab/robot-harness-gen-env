# asset_ledger.v1 交付记录（feat/asset-ledger-v1）

> **日期**：2026-08-09 · **范围**：ab06a47..0e7c292（17 提交，15 文件）· **终审**：可合并（全分支审查，零合并前必修项）
> 执行方式：subagent-driven（每任务独立实现者+审查者+scoped 复审；T5 经 3 轮修复收口，全程审计记录见附录）

## 1. 交付内容
- `lib/ledger.py`：asset_ledger.v1 契约库（validator 全规则含 bad_timestamp、upsert、fcntl 锁 append、latest-per-(backend,check)+digest 失效语义、`to_ir_bundles` IR 拆包、`write_ledger` 原子写）＋46→49 条单测。
- `scripts/backfill_ledger_v1.py`：per-model 旧 bundle 聚合升 v1（dry-run 默认、幂等、`--bundle-alias` 逃生舱、pending-manifest 写序、web_runs 信任过滤）。
- `scripts/gen_fragment.py`：账本→fragment（latest-settle+digest 过滤、default pose 投影、`--license-gate` 默认关+unknown 常显警告）。
- 四管线接入：`import_materialize`（两层校验/快照渲制/quarantine 裁账本/model 粒度清理/I-3 sys.path 修复）、`s13b`（articulated+balance_gate）、`s11`（runtime_load 回填）、`s5`（经拆包读权威账本）。
- `.gitignore` 账本豁免；README 账本契约用法+发布纪律（license-gate、手工删库文件不安全）。
- 测试：worktree 139 passed / openxsim 51 passed。

## 2. 真实池状态（主树 data/asset_library，非侵入式验收）
- **10/16 资产入 v1**（301_cup/302_can/303_box/304_bottle/305_bowl/306_block/307_banana/308_pitcher/309_power_drill/311_marker）；账本 `validate_ledger(check_files=True)` 全量 0 violation（含 sha256 实算，双 conda env 验证）。
- **6 个排除**（301_brick/302_bowl/303_boombox/304_kettle/305_teacup/314_cabinet）：`_source` 镜像目录整体缺失或旧 bundle 缺 size_resolution/mesh_up_axis 等过程记录——validator 未放宽，补数据重跑 backfill 即可纳入（见 §4-B4 前置）。
- fragment 语义等价：10 可比资产结构相等（映射错误=0），生成版在 `results/20260809_ledger_v1_accept/fragment_generated.yml`。
- 隔离验收：s9 隔离影子 PASS + s11 runtime_load 18/18（已回填账本）。
- **未做（推迟到合并/生产切换）**：主树 merged fragment 替换、生产 shadow 重建、s10/s12 四连、10 份账本 git add。

## 3. 合并指引（与主树 feat/env-gen-ir-bridge）
- 已提交部分 merge-tree **零冲突**；冲突面全部来自主树未提交改动：`import_materialize.py`（**必然冲突，取本分支侧**——I-3 修复包含对方的一行修法）、`README.md`（毗邻插入，手工合段）。
- 合并后数据面步骤：① `git add data/asset_library/*/ledger.json` 提交（豁免规则随分支生效）；②生产切换（用户定时机）：fragment 换生成版→重建生产 shadow→**补跑 s10/s12 四连**（补齐 spec §7.3 缺口）；③继续 pathspec 提交纪律。

## 4. 后续硬化清单（终审 triage，按优先级）
1. 全库文件完整性巡检工具（入库门只查本 model，兄弟/排除资产磁盘漂移无人巡检——设计移交的补位件）。
2. 资产退役路径（retire 工具：删文件同时经 write_ledger 裁条目）。
3. backfill/s13b 写盘收敛到 write_ledger API；s13b 的 violation-仅-WARN 语义与 materialize 门禁对齐。
4. **镜像目录 retrieved_at 改取目录内最新文件 mtime——在为 6 个排除资产补数据重跑之前必须先修**（否则入 v1 带错日期）。
5. `_is_iso_datetime` 收紧规范 T 形（py3.10/3.11 fromisoformat 分歧、空格形排序反转，均潜伏零暴露）。
6. verified_digest 写入时刻时序缺口（与 spec §8 verification 长期形态一并）。
7. spec §3/§6 补文档码：bad_type/bad_timestamp/bad_sha256（ledger.py 表为规范，spec 为文档视图）。
8. 低优先：RMW 读侧锁、结构层整本作用域取舍、manifest 非原子写、不可信 bundle 拒绝记录、孤儿快照、fsync。

另：license audit（NVIDIA EULA/YCB 两来源核查→批量回写）为**待议**项（owner 2026-08-08 标记），发布前必须 `--license-gate` 且 unknown 归零。

## 5. 硬化执行记录（feat/ledger-hardening）

对照 §4 后续硬化清单的编号，H1/H2 完成项如下（M-6：本节移到附录之前，与 §1-4 同属正文）。

**H1（契约层小修，commits c820853, 0208840）** — §4 编号 4/5/7 + 三个顺手项：
- 4：backfill `generated_from_mirror_dir` 分支的 `retrieved_at` 改取镜像目录内最新**文件** mtime（不再用目录自身 mtime，防增删文件污染日期）。
- 5：`_is_iso_datetime` 收紧为规范 T 形（19 字符/秒级/正则先行），拆出 `_is_iso_date` 专司 `source.retrieved_at`；消灭 py3.10/3.11 `fromisoformat` 接受集分歧与空格分隔形排序反转隐患。
- 7：spec §6 补「validator 违规码全集」小节（`bad_type`/`bad_timestamp`/`bad_sha256`）。
- 顺手：`_atomic_write_json` 补 `flush()+fsync()`（§4-8 低优先项之一）；backfill 的 `SOURCE_MANIFEST.json` 落盘改临时文件+`os.replace` 原子写；`_find_latest_bundle` 信任过滤拒绝的候选记入 `notes.bundle_rejected_untrusted`。
- 测试：139→148 passed（0 回归）。

**H2（工具+写盘收敛）** — §4 编号 1/2/3，含审查修复轮 1（下方以最终状态描述）：
- 1：新增全库巡检工具 `scripts/ledger_audit.py`——扫 `*/ledger.json` 逐份 `validate_ledger(check_files=True)`，无账本但已材质化（有 `model_data*.json`）的资产记 `no_ledger`（非失败）。exit 码：有 violation → 1；扫描结果 `audited==0` 且 `no_ledger==0`（很可能 `--library-dir` 指错目录）→ 2（M-4）；否则 0。一句话用法：
  ```bash
  python scripts/ledger_audit.py --library-dir data/asset_library [--out report.json]
  ```
- 2：新增退役工具 `scripts/retire_asset.py`——`--model N` 删单 model（网格+`model_data{N}.json`+快照，账本裁条目经 `write_ledger` 原子写回；裁空则连账本/`.lock`/整目录一起删），不带 `--model` 则退役整资产；只清**池内**文件+裁账本，不级联清理 `results/**/bundles/` 运行快照或 `_source/` 镜像（镜像可能多资产共享，保守不清是有意设计，README 已说明）；默认 dry-run。containment guard（I-1/I-2）：`--asset` 转义 `--library-dir`（`../`、绝对路径——pathlib 的 `/` 遇绝对右操作数会直接丢弃左侧）或落在 symlink 上（防误指到 `data/robotwin_shadow/` 之类的影子根、经软链删到真实池）均拒绝，exit 2；无账本资产 / 未知 `--model` 是业务性错误，exit 1。一句话用法：
  ```bash
  python scripts/retire_asset.py --library-dir data/asset_library --asset <name> [--model N] [--apply]
  ```
- 3：写盘收敛——`backfill_ledger_v1.py` 与 `s13b_validate_articulated.py` 的账本落盘从裸 `write_text` 改为 `lib.ledger.write_ledger`（原子+锁+权限）。s13b 门禁语义对齐 `import_materialize`：`validate_ledger` 有 violation 时 stdout 打印 `FAIL s13b: schema violations (N)`（本脚本既有 FAIL 风格）+ 整体 `exit 1`（I-3：仅拦写不够，会让"账本没落但仍 PASS/exit 0"比改前的"WARN 仍写"更易被忽略），详细违规逐条走 stderr；**不写权威账本**，运行快照（bundle/validation JSON、screenshot、model_data）仍照写。顺手：`import_materialize.py` 的 quarantine 段落补删被隔离 model 的孤儿快照 `snapshots/m{N}_*.png`。
- 审查修复轮 1（4 Important + 3 Minor 落地，3 Minor 推迟）：I-1/I-2 上述 containment guard（复审曾在隔离池实测越界删除成功，真实破坏面）；I-3 上述 s13b FAIL+exit 1；I-4 README 措辞补充「retire_asset.py 不级联清 results/bundles 与 \_source 镜像」；M-2 整资产 rmtree 前打印实际文件数（**选择了"改文案"而非"实现双重门禁"**——纯信息展示，真正把关的仍是 containment guard，不做成会误导"通过即安全"的第二道假门）；M-4/M-6 见上；M-5 `ledger_audit.py` docstring 补一句：`representations[].uri` 是导入时刻的绝对路径，库搬家/换挂载点会让 `check_files=True` 整批误报 `file_missing`，非本工具能特判。M-1（两工具对坏账本 JSON 的口径不统一）、M-3（rmtree 执行到一半的中间态）、M-7（空 `snapshots/` 目录遗留）如实推迟，未修。
- 测试：148→159（H2 首轮）→163 passed（修复轮 1 新增 4 条：`retire_asset` traversal 3 条 + `ledger_audit` 空扫描 1 条；s13b 既有测试原地加断言未新增用例；0 回归）。
- 范围说明：`lib/ledger.py` 全程未改动，两个新工具与写盘收敛均直接消费其既有 API（`validate_ledger`/`write_ledger`/`ledger_path`）。`import_materialize.py` 的孤儿快照一行改动因该文件目前无任何 pytest 覆盖（无 `main()` 守卫、纯脚本式，需真实 trimesh/SAPIEN fixture 才能跑通）而未配自动化测试——功能改动已核对代码路径（与快照写入的命名约定 `m{model}_default.png` 精确匹配），如实说明留作已知缺口。

## 附录：执行审计 ledger（原文）
# SDD ledger — plan: /private/tmp/claude-501/-Users-yuxin-Library-Mobile-Documents-iCloud-md-obsidian-Documents----/350ddfd4-ebf6-4c54-9ca8-0e3ff58a36e9/scratchpad/2026-08-08-asset-ingest-metadata-contract-plan.md
# 远程仓库: lv-5090:/home/jingxiang/yuxin/env-gen-dev · 工作树: /home/jingxiang/yuxin/env-gen-dev-ledger (Task1 创建) · 分支: feat/asset-ledger-v1
Task 1: complete (commits ab06a47..43e3273, review Approved)
Task 1: ruling — reviewer Important finding "Step 1.5 环境准备越权" 不成立：Step 1.5 是控制器在派单 prompt 里明确指示的（worktree 缺 data/external/openxsim 软链），属已授权上下文，非实现者擅自偏离
Task 1: minor (deferred): openxsim deps/third_party 软链在 worktree 显示 untracked（既有 gitignore 尾斜杠规则不匹配软链）; 完整 40 位 SHA 已由控制器 rev-parse 复核
Task 2: review 需修复 — C-1 upsert 漏检 semantic_name 漂移; I-1 snapshot 排除零覆盖; I-2 latest 时间戳排序未钉住; I-3 整条替换零覆盖; I-4 models:null 假阴性; I-5 mass/friction 非 dict 绕过; I-6 REQUIRED_MODEL 漏 model_id/verification; I-7 derive_usable/reps_digest 公式零测试
Task 2: ruling — M-11(derived_field_handwritten 语义)：spec §3.9 "禁止手写"为准，出现即违规；plan 文本"且与推导不符"作废（spec > plan prose），实现不改
Task 2: minor (deferred): M-2 半深拷贝; M-3 bad_sha256 契约外码(本轮补测试); M-4 缺 fsync; M-5 缺 timestamp KeyError; M-6 append 不校验条目; M-7 整文件读 hash; M-8 conventions 遮蔽; M-9 死条件; M-10 CASES 不校验 path
Task 2: fix round 1/5 (10 addressed, 1 new open — model_id:null 校验放行(净退化,I-6 修复副作用); commits 20654ea..fd250cb)
Task 2: fix round 2/5 (1 addressed, 0 open — NOT_NULLABLE_MODEL 全量收敛+stable_poses null 旁路顺带堵上; commits fd250cb..a1f6c97)
Task 2: complete (commits 43e3273..a1f6c97, review clean after 2 fix rounds; ledger 测试 46, 全量 115)
Task 3: complete (commit 9d5c712, backfill_ledger_v1.py + 3 测试全过；ledger+backfill+a4_coverage 共 56 测试全过)
Task 3: fix round 1/1 (1 addressed — origin_convention 未判 backend==sapien 静默取错值; commit 536da5b; 新增回归测试 test_origin_convention_prefers_sapien_backend；57 测试全过)
Task 3: note — 全量 tests/ 直接跑在 collection 阶段炸 6 个无关文件（agenticsim/scene_acquire ModuleNotFoundError，并发会话 scripts/ 重组遗留，与本任务改动无关，已用移出文件复现法确认），未修复（越权风险，已如实上报）
Task 3: review Approved 但 1 Important 进修复轮 — origin_convention 缺 backend==sapien 过滤 (commits a1f6c97..9d5c712)
Task 3: minor (deferred): converter 空串被 or 误判为缺省; mass_override 残缺 dict 不补全(如实暴露可接受); notes 三路径/exit1 分支/articulated basis 分支未被测试执行(T8 真实池留意)
Task 3: 控制器亲测: 全量 118 passed 需 PYTHONPATH=.:scripts/d_acquire:../shared/openxsim/source/agenticsim（并发重组后 6 文件收集依赖此路径，后续任务派单需带上）
Task 3: fix round 1/5 (1 addressed, 0 open — backend==sapien 过滤+差分测试; commits 9d5c712..536da5b)
Task 3: complete (commits a1f6c97..536da5b, review clean after 1 fix round; backfill 测试 4)
Task 4: complete (commits 536da5b..af56ae3, review Approved 零修复轮; fragment 测试 5, 全量 124)
Task 4: minor (deferred): 多 model 部分过滤/全部过滤边界无自动化测试(代码走查正确, T8 真实池 302_can 4-model 会实际走到); _default_pose 无 per-asset 容错隔离(validator 保证不变量, 设计取舍)
顺序调整: T5(import_materialize) 推迟——主树该文件仍有并发未提交改动(对方持续开发中, 新提交 1c5cd65); 先做 T6(s13b)/T7(s11+s5)——三文件主树干净无冲突风险. T5 待对方落地后执行.
Task 6: review 需修复 — I-1 movable_meta dict 形状假设(mock 自证); I-2 非数字 instance 目录名静默 model_id=0(覆盖风险) (commits af56ae3..03806a4)
Task 6: minor (deferred): reflow 披露计数不准(实为≥5处,已逐处核对无行为变化); joint_types .get 兜底不对称; q_show 死代码为改动前既有 bug(留后续)
Task 6: fix round 1/5 (2 addressed + Minor-1/2 顺带, 0 open — s13a 真实 schema 已从源码核实(rec dict 列表), isinstance 防御+对称兜底 helper, 非数字目录名 fail-fast+subprocess 真测; commits 03806a4..6838c2c)
Task 6: complete (commits af56ae3..6838c2c, review clean after 1 fix round; 全量 126 passed 控制器亲测)
Task 7: review Approved + 1 Important 进修复轮 — s5 to_bundles 调用在 try 外(结构损坏账本崩全脚本) (commits 6838c2c..9127584)
Task 7: parked — verified_digest 写入时刻时序缺口(catalog 快照可能落后于账本, 插入前漂移不被 latest 失效机制覆盖) — ruling: brief 草图设计所致非本任务引入; T8 流程中 catalog 由 fragment 现生成、漂移窗口极小; 记为后续硬化项(与 spec §8 verification 长期形态一并), 汇报用户
Task 7: fix round 1/5 (1 addressed, 0 open — to_bundles 入 try+两参数回归测试, 复审纠正测试构造获确认; commits 9127584..95c5463)
Task 7: complete (commits 6838c2c..95c5463, review clean after 1 fix round; 全量 131)
T5 启动决定: 主树 import_materialize.py 并发改动仍未落地; 在 worktree 基于已提交版本(81402ec 状态)改, 不扰动对方; 合并时冲突由合并者解决(时机用户定)
Task 5: review 需修复 — C-1 footprint 人工覆盖丢弃(4 真实资产 circle→box); I-1 落账非原子无锁; I-2 快照 bundle 取 [-1] 隐性耦合; I-3 预置 sys.path bug 使脚本不可执行(控制器裁决:纳入本任务修复,一行,在本任务已编辑的 import 块内); I-4 fragment 全库化×acquire 拼接 N 倍复制 (commits 95c5463..48ba649)
Task 5: ruling — I-4: materialize 的 --overrides-fragment 输出过滤为本次 run 资产(生成自账本的子集投影), 全库 fragment 归 gen_fragment CLI/T8; 依据: spec 只约束"fragment 由 generator 从账本生成", 未约束 materialize 输出范围; 此裁决保持 acquire_batch 拼接与 s9 wanted 断言语义零变
Task 5: ruling — M-1 升级进修复轮: driver 预清空 rmtree 连权威账本+verification 历史一起删, 违背 §3.8 append-only 审计意图; 预清空需保留 ledger.json
Task 5: ruling — source_manifest_path 张力: validator 胜(ledger.py 表为契约规范), "缺 SOURCE_MANIFEST=拒收"为有意语义; 建议的明确 reason 文案留 T8 观察后定
Task 5: minor (deferred): M-2 孤儿快照+窄窗口账本-文件不一致; M-3 空 aliases 无兜底提示; M-6 _source 硬编码; M-7 validate O(n²) 整读哈希; M-9 physical.scale 键消失(无 b_batch 消费者); M-10 backfill 通配会撞新摊平快照(T8 brief 必须提醒); ~9 处 black reflow 未披露(审查者逐处核对语义中性)
Task 5: fix round 1/5 (7 addressed + 1 主动扩展(quarantine 第二处 rmtree,复审确认正确), 1 NEW CRITICAL open — M-1 保留账本×整目录预清空×全账本 validator 组合致多 model 重跑批量拒收+悬空条目+fragment 指向已删文件, 真机双 run 复现; commits 48ba649..13e77fa)
Task 5: ruling — (b) 项池层语义: models[] 是池内当前状态映像(非审计日志), quarantine 裁掉对应 model 条目(经 write_ledger 原子写回), 零 model 则删除整本账本; 审计痕迹由 import_matrix+运行快照承担; 与 OVERVIEW 既有"淘汰物理隔离出资产池"语义一致, 不违背"只进不出"(那指不主动删通过的资产)与 verification append-only(那指在池 model 的验证历史)
Task 5: minor (deferred): write_ledger 只罩写不罩读(RMW 非原子,当前串行不可达); backfill/s13b 两写手未收敛到 write_ledger API(后续统一); ledger.lock 常驻资产目录(功能无害); I-2 next() 无 default(try 内降级 rejected,可接受)
Task 5: fix round 2/5 (原 Critical 实例闭环+fix(b)裁账本双向实测好, 但问题类未闭环 — 复审复现第二实例: 改几何+门禁失败→sha256_mismatch 连坐→fix(b)放大为整资产静默清库; 另: 空壳资产目录会被 s9 注入影子(小回归), harness 在 /tmp 未入库; commits 13e77fa..4f51156)
Task 5: 复审裁定采纳 — 池层"manifest 缩减后旧 model 残留"=正确语义非脏数据; 但"手工删库文件从此不安全"需文档化+retire 工具或 T8 全库巡检兜底(记 T8)
Task 5: fix round 3/5 (Critical 类闭环 — 两层校验(结构整本+文件仅当前model), 复审用原版物理触发器独立重放 PASS, A/B 对照证明 load-bearing; 空壳清理/注释订正/harness 入库齐; 顺带解决 M-7 O(n²)哈希; commits 4f51156..87acc9d)
Task 5: complete (commits 95c5463..87acc9d, review Approved after 3 fix rounds; 全量 132)
Task 5→T8 携带项(复审汇总): ①全库文件完整性巡检落 T8(兄弟 model 文件校验按设计移交); ②无退役路径-手工删库文件不安全需文档化; ③RMW 非原子(串行不可达); ④backfill/s13b 未收敛 write_ledger API; ⑤acquire_batch 入口 sys.path bug 仍在(非 T5 范围); ⑥孤儿快照/reasons 重复/--source-root 顺手项; ⑦reasons 非互异多重集勿建计数指标; ⑧结构层仍整本作用域(损坏兄弟条目挡好 model, 有意取舍, T8 重导入时留意)
T8 设计调整(并发保护): 主树 data 被对方 Pipeline Studio 实时使用 → T8 非侵入式: 账本写池(纯增量新文件); fragment 生成+等价验证只落 results/; s9 重建到隔离 shadow/ext 目录(CLI 支持); 验收用 s11(隔离 shadow, 顺带回填 runtime_load); s10(路径写死主树 shadow)若不可隔离则跳过并如实报; 生产切换+账本 git 提交推迟到合并时(用户协调)
Task 8: review Approved + 3 Important latent traps 进修复轮 — I-1 合成 SOURCE_MANIFEST 留池(错误 prefix+未来 retrieved_at 污染); I-2 坏时间戳三层不设防(测试/validator/latest 字符串比较); I-3 rglob 会跨到对方 web_runs 并发产出 (commits 87acc9d..19717aa)
Task 8: 数据核验(审查者真机): 10 账本 0 violation 含 sha256 实算; 坏时间戳零残留; 6 排除属实; 主树写入 32 项全在允许清单; fragment 字节级可复现等价; s11 18/18; 幂等已验
Task 8: minor (deferred): 命令日志未落盘; manifest 非原子写; _RUN_DATE_RE 不验日期合理性; --bundle-alias 未入 README; apply 成功但 exit 1 组合; 6 排除资产无完整性巡检覆盖(推迟项已点明)
Task 8: fix round 1/5 (3 addressed, 0 open — pending_manifest 写序倒转+合成清单删除+bad_timestamp 双env验证+web_runs 信任过滤(选取零漂移 18/18 血缘对照); commits 19717aa..0e7c292)
Task 8: complete (commits 87acc9d..0e7c292, review clean after 1 fix round; worktree 139 + openxsim 51; 10/16 资产入 v1, 6 排除带证据清单)
Task 8: minor (deferred, 后续硬化): ①镜像目录 retrieved_at 改取目录内最新文件 mtime(否则 314 将来入 v1 带 2026-08-09 错日期); ②_is_iso_datetime 收紧规范 T 形(py3.10/3.11 fromisoformat 分歧+空格形排序反转,均潜伏); ③不可信 bundle 拒绝加独立 report 记录(防派生行为静默改变)
=== 全部 8 任务完成, 进入全分支终审 ===

### §5.1 复审残留（非阻塞，与 M-1/M-3/M-7 同列后续清单）
- retire 的真实删除计数只在 --apply 时打印，dry-run 仍是账本推导数（建议同等计数进 dry-run）
- retire 的 symlink 防护只覆盖资产目录本身，不覆盖目录内部条目（现实唯一造链者 s9 在资产层、已被覆盖；作用域边界如实记录）
- audit 对不存在的 --library-dir 仍是 traceback/exit 1，与 violation 的 exit 1 不可区分（M-4 只覆盖了"存在但空"）
- retire docstring 遗留一句未实现的"双守卫"声称（execute 注释已如实说明实际行为）

## §6 收尾执行记录（2026-08-09 晚，owner 授权"全部执行"）
- **重导入**：301_brick/302_bowl/304_kettle/305_teacup（批量管线重取源）+ 314_cabinet（s13a/s13b 重跑）入 v1；305_teacup 侦查还原真实源（web_ 标签误导，实为 YCB 025_mug 本地复用）；**303_boombox 排除**——源 key 已从 NVIDIA 服务器消失（KeyCount=0 双重确认），池 15/16。
- **license audit（owner 指示：查明而非记 unknown）**：条款研究（Isaac Sim Additional Software and Materials License v2025-06-09 + YCB CC-BY-4.0，出处与关键引文在 _source/_license_evidence/20260809/）；关键发现：**按 S3 路径分组而非物体类型**——22 model 属 YCB（水壶/钻头/剪刀都是），仅 314_cabinet 属 NVIDIA Props；判定：内部研究两组均明确允许；再分发 NVIDIA Props 明确禁止、NVIDIA 转制 YCB USD 条款未明（干净路径=从上游 CC-BY-4.0 数据重建+署名）。15 份账本 license 全部 declared（写回脚本+分类表在 results/20260809_license_switch/）。
- **生产切换**：fragment v2（license-gate 开启零排除，15 资产）部署 + s9 重建生产影子（15 外部资产全 usable）+ **s10 四连 PASS** + **s12 5/5 PASS**。
- **顺带修复两个缺陷**：gen_fragment 关节体过滤（joint_sweep 认可，9022f03）；s12 场景选取 bug（ls -t 抓旧目录产生假 FAIL，改 scene_id 解析+日期戳目录，0bb1fe4）。
- 账本提交 235b3b9；备份：results/20260809_license_switch/backup_*。

## §7 上游资产纳入（feat/upstream-ledgers，2026-08-10）
- 架构：派生核心（catalog 自动再生成，上游权威）+ 增量层（isaacsim 表示/verification/license，本项目权威，再生成保留合并）——spec §9。
- 产物：`data/upstream_ledgers/<asset>/ledger.json`（约 130 份）+ 逐资产 SOURCE_MANIFEST（兼上游漂移检测锚）；A 线打样 USD（bottle/cabinet）登记为首批 isaacsim 表示。
- 消费端：usd_enrich 支持按账本查 USD 表示；gen_fragment/ledger_audit 零改动（audit 传 --library-dir 即可巡检新区）。

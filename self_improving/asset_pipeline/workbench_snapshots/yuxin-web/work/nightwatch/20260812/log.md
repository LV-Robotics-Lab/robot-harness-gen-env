# nightwatch 过程日志 — 2026-08-12（web studio v2）

均为新加坡时间。

- 23:45 接管。审查旧前端：登录 8811 截图（首页/详情）、读 app.py(561行)+index.html(649行)、摸 run 产物结构与日志密度。结论：全页重绘/阶段状态误导/零时间信息/日志不可用为四大硬伤。
- 23:58 用户拍板：方案1（前端重写+app.py增量接口）+ 横向stepper；随后授权通宵自主执行（只改 web 层，不动项目代码）。
- 00:05 git 快照：main 上有用户未提交改动 import_materialize.py（不碰不提交）。开分支 feat/web-studio-v2。spec 落盘 commit 47ed58e；实施计划 commit 70bbacc。
- 00:10 后端 TDD：web/tests/test_app.py 8 用例先行（fail 确认）→ 实现 stage_timeline/log/files/current + .py 白名单 → 8/8 绿。真实 duck run 冒烟合理。commit cf56098。
  - 事件1：ssh 里 pkill -f web/app.py 自匹配杀掉远程 shell（exit 255 两次）。改用 pkill -f "bin/python app[.]py" 规避。服务 pid 70856 起。
- 00:17 前端整体重写（约950行单文件）→ 部署。浏览器验证：首页/duck run 详情/七面板/文件预览弹层/日志折叠（OIDN ×51）全部正常。commit 80ce8a3。
- 00:22 审阅循环第1轮：blocker run 正常但发现 头卡run_id重复 + 无日志黑条 + 0s应显<1s → 三处修复部署。fallback_drill 证据chips正常。dark/900px 正常。
- 00:24 回放器 sim_replay.py（只写 results/web_runs/_sim_live_demo 数据）50s 全程：stepper 状态迁移/live秒表/日志流/×30折叠/跟随开关全部正常；核心验证：手动选中阶段跨4个轮询周期不被抢走、展开JSON保持、日志只追加。
  - 事件2：⑦耗时显示39m59s（copytree保留源mtime把锚点带偏）。服务端加防线：早于run开始的mtime不入时间轴锚链、耗时≤总时长、③④独立子锚点。补测试 9/9 绿。commit 4436a37。sim 复查：③8s ④0s ⑦25s 合理。删除 _sim_live_demo。
- 00:28 真实 E2E：UI 提交 place a red mug on the table → 17s 完成，全程实时跟随正常，按钮忙碌态/托管禁用/tab标题正常。
- 00:29 回归：12条历史run徽章/步骤全正常，console 零错误。
- 00:31 README 补 v2 节 commit 647927f。git push origin feat/web-studio-v2。
- 00:35 截图（light/dark）、写报告、proactive 推送。

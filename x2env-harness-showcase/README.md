# x2env Harness 组会展示页

一个完全独立的静态网站，所有代码和展示素材都位于本目录。

架构区单独标出 `x2env.compile`、`x2env.replay`、`x2env.validate` 三个公开 Skill，并给出代码和真实运行证据；验证区补充 Yuxin 本地复用、Yuxin Web 搜索和 Gujie 三维重建的接线及执行边界。

## 打开方式

在本目录运行：

```bash
python3 -m http.server 4173
```

然后访问 `http://127.0.0.1:4173`。页面没有第三方前端依赖，也不需要构建步骤。

## 证据边界

- canonical matrix v2：text / image / video / image+text 四个简单 workflow 真实通过。
- 稳定核心 can-on-plate：compile / 900-step SAPIEN replay / validate 真实通过。
- 成功环境包仍明确为 development，`sim_ready=false`、`release_qualified=false`。
- 页面展示的视频和图片是解释性入口；物理结论来自绑定的运行时报告。
- 用户保温杯图片的真实运行在 112.46 秒后以 `blocked_license` 停在 `asset.resolve`。模型理解、分割和 7.5 MB 几何生成已完成，但部署中的派生授权不覆盖该图片，所以资产没有注册，Genesis 没有启动，也没有授予可用环境。
- Yuxin 本地复用有完整成功工作流；Yuxin Web 搜索和 Gujie 重建都已有真实执行回执，但尚无完整来源 E2E pass。

主要依据见仓库中的 `repo-docs/canonical-x2env.md`、`repo-docs/canonical-x2env-testing.md`、`repo-docs/canonical-x2env-roadmap.md`、`self_improving/golden_e2e_progress/CANONICAL_MATRIX_V2_PUSH_AUDIT_20260913.md` 和 `docs/evidence/text2env-can-on-plate-e2e-20260906.md`。

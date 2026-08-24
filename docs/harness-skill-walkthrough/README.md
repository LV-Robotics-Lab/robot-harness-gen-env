# Harness Skill Walkthrough

面向项目组硕士研究生的互动导览，说明 `text2env.compile/replay/validate@1.0.0`
怎样把一句场景请求转换为可验证、可审计、最终可判断发布资格的环境包。

## 本地运行

```bash
npm install
npm run dev
```

默认开发地址为 `http://localhost:3000/`。

## 验证

```bash
npm test
npm run lint
```

网站是概念 walkthrough，不会在浏览器内执行 Python、RoboTwin 或 SAPIEN。真实契约与实现
仍以 `docs/contracts/`、`scene_gen/` 和 `self_improving/harness/` 为准。

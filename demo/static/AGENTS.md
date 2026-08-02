<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-29 | Updated: 2026-07-29 -->

# static

## Purpose（用途）
Flask demo 的浏览器前端资产。由 `demo/app.py` 服务的纯 HTML/CSS/JS；无构建步骤、无框架。

## Key Files（关键文件）
| File | Description |
|------|-------------|
| `index.html` | demo 页面标记 |
| `app.js` | 浏览器 UI 客户端逻辑 |
| `styles.css` | demo 样式 |

## Subdirectories（子目录）
| Directory | Purpose |
|-----------|---------|
| `vendor/` | 第三方库 vendoring（压缩版；无 AGENTS.md） |

## For AI Agents（给 AI agent 的提示）

### Working In This Directory（在本目录工作）
- 没有构建工具链——直接编辑文件。
- 不要加前端构建依赖；保持本区域无框架。

### Testing Requirements（测试要求）
- 静态资产无自动化测试；在浏览器加载 demo 验证改动（`python -m demo.app`）。

### Common Patterns（常见模式）
- 图标用 vendored 的 Lucide（见 `vendor/lucide.min.js`）。

## Dependencies（依赖）

### External（外部）
- Lucide 图标（vendored 在 `vendor/`）

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
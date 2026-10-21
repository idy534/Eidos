# Eidos Settings Sidebar — SVG 图标集

本图标集包含 10 个设置入口图标；每个 SVG 都可以直接作为 `<img>` 使用，也可以内联插入 React/Electron 页面。

## 图标映射

| 入口 | SVG 文件 | 上游来源 |
|---|---|---|
| 模型 | `models.svg` | Lucide / cpu |
| 网页搜索 | `web-search.svg` | Lucide / globe |
| 助理 | `assistants.svg` | Lucide / messages-square |
| 记忆 | `memory.svg` | Lucide / brain |
| Plugins | `plugins.svg` | Lucide / puzzle |
| 技能 | `skills.svg` | Lucide / book-open-check |
| 智能体 | `agents.svg` | Lucide / bot |
| MCP Servers | `mcp-servers.svg` | MCP 项目官方 favicon.svg |
| Worktrees | `worktrees.svg` | Lucide / git-branch |
| Runtime | `runtime.svg` | Lucide / square-terminal |

## UI 建议

- SVG viewBox：24 × 24；侧栏实际显示 18 × 18 px。
- SVG 颜色：`currentColor`，跟随父元素 `color`。
- 普通态：`#525A60`，选中态：`#285D45`（仅供建议，可复用现有 Eidos token）。
- 选中态主要通过背景与文字权重区分，不建议给 10 个图标分别上不同的彩色。
- MCP logo 保留官方原始路径，只有画布尺寸与前景色做了适配；`mcp-official-original.svg` 是未改动的原始版本。

```css
.settings-nav__icon {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
}
/* 外链 SVG 作为 <img> 时，currentColor 不会自动继承 DOM color；建议内联 SVG 或使用图标组件 */
.settings-nav__item { color: #525a60; }
.settings-nav__item[aria-current="page"] { color: #285d45; }
```

## 来源与许可

- Lucide 官方图标：https://github.com/lucide-icons/lucide/tree/main/icons
- Lucide 许可：https://github.com/lucide-icons/lucide/blob/main/LICENSE （ISC；部分 Feather 派生图标为 MIT）
- MCP 官方原始图标：https://github.com/modelcontextprotocol/docs/blob/main/favicon.svg （Model Context Protocol 项目官方资源）
- MCP 图标的来源及许可证说明：https://commons.wikimedia.org/wiki/File:Model_Context_Protocol_logo.svg
- Logo 属于各自所有者的标识，仅用于表明 MCP 功能或兼容性。

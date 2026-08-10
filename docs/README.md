# Eidos 文档入口

本目录按三个等级组织文档：Current、Development、Archive。

## Current

Current 文档只描述当前代码已经实现的行为。当前事实来源只有生产代码和自动化测试。

权威顺序如下：

```text
Production Code + Tests
        ↓
current-architecture.md
current-capabilities.md
current-limitations.md
architecture-overview.html
```

- [当前架构](current-architecture.md)：说明进程、边界、职责和真实调用链。
- [Runtime Git Worktree Kernel](architecture/git-worktree.md)：说明 Project、Worktree、恢复和 Sandbox 边界。
- [当前能力](current-capabilities.md)：说明当前 main 已经能做什么。
- [当前限制](current-limitations.md)：说明当前 main 还没有形成哪些完整闭环。
- [宏观架构图](architecture-overview.html)：离线可打开的当前架构视图。

## Development

- [本地开发与验证](../DEVELOPMENT.md)：说明安装、启动、测试、Runtime Bundle 和 DMG 打包。

## Archive

- [历史文档归档](archive/README.md)：说明历史 PRD、TDD、Decision、参考资料和 Phase 文档的使用边界。

Archive 只保存历史证据。Archive 文档不与 Current 文档并列作为当前行为依据。

## 维护规则

维护者修改代码后，应先更新测试，再根据代码和测试核对 Current 文档。维护者只有在文档不再反映当前行为时，才把旧设计资料移动到 Archive。历史文档可以保留当时的 schema、生命周期预算、API 和目标态描述。

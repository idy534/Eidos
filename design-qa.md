# Session 消息队列检查

final result: passed

本次检查只覆盖用户要求的 Session 输入框上方的消息队列。实现采用 Product Design 的 image-to-code 与 design-qa 检查方法。实现沿用 Eidos 的样式与图标系统。

## 视觉证据

- 用户草图：`/var/folders/vm/1k9rv81d38jgggql67_1fz_h0000gn/T/codex-clipboard-3b3c345b-ed8a-4f9a-9f8b-f88b81cddc67.png`，1466×114 像素。草图没有标明 CSS 宽度和截图密度，所以检查没有声称像素完全一致。
- 桌面截图：`/Users/xielei/.codex/visualizations/2026/10/14/01a138f2-9293-7d21-9b7d-c7139a64fc52/queued-messages-desktop.png`，2200×1600 像素，对应 1100×800 CSS 像素、2 倍密度。
- 局部截图：`/Users/xielei/.codex/visualizations/2026/10/14/01a138f2-9293-7d21-9b7d-c7139a64fc52/queued-messages-detail.png`，1600×342 像素，对应 800×171 CSS 像素、2 倍密度。该图直接由 Electron 对队列与输入框区域截图生成。
- 窄窗口截图：`/Users/xielei/.codex/visualizations/2026/10/14/01a138f2-9293-7d21-9b7d-c7139a64fc52/queued-messages-narrow.png`，840×1400 像素，对应 420×700 CSS 像素、2 倍密度。

检查在同一次对照输入中打开了草图、桌面与窄窗口截图。检查又在同一次对照输入中打开了草图与局部截图。局部检查覆盖单条消息预览、编辑和删除按钮、队列与主输入框的相接位置。窄窗口检查覆盖长消息、八条队列和打开编辑器的状态。

## 检查结果

- 字体：实现使用现有 macOS 字体栈。消息使用 14px 正文和 500 字重；操作文字使用 12px。长消息显示省略号，编辑器保留全文。
- 布局：队列和输入框共用宽度。队列位于输入框上方，两侧对齐。列表使用圆角和浅边框。普通队列在 30vh 内滚动；编辑区域在 45vh 内滚动。1100px、600px、420px 下均没有横向溢出。
- 颜色：实现使用 Eidos 的 surface、text、muted、line 和 accent 变量。编辑、删除保持灰色轻操作样式。
- 图标与图片：实现复用已有 HistoryIcon、EditIcon 和 TrashIcon，图标尺寸固定为 16px。草图没有照片或插画，所以实现没有新增位图资产。
- 文案：每行展示真实排队消息。右侧提供用户要求的编辑和删除。编辑器提供保存和取消；窄窗口保留按钮的可访问名称。
- 交互：原生测试通过了编辑、取消、删除、空队列隐藏、长队列滚动和主草稿保留。编辑器的保存、取消按钮在窄窗口中可以直接看到。页面没有 Renderer console error。

## 对照历史

第一次原生截图发现两项 P2 问题：SVG 没有固定尺寸，导致图标丢失、消息居中和操作文字换行；长消息编辑器在窄窗口中遮住了保存、取消按钮。实现固定了图标尺寸，改为左对齐消息预览，并扩大了编辑时的列表高度。第二次原生截图与最终局部截图确认这些问题已经消失。最终检查没有剩余 P0、P1、P2 项。

## 实现与验证范围

本次改动覆盖队列列表与读取 Hook、typed IPC、生成契约、Runtime Application / Repository、队列 Event / Outbox、定向测试和当前文档。实现复用 SQLite、现有 Operation 幂等记录、Pydantic、Ajv 和现有图标。实现没有增加依赖、数据库表或调度器。实现保留同 Session FIFO、模型与权限快照、引用和主草稿；删除仅针对未开始执行的消息。显式关闭 Runtime 时的既有取消语义保持不变。

本次代码与测试 Diff 约 1,800 行新增，其中 457 行是生成的 TypeScript 与 JSON Schema；另有少量文档和本报告。这个估算不包含工作区中其他任务的未提交改动。

- Runtime 队列、协议路由、原有并发生命周期定向测试：18 项通过。
- Renderer 队列、读取竞争、Composer、Run Controller、AppShell 定向测试：66 项通过。
- Session 状态纯测试：24 项通过。共享队列协议 Fixture：1 项通过。
- Main → Runtime 原生跨进程测试：1 项通过。
- 原生队列布局测试、原有 Composer 布局与滚动回归、Electron smoke：全部通过。宽、窄窗口单行输入和全选删除的历史滚动差值均为 0。
- Desktop 构建、契约检查、Ruff、Deptry、两组 pip-audit 和 git diff --check：通过。

Full Suite、打包验证与真实用户 Session 人工验收没有执行。队列编辑目前保留原有引用；界面没有修改引用或拖动排序的入口。工作区中的其他未提交改动保持原样，本任务没有创建 Commit。

草稿态补充回归：用户提供的日志和截图指出，未保存的新会话曾把 `draft-…` ID 传给 Main 的 UUID 校验。两项 AppShell 测试先复现了该调用，再验证修复后的无项目草稿与项目草稿不读取队列、不显示错误；测试也验证了 Session 物化后开始读取，以及切回草稿后卸载队列。Main 与 Runtime 的 UUID 校验保持不变。

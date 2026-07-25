# Eidos Brand Visual Identity Guide (品牌视觉标识指南)

本文档定义了桌面端个人 AI Agent 产品 **Eidos** 的品牌标识系统与应用规范。

---

## 1. Logo 设计理念 (Design Philosophy)

> **"让想法拥有可执行的形态" (Giving Ideas Actionable Form)**

**Eidos** 源自古希腊语，代表“形态、形式、理念、事物的本质形象”。

Logo 遵循 **“几何沉静 (Geometric Silence)”** 与 **“高级克制 (Restrained Intelligence)”** 的视觉设计哲学。它拒绝常规 AI 产品俗套的机器人头像、大脑回路、魔法棒与强光晕渐变，转而采用精确度极高的 1:1 建筑感几何结构。

* **负空间字母 E**：左侧坚毅的主脊与两条圆头切割槽，自然的在负空间中勾勒出标志性的字母 **E**。
* **执行切角 (Execution Chamfer)**：图形右下角采用精密的 45° 几何切角，打破封闭圆角的孤立感，表达“意图从抽象概念流向具体可执行结果”的动态过程，象征 agent 的开放响应与持续执行能力。

---

## 2. 图形含义 (Graphic Metaphor)

```text
 ┌───────────────────────────┐ 
 │  ┌─────────────────────┐  │  <- 顶部结构：用户意图 (Intent)
 │  │                     │  │
 │  └──────┐       ┌──────┘  │  <- 切槽 1：智能拆解与上下文分析
 │         │       │         │
 │  ┌──────┘       └──────┐  │  <- 中部结构：工具调用 (Tool Execution)
 │  │                     │  │
 │  └──────┐       ┌──────┘  │  <- 切槽 2：逻辑验证与推理
 │         │        \        │
 │  └──────┴─────────\───────┘  <- 45° 几何切角：可执行输出 (Actionable Result)
 └───────────────────────────┘
```

1. **晶体形化 (Crystallization)**：正方形体块象征桌面端本地 Agent 的安全、稳定与安定感。
2. **三段式律动**：上中下三层几何横杠展现 Agent 处理复杂任务时的“接收 - 推理 - 执行”三阶段。
3. **开放缺口**：右下角 45° 切角并非损伤，而是整个几何秩序的有机出口，代表“任务结晶与开放交付”。

---

## 3. 规范配色体系 (Color System)

| 颜色角色 | 色彩名称 | Hex Code | HSL Value | 适用场景 |
| :--- | :--- | :--- | :--- | :--- |
| **主色 (Primary)** | Slate Graphite (深石墨蓝) | `#0F172A` | `222°, 47%, 11%` | 浅色模式图形主体、标准 Wordmark 字体 |
| **辅助色 (Secondary)** | Titanium Light (钛白) | `#F8FAFC` | `210°, 40%, 98%` | 深色模式图形主体、高对比界面 |
| **中性过渡 (Muted Slate)** | Cool Mineral Gray (冷石墨灰) | `#64748B` | `215°, 16%, 47%` | 副标题、分割线、小尺寸文字 |
| **Mac 渐变背景 (Dark Gradient)** | Obsidian Slate (黑曜石渐变) | `#0F172A` → `#1E293B` | - | macOS Dock 应用图标底座 |

---

## 4. 文件资产目录与用途 (Asset Map)

文件存放于 `assets/branding/eidos-logo/`:

```text
assets/branding/eidos-logo/
├── concepts/
│   ├── eidos-concepts-overview.md       # 5个概念草案分析与评分矩阵
│   └── eidos-concepts-visual.svg        # 概念视觉推演图
├── svg/
│   ├── eidos-logo-primary.svg           # 主品牌矢量标志 (标准 SVG)
│   ├── eidos-logo-dark.svg              # 深色背景优化版标志
│   ├── eidos-logo-light.svg             # 浅色背景优化版标志
│   ├── eidos-logo-monochrome-black.svg  # 纯黑单色版 (适合打印/单色印刷)
│   ├── eidos-logo-monochrome-white.svg  # 纯白单色版 (适合暗色界面)
│   ├── eidos-app-icon.svg               # macOS 桌面端应用图标 (Squircle 规范)
│   └── eidos-logo-horizontal.svg        # 图标+EIDOS字标横向组合标志
├── preview/
│   ├── index.html                       # 可在浏览器中打开的交互预览页
│   └── eidos-preview-sheet.svg          # 包含所有场景测试的大图 SVG
└── README.md                            # 品牌视觉规范文档 (本文档)
```

---

## 5. 使用规范 (Usage Guidelines)

### A. 安全留白 (Clearspace Rule)

为了保持品牌标识的独立性与高级感，标识四周必须保留至少 **X** 的安全留白空间（其中 X 为 Logo 宽度与高度的 **1/4**）。

```text
  +-----------------------------+
  |              X              |
  |    +-------------------+    |
  |    |                   |    |
  |  X |    EIDOS LOGO     | X  |
  |    |                   |    |
  |    +-------------------+    |
  |              X              |
  +-----------------------------+
```

### B. 最小使用尺寸 (Minimum Sizes)

* **独立标志 (Vector Mark)**：最小尺寸为 **16×16 px**。
* **组合标志 (Horizontal Mark)**：最小宽度为 **120 px**。
* **macOS Dock 图标**：标准应用图标尺寸为 **512×512 px** (系统缩放到 64px~128px 保持超高清物理显示)。

---

## 6. 禁止使用方式 (Prohibited Uses)

* **禁止扭曲拉伸**：不得改变 1:1 的正方形高宽比例。
* **禁止随意变色**：不得使用高饱和霓虹色、彩虹渐变或未经授权的强光晕。
* **禁止擅自旋转**：缺口方向必须固定位于右下角（45° Chamfer）。
* **禁止添加杂乱图层**：不得在图形内部叠加多余的线条、发光点或阴影。
* **禁止模糊边缘**：矢量路径必须精准锚定像素网格，避免出现反锯齿混浊。

---

## 7. 最终选择该方案的原因 (Selection Rationale)

1. **极强的识别度**：在同类 AI 桌面产品中，避开了通俗的对话气泡与星芒，以建筑感极强的负空间 E + Chamfer 创造了极具辨识度的超级符号。
2. **完美契合 macOS 视觉气质**：低饱和度深石墨蓝与钛白配色，继承了 Apple / Linear 级产品的克制与精致。
3. **极限尺寸下的卓越表现**：在 16×16px 像素网格测试中，图形边缘清晰度保持 100%，无糊团与线条混淆。
4. **耐看且具备长远商业生命力**：纯粹的几何秩序不会随设计潮流的变迁而过时，适合作为 Eidos 的长期产品品牌标识。

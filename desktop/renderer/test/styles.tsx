import { useState } from "react";
import { createRoot } from "react-dom/client";
import "react-diff-view/style/index.css";
import "katex/dist/katex.min.css";
import { Button } from "../src/components/Button.js";
import { ApprovalComposer } from "../src/components/ApprovalComposer.js";
import { MarkdownContent } from "../src/components/MarkdownContent.js";
import { TurnResults } from "../src/components/TurnResults.js";
import type { ApprovalRequest, Item, Run } from "../src/contracts.js";
import "../src/styles.css";

type Sample = "controls" | "selected" | "content" | "layout" | "menu" | "scrollbars";
const noop = () => {};
const run = {
  id: "style-run", sessionId: "style-session", status: "waiting_approval",
  modelStepCount: 0, createdAt: 1, updatedAt: 1, allowedActions: ["approve", "reject"],
} satisfies Run;
const approval = {
  id: "style-approval", sessionId: run.sessionId, runId: run.id, itemId: "style-item",
  toolCallId: "style-tool", kind: "file_change", summary: "Write document", diff: "+ document",
} satisfies ApprovalRequest;

const textChange = {
  id: "style-change", sessionId: run.sessionId, runId: run.id, ordinal: 1,
  kind: "file_change", status: "completed", createdAt: 1,
  toolCall: {
    id: "style-change-tool", itemId: "style-change", modelStepIndex: 1, batchOrder: 0,
    providerCallId: "style-call", toolName: "apply_patch", status: "completed", startedAt: 1,
    changeDiff: "diff --git a/main.ts b/main.ts\n--- a/main.ts\n+++ b/main.ts\n@@ -1 +1 @@\n-old\n+new\n",
  },
} satisfies Item;

function StyleSamples() {
  const [sample, setSample] = useState<Sample>("controls");
  const [dock, setDock] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [menu, setMenu] = useState("session-menu-popover");
  Object.assign(window, {
    showStyleSample: setSample,
    setStyleDock: setDock,
    setStyleSidebarCollapsed: setCollapsed,
    setStyleMenu: setMenu,
  });
  if (sample === "controls") return <div id="samples">
    <div className="sample-row">
      <Button id="primary" variant="primary">提交</Button>
      <Button id="danger" variant="danger">删除</Button>
      <Button id="disabled" variant="primary" disabled>不可用</Button>
      <button id="icon" className="icon-button" aria-label="工具">＋</button>
    </div>
    <div className="sample-row">
      <div className="plan-dock-execute-row"><Button id="plan-submit" variant="primary">执行计划</Button></div>
      <div className="clarification-footer"><Button id="clarification-submit" variant="primary">提交回答</Button></div>
    </div>
    <div className="sample-row">
      <button id="model-trigger" className="reasoning-selector__trigger">模型</button>
      <button id="mode-trigger" className="approval-mode-selector__trigger">审批模式</button>
    </div>
    <ApprovalComposer run={run} approval={approval} onApprove={noop} onReject={noop} />
    <ApprovalComposer run={run} approval={{ ...approval, id: "danger-approval", requiresHumanConfirmation: true, riskWarning: "高危操作" }} onApprove={noop} onReject={noop} />
  </div>;
  if (sample === "selected") return <div id="samples">
    <div id="selection-samples" className="sample-row">
      <ul className="session-list"><li className="session-item"><button id="selected-session" className="selected">当前会话</button></li></ul>
      <button id="selected-git" className="git-scope-tab" aria-selected="true">当前任务</button>
      <button id="selected-project" className="project-picker-item project-picker-item--selected">当前项目</button>
      <div id="selected-tree" className="workspace-tree-row is-selected">当前文件</div>
      <label id="selected-model" className="reasoning-selector__model-choice is-selected">当前模型</label>
      <label id="selected-mode" className="approval-mode-selector__choice is-selected">审批方式</label>
      <div className="skill-categories"><button id="selected-skill" aria-pressed="true">个人</button></div>
      <div id="selected-handoff" className="handoff-mode-card is-selected">本地执行</div>
      <button id="selected-clarification" className="clarification-option clarification-option--selected">当前选项</button>
      <button id="selected-mcp" className="mcp-create-type-button is-active">本地 MCP</button>
    </div>
  </div>;
  if (sample === "content") return <div id="content-samples">
    <div className="git-review-stats" id="git-stat-reference"><span className="git-review-stat">+682</span><span className="git-review-stat">-4</span></div>
    <TurnResults run={run} items={[textChange]} />
    <MarkdownContent className="eidos-chat-prose" content={"# 标题\n\n正文中的 `inline` 代码。\n\n第二段文字。\n\n- 第一项\n- 第二项\n\n```text\nfirst line\nsecond line\n```"} />
    <div className="user-message-bubble"><p className="eidos-chat-prose">用户消息</p></div>
    <MarkdownContent content={"非聊天正文。\n\n第二段。"} />
    <div className="workspace-text-preview"><pre><span className="workspace-text-line">文件正文</span></pre></div>
    <div className="mermaid-svg-wrapper"><svg><foreignObject width="200" height="40"><div><p>图表文字</p></div></foreignObject></svg></div>
  </div>;
  if (sample === "menu") return <div className={`${menu} fixture-menu`}><button id="menu-item">菜单操作</button></div>;
  if (sample === "scrollbars") return <div id="scrollbar-samples">
    {["feed", "workspace-tree-list", "settings-content-col", "input-picker__list", "input-reference-list", "git-review-files", "git-file-diff-scroll", "environment-popover__body", "agent-list", "mcp-create-body", "skill-detail-dialog", "code-block-content", "diff-file-body"].map((className, index) =>
      <div key={className} id={`scrollbar-${index}`} className={`scrollbar-sample ${className}`}><div>{className}</div></div>,
    )}
  </div>;
  return <div className={`workbench${collapsed ? " workbench--sidebar-collapsed" : ""}`}>
    <aside className="sidebar" />
    <div className="workspace">
      <div className={`workspace-body ${dock ? "workspace-body--with-dock" : "workspace-body--session-centered"}`}>
        <div className="workspace-main-column">
          <header className="session-header"><button className="workspace-dock-toggle">工作区</button></header>
          <div className="workspace-content"><div className="workspace-main">
            <div className="feed"><article className="feed-item feed-item--assistant">回答</article></div>
            <form className="composer"><textarea aria-label="输入" defaultValue="内容" /></form>
          </div></div>
        </div>
        {dock && <><div className="workspace-dock-resize-handle" /><aside className="workspace-dock"><header className="workspace-dock__header">工作区</header></aside></>}
      </div>
    </div>
  </div>;
}

createRoot(document.getElementById("root")!).render(<StyleSamples />);

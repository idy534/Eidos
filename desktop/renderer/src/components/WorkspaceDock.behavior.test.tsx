import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  WorkspaceDock,
  type WorkspaceToolKind,
} from "./WorkspaceDock.js";

function Harness() {
  const [tabs, setTabs] = useState<WorkspaceToolKind[]>(["review", "files"]);
  const [active, setActive] = useState<WorkspaceToolKind>("review");
  const [expanded, setExpanded] = useState(false);

  function addTool(tool: WorkspaceToolKind) {
    setTabs((current) => current.includes(tool) ? current : [...current, tool]);
    setActive(tool);
  }

  function closeTool(tool: WorkspaceToolKind) {
    setTabs((current) => {
      const next = current.filter((item) => item !== tool);
      if (active === tool && next[0]) setActive(next[0]);
      return next;
    });
  }

  return (
    <WorkspaceDock
      activeTool={active}
      availableTools={["review", "terminal", "files"]}
      expanded={expanded}
      openTools={tabs}
      onAddTool={addTool}
      onClose={() => undefined}
      onCloseTool={closeTool}
      onSelectTool={setActive}
      onToggleExpanded={() => setExpanded((current) => !current)}
      review={<div>审阅内容</div>}
      terminal={<div>终端内容</div>}
      files={<div>文件内容</div>}
    />
  );
}

describe("WorkspaceDock", () => {
  it("adds and switches tool windows while keeping inactive content mounted", () => {
    render(<Harness />);

    expect(screen.getByRole("tab", { name: "审阅" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("审阅内容")).toBeVisible();
    expect(screen.getByText("文件内容")).toBeInTheDocument();
    expect(screen.getByText("文件内容").closest("[role=tabpanel]")).toHaveAttribute("hidden");

    fireEvent.click(screen.getByRole("button", { name: "添加窗口" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "终端" }));

    expect(screen.getByRole("tab", { name: "终端" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("终端内容")).toBeVisible();

    fireEvent.click(screen.getByRole("tab", { name: "文件" }));
    expect(screen.getByText("文件内容")).toBeVisible();
    expect(screen.getByText("终端内容")).toBeInTheDocument();
  });

  it("supports workspace expansion and closing a tool window", () => {
    const { container } = render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "展开工作区" }));
    expect(container.querySelector(".workspace-dock")).toHaveClass("workspace-dock--expanded");
    expect(screen.getByRole("button", { name: "收缩工作区" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "关闭文件" }));
    expect(screen.queryByRole("tab", { name: "文件" })).not.toBeInTheDocument();
  });
});

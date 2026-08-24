import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  WorkspaceDock,
  WorkspaceDockToggle,
  type WorkspaceTab,
  type WorkspaceToolKind,
} from "./WorkspaceDock.js";

function Harness({ initialTabs = [
  { id: "review", kind: "review" },
  { id: "files", kind: "files" },
] }: { initialTabs?: WorkspaceTab[] }) {
  const [tabs, setTabs] = useState<WorkspaceTab[]>(initialTabs);
  const [active, setActive] = useState<string | undefined>(initialTabs[0]?.id);
  const [expanded, setExpanded] = useState(false);

  function addTool(tool: WorkspaceToolKind) {
    const tab = tool === "terminal"
      ? { id: `terminal-${tabs.length + 1}`, kind: tool, title: `终端 ${tabs.length + 1}` }
      : { id: tool, kind: tool };
    setTabs((current) => current.some((item) => item.kind === tool && tool !== "terminal")
      ? current
      : [...current, tab]);
    setActive(tab.id);
  }

  function closeTab(id: string) {
    setTabs((current) => {
      const index = current.findIndex((item) => item.id === id);
      const next = current.filter((item) => item.id !== id);
      if (active === id) setActive(next[Math.min(index, next.length - 1)]?.id);
      return next;
    });
  }

  return (
      <WorkspaceDock
      activeTabId={active}
      availableTools={["review", "terminal", "files"]}
      expanded={expanded}
      openTabs={tabs}
      onAddTool={addTool}
      onCloseTab={closeTab}
      onSelectTab={setActive}
      onToggleExpanded={() => setExpanded((current) => !current)}
      renderTab={(tab) => <div>{tab.kind === "review" ? "审阅内容" : tab.kind === "terminal" ? "终端内容" : "文件内容"}</div>}
    />
  );
}

describe("WorkspaceDock", () => {
  it("shows a picker when no tool tab is open", () => {
    render(<Harness initialTabs={[]} />);

    expect(screen.getByRole("status")).toHaveTextContent("打开工作区");
    expect(screen.getByRole("button", { name: "审阅" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "终端" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "文件" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "终端" }));
    expect(screen.getByRole("tab", { name: "终端 1" })).toHaveAttribute("aria-selected", "true");
  });

  it("adds and switches tool windows while keeping inactive content mounted", () => {
    render(<Harness />);

    expect(screen.getByRole("tab", { name: "审阅" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("审阅内容")).toBeVisible();
    expect(screen.getByText("文件内容")).toBeInTheDocument();
    expect(screen.getByText("文件内容").closest("[role=tabpanel]")).toHaveAttribute("hidden");

    fireEvent.click(screen.getByRole("button", { name: "添加窗口" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "终端" }));

    expect(screen.getByRole("tab", { name: "终端 3" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("终端内容")).toBeVisible();

    fireEvent.click(screen.getByRole("tab", { name: "文件" }));
    expect(screen.getByText("文件内容")).toBeVisible();
    expect(screen.getByText("终端内容")).toBeInTheDocument();
  });

  it("opens the add-window menu on the first click", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "添加窗口" }));

    expect(screen.getByRole("menu", { name: "添加窗口" })).toBeVisible();
  });

  it("allows multiple terminal tabs while keeping review and files singletons", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "添加窗口" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "终端" }));
    fireEvent.click(screen.getByRole("button", { name: "添加窗口" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "终端" }));

    expect(screen.getAllByRole("tab")).toHaveLength(4);
    expect(screen.getByRole("tab", { name: "终端 3" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "终端 4" })).toBeInTheDocument();
  });

  it("supports workspace expansion and closing a tool window", () => {
    const { container } = render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "展开工作区" }));
    expect(container.querySelector(".workspace-dock")).toHaveClass("workspace-dock--expanded");
    expect(screen.getByRole("button", { name: "收缩工作区" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "收缩工作区" }).querySelector("svg"))
      .toHaveAttribute("data-icon", "exit-fullscreen");
    expect(screen.queryByRole("button", { name: "关闭工作区工具" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "关闭文件" }));
    expect(screen.queryByRole("tab", { name: "文件" })).not.toBeInTheDocument();
  });

  it("uses paired diagonal-arrow glyphs for workspace expansion and collapse", () => {
    render(<Harness />);

    const expand = screen.getByRole("button", { name: "展开工作区" });
    expect(expand.querySelector("svg")).toHaveAttribute("viewBox", "0 0 24 24");
    expect(expand.querySelector("path"))
      .toHaveAttribute("d", "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7");

    fireEvent.click(expand);
    expect(screen.getByRole("button", { name: "收缩工作区" }).querySelector("path"))
      .toHaveAttribute("d", "M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7");
  });

  it("uses a rounded workspace toggle glyph", () => {
    render(<WorkspaceDockToggle open={false} onClick={() => undefined} />);

    const toggle = screen.getByRole("button", { name: "打开工作区工具" });
    expect(toggle.querySelector("rect")).toHaveAttribute("rx", "3");
    expect(toggle.querySelector("path")).toHaveAttribute("stroke-linecap", "round");
  });

  it("uses a diff document glyph for review", () => {
    render(<Harness initialTabs={[]} />);

    const review = screen.getByRole("button", { name: "审阅" });
    expect(review.querySelector("path"))
      .toHaveAttribute("d", "M5 2.5h7l3 3v12H5zM12 2.5v3h3M7.5 10h4M9.5 8v4M7.5 14h4");
  });

  it("uses a left-tab folder glyph for files", () => {
    render(<Harness initialTabs={[]} />);

    expect(screen.getByRole("button", { name: "文件" }).querySelector("path"))
      .toHaveAttribute("d", "M2.5 5h5l1.5 2h8.5v9.5h-15zM2.5 7h15");
  });
});

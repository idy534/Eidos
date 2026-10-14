import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Session } from "../../../shared/domain-contracts.js";
import { ForkConversationDialog } from "./ForkConversationDialog.js";

const baseSession: Session = {
  id: "session-1",
  workspaceRoot: "/workspace",
  executionMode: "local",
  taskStatus: "completed",
  createdAt: 1000,
  updatedAt: 2000,
};

describe("ForkConversationDialog", () => {
  it("renders both options for standard workspace session with polished cards", () => {
    const onTargetChange = vi.fn();
    const onConfirm = vi.fn();
    const onCancel = vi.fn();

    render(
      <ForkConversationDialog
        open
        session={baseSession}
        forkTarget="same_workspace"
        onTargetChange={onTargetChange}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByRole("alertdialog", { name: "从这里分叉" })).toBeInTheDocument();
    expect(screen.getByText("当前目录")).toBeInTheDocument();
    expect(screen.getByText("独立工作树")).toBeInTheDocument();
    expect(screen.getByText("共享文件")).toBeInTheDocument();
    expect(screen.getByText("隔离分支")).toBeInTheDocument();

    const sameWorkspaceRadio = screen.getByRole("radio", { name: "在当前目录继续，两条会话共享文件。" });
    const newWorktreeRadio = screen.getByRole("radio", { name: "在独立工作树继续。" });

    expect(sameWorkspaceRadio).toBeChecked();
    expect(newWorktreeRadio).not.toBeChecked();

    fireEvent.click(newWorktreeRadio);
    expect(onTargetChange).toHaveBeenCalledWith("new_worktree");

    fireEvent.click(screen.getByRole("button", { name: "创建分叉" }));
    expect(onConfirm).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("renders only independent worktree option when session is in worktree mode", () => {
    const worktreeSession: Session = {
      ...baseSession,
      executionMode: "worktree",
    };

    render(
      <ForkConversationDialog
        open
        session={worktreeSession}
        forkTarget="new_worktree"
        onTargetChange={vi.fn()}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(1);
    expect(radios[0]).toBeChecked();
    expect(screen.getByText("独立工作树")).toBeInTheDocument();
    expect(screen.queryByText("当前目录")).not.toBeInTheDocument();
  });

  it("closes on Escape key when not busy", () => {
    const onCancel = vi.fn();

    render(
      <ForkConversationDialog
        open
        session={baseSession}
        forkTarget="same_workspace"
        onTargetChange={vi.fn()}
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("displays error message if provided", () => {
    render(
      <ForkConversationDialog
        open
        session={baseSession}
        forkTarget="same_workspace"
        error="创建分叉失败，请检查工作区"
        onTargetChange={vi.fn()}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("创建分叉失败，请检查工作区");
  });
});

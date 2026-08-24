import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HandoffDialog } from "./HandoffDialog.js";

describe("HandoffDialog 工作环境配置", () => {
  it("使用中文展示本地和新建本地工作树", () => {
    render(
      <HandoffDialog
        open
        currentMode="local"
        currentBranch="main"
        branches={["main", "feature/review"]}
        changedFileCount={3}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "更改工作环境" })).toBeInTheDocument();
    expect(screen.getByText("本地")).toBeInTheDocument();
    expect(screen.getByText("新建本地工作树")).toBeInTheDocument();
    expect(screen.getByText("从本地分支 main 创建独立工作树")).toBeInTheDocument();
    expect(screen.getByText("3 个文件的当前修改会一起迁移")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建并切换" })).toBeEnabled();
    expect(screen.queryByText(/Hand off|Local|Worktree|Starting Branch|Include current changes/i))
      .not.toBeInTheDocument();
  });

  it("选择本地后可以选择并切换本地分支", () => {
    const onConfirm = vi.fn();
    render(
      <HandoffDialog
        open
        currentMode="local"
        currentBranch="main"
        branches={["main", "feature/review"]}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    fireEvent.click(screen.getByRole("radio", { name: /^本地/ }));
    const branch = screen.getByRole("combobox", { name: "本地分支" });
    expect(screen.getByRole("button", { name: "当前环境" })).toBeDisabled();

    fireEvent.change(branch, { target: { value: "feature/review" } });
    fireEvent.click(screen.getByRole("button", { name: "切换到 feature/review" }));

    expect(onConfirm).toHaveBeenCalledWith("local", "feature/review");
  });

  it("有关联工作树时明确表示返回已有工作树", () => {
    render(
      <HandoffDialog
        open
        currentMode="local"
        currentBranch="main"
        branches={["main"]}
        associatedWorktreeId="worktree-a"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByText("已有本地工作树")).toBeInTheDocument();
    expect(screen.getByText("返回这个会话原有的独立工作树")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回工作树" })).toBeEnabled();
  });

  it("从工作树切换回本地时说明会同步当前 Git 状态", () => {
    const onConfirm = vi.fn();
    render(
      <HandoffDialog
        open
        currentMode="worktree"
        currentBranch={null}
        branches={[]}
        associatedWorktreeId="worktree-a"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByText("当前工作树的 Git 状态会安全同步到本地")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "切换到本地" }));
    expect(onConfirm).toHaveBeenCalledWith("local", undefined);
  });
});

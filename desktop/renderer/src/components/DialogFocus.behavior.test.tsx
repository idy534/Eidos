import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "./settings/ConfirmDialog.js";
import { ApprovalFeedbackDialog } from "./ApprovalFeedbackDialog.js";
import type { ApprovalRequest } from "../contracts.js";

const mockApproval: ApprovalRequest = {
  id: "app-1",
  sessionId: "session-1",
  runId: "run-1",
  itemId: "item-1",
  toolCallId: "tc-1",
  kind: "command_execution",
  summary: "Delete database table",
  command: "drop table users",
  cwd: "/workspace",
  timeoutSeconds: 30,
};

describe("DialogFocus & Trap behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("ConfirmDialog destructive initially focuses Cancel button and wraps Tab focus", async () => {
    const user = userEvent.setup();
    const onCancelSpy = vi.fn();
    const onConfirmSpy = vi.fn();

    render(
      <div>
        <button type="button" id="trigger-btn">Open Dialog</button>
        <ConfirmDialog
          open={true}
          title="确定删除项目？"
          description="此操作不可撤销"
          isDestructive={true}
          onCancel={onCancelSpy}
          onConfirm={onConfirmSpy}
        />
      </div>,
    );

    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute("aria-modal", "true");

    const cancelBtn = screen.getByRole("button", { name: "取消" });
    const confirmBtn = screen.getByRole("button", { name: "确认" });

    // Wait for initial focus timer (16ms)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    expect(cancelBtn).toHaveFocus();

    // Tab moves focus to Confirm
    await user.keyboard("{Tab}");
    expect(confirmBtn).toHaveFocus();

    // Tab wraps around to Cancel
    await user.keyboard("{Tab}");
    expect(cancelBtn).toHaveFocus();

    // Shift+Tab wraps back to Confirm
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(confirmBtn).toHaveFocus();
  });

  it("ConfirmDialog non-destructive initially focuses Confirm button", async () => {
    render(
      <ConfirmDialog
        open={true}
        title="保存变更？"
        description="保存所有更改"
        isDestructive={false}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    const confirmBtn = screen.getByRole("button", { name: "确认" });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    expect(confirmBtn).toHaveFocus();
  });

  it("Escape closes dialog when not busy, but does not close when busy", async () => {
    const user = userEvent.setup();
    const onCancelSpy = vi.fn();

    const { rerender } = render(
      <ConfirmDialog
        open={true}
        busy={true}
        title="Deleting..."
        description="Please wait"
        onCancel={onCancelSpy}
        onConfirm={vi.fn()}
      />,
    );

    await user.keyboard("{Escape}");
    expect(onCancelSpy).not.toHaveBeenCalled();

    rerender(
      <ConfirmDialog
        open={true}
        busy={false}
        title="Deleting..."
        description="Please wait"
        onCancel={onCancelSpy}
        onConfirm={vi.fn()}
      />,
    );

    await user.keyboard("{Escape}");
    expect(onCancelSpy).toHaveBeenCalledTimes(1);
  });

  it("ApprovalFeedbackDialog renders role=dialog, aria-modal=true, and alert role for errors", async () => {
    render(
      <ApprovalFeedbackDialog
        approval={mockApproval}
        busy={false}
        error="反馈不能超过 1024 字节"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute("aria-modal", "true");

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("反馈不能超过 1024 字节");
  });
});

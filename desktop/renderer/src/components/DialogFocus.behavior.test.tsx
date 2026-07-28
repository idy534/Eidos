import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import { useRef, useState } from "react";
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

function controlAnimationFrames() {
  let nextId = 1;
  const callbacks = new Map<number, FrameRequestCallback>();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    const id = nextId++;
    callbacks.set(id, callback);
    return id;
  });
  const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
    callbacks.delete(id);
  });
  return {
    cancel,
    pending: () => callbacks.size,
    flush: () => {
      const queued = [...callbacks.values()];
      callbacks.clear();
      act(() => queued.forEach((callback) => callback(0)));
    },
  };
}

describe("DialogFocus & Trap behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each(["confirm", "approval"] as const)(
    "%s dialog initial closed mount does not move focus or schedule work",
    (kind) => {
      const requestFrame = vi.spyOn(window, "requestAnimationFrame");
      const focus = vi.spyOn(HTMLElement.prototype, "focus");

      function Harness() {
        const fallbackRef = useRef<HTMLDivElement>(null);
        return (
          <>
            <input aria-label="External input" autoFocus />
            <div ref={fallbackRef}>Workspace</div>
            {kind === "confirm" ? (
              <ConfirmDialog
                open={false}
                title="Title"
                description="Description"
                getFallbackFocus={() => fallbackRef.current}
                onCancel={vi.fn()}
                onConfirm={vi.fn()}
              />
            ) : (
              <ApprovalFeedbackDialog
                approval={null}
                getFallbackFocus={() => fallbackRef.current}
                onCancel={vi.fn()}
                onConfirm={vi.fn()}
              />
            )}
          </>
        );
      }

      render(<Harness />);

      expect(screen.getByRole("textbox", { name: "External input" })).toHaveFocus();
      expect(requestFrame).not.toHaveBeenCalled();
      expect(focus).toHaveBeenCalledTimes(1);
      expect(screen.getByText("Workspace")).not.toHaveAttribute("tabindex");
    },
  );

  it("ConfirmDialog destructive initially focuses Cancel button and wraps Tab focus", async () => {
    const frames = controlAnimationFrames();
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

    frames.flush();
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

  it("ConfirmDialog non-destructive initially focuses Confirm button via requestAnimationFrame", async () => {
    const frames = controlAnimationFrames();
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

    frames.flush();
    expect(confirmBtn).toHaveFocus();
  });

  it.each(["cancel", "escape", "confirm"] as const)(
    "ConfirmDialog real %s close restores the real trigger",
    async (action) => {
      const frames = controlAnimationFrames();
      const user = userEvent.setup();

      function Harness() {
        const [open, setOpen] = useState(false);
        const fallbackRef = useRef<HTMLDivElement>(null);
        return (
          <>
            <button type="button" onClick={() => setOpen(true)}>Open Dialog</button>
            <div ref={fallbackRef} tabIndex={-1}>Workspace</div>
            <ConfirmDialog
              open={open}
              title="Confirm"
              description="Description"
              getFallbackFocus={() => fallbackRef.current}
              onCancel={() => setOpen(false)}
              onConfirm={() => setOpen(false)}
            />
          </>
        );
      }

      render(<Harness />);
      const trigger = screen.getByRole("button", { name: "Open Dialog" });
      await user.click(trigger);
      frames.flush();
      expect(screen.getByRole("button", { name: "确认" })).toHaveFocus();
      if (action === "cancel") await user.click(screen.getByRole("button", { name: "取消" }));
      if (action === "confirm") await user.click(screen.getByRole("button", { name: "确认" }));
      if (action === "escape") await user.keyboard("{Escape}");
      expect(trigger).toHaveFocus();
    },
  );

  it("ConfirmDialog disconnected trigger restores explicit fallback and missing fallback is safe", () => {
    const frames = controlAnimationFrames();
    const fallback = document.createElement("div");
    fallback.tabIndex = -1;
    document.body.append(fallback);
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const getFallbackFocus = vi.fn(() => fallback);
    const props = {
      title: "Confirm",
      description: "Description",
      getFallbackFocus,
      onCancel: vi.fn(),
      onConfirm: vi.fn(),
    };
    const { rerender } = render(<ConfirmDialog {...props} open={false} />);
    rerender(<ConfirmDialog {...props} open />);
    frames.flush();
    trigger.remove();
    rerender(<ConfirmDialog {...props} open={false} />);
    expect(fallback).toHaveFocus();

    fallback.remove();
    expect(() => {
      rerender(<ConfirmDialog {...props} open />);
      rerender(<ConfirmDialog {...props} open={false} />);
    }).not.toThrow();
  });

  it("ConfirmDialog quick close and unmount cancel pending RAF", () => {
    const frames = controlAnimationFrames();
    const props = {
      title: "Confirm",
      description: "Description",
      onCancel: vi.fn(),
      onConfirm: vi.fn(),
    };
    const { rerender, unmount } = render(<ConfirmDialog {...props} open={false} />);
    rerender(<ConfirmDialog {...props} open />);
    expect(frames.pending()).toBe(1);
    rerender(<ConfirmDialog {...props} open={false} />);
    expect(frames.cancel).toHaveBeenCalledTimes(1);
    expect(frames.pending()).toBe(0);
    rerender(<ConfirmDialog {...props} open />);
    unmount();
    expect(frames.cancel).toHaveBeenCalledTimes(2);
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

  it("Backdrop click calls onCancel when not busy", () => {
    const onCancelSpy = vi.fn();

    const { container } = render(
      <ConfirmDialog
        open={true}
        busy={false}
        title="Confirm"
        description="Details"
        onCancel={onCancelSpy}
        onConfirm={vi.fn()}
      />,
    );

    const backdrop = container.querySelector(".modal-backdrop");
    expect(backdrop).toBeInTheDocument();

    fireEvent.click(backdrop!);
    expect(onCancelSpy).toHaveBeenCalledTimes(1);
  });

  it("Backdrop click is ignored when busy", () => {
    const onCancelSpy = vi.fn();

    const { container } = render(
      <ConfirmDialog
        open={true}
        busy={true}
        title="Confirm"
        description="Details"
        onCancel={onCancelSpy}
        onConfirm={vi.fn()}
      />,
    );

    const backdrop = container.querySelector(".modal-backdrop");
    fireEvent.click(backdrop!);
    expect(onCancelSpy).not.toHaveBeenCalled();
  });

  it("Destructive ConfirmDialog ignores backdrop click", () => {
    const onCancel = vi.fn();
    const { container } = render(
      <ConfirmDialog
        open
        isDestructive
        title="Delete"
        description="Cannot undo"
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />,
    );
    fireEvent.click(container.querySelector(".modal-backdrop")!);
    expect(onCancel).not.toHaveBeenCalled();
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

  it("ApprovalFeedbackDialog opens on textarea, traps Tab, and cancel restores trigger", async () => {
    const frames = controlAnimationFrames();
    const user = userEvent.setup();

    function Harness() {
      const [approval, setApproval] = useState<ApprovalRequest | null>(null);
      const fallbackRef = useRef<HTMLDivElement>(null);
      return (
        <>
          <button type="button" onClick={() => setApproval(mockApproval)}>Reject operation</button>
          <div ref={fallbackRef} tabIndex={-1}>Workspace</div>
          <ApprovalFeedbackDialog
            approval={approval}
            getFallbackFocus={() => fallbackRef.current}
            onCancel={() => setApproval(null)}
            onConfirm={() => setApproval(null)}
          />
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Reject operation" });
    await user.click(trigger);
    frames.flush();
    const textarea = screen.getByRole("textbox", { name: "拒绝原因（可选）" });
    expect(textarea).toHaveFocus();
    textarea.focus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(screen.getByRole("button", { name: "拒绝并反馈" })).toHaveFocus();
    await user.keyboard("{Tab}");
    expect(textarea).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(trigger).toHaveFocus();
  });

  it("ApprovalFeedbackDialog busy state blocks Escape and backdrop and disables all controls", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const { container, rerender } = render(
      <ApprovalFeedbackDialog
        approval={mockApproval}
        busy
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />,
    );
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝并反馈" })).toBeDisabled();
    await user.keyboard("{Escape}");
    fireEvent.click(container.querySelector(".modal-backdrop")!);
    expect(onCancel).not.toHaveBeenCalled();

    rerender(
      <ApprovalFeedbackDialog
        approval={mockApproval}
        busy={false}
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />,
    );
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("ApprovalFeedbackDialog idle backdrop closes and rapid close/unmount cancel RAF", () => {
    const frames = controlAnimationFrames();
    const onCancel = vi.fn();
    const props = {
      onCancel,
      onConfirm: vi.fn(),
    };
    const { container, rerender, unmount } = render(
      <ApprovalFeedbackDialog {...props} approval={null} />,
    );
    rerender(<ApprovalFeedbackDialog {...props} approval={mockApproval} />);
    expect(frames.pending()).toBe(1);
    fireEvent.click(container.querySelector(".modal-backdrop")!);
    expect(onCancel).toHaveBeenCalledTimes(1);
    rerender(<ApprovalFeedbackDialog {...props} approval={null} />);
    expect(frames.cancel).toHaveBeenCalledTimes(1);
    rerender(<ApprovalFeedbackDialog {...props} approval={mockApproval} />);
    unmount();
    expect(frames.cancel).toHaveBeenCalledTimes(2);
  });

  it("Disconnecting trigger element before close is safe", async () => {
    let showTrigger = true;
    const { rerender } = render(
      <div>
        {showTrigger && <button type="button" id="trigger-btn" autoFocus>Trigger</button>}
        <ConfirmDialog
          open={true}
          title="Title"
          description="Desc"
          onCancel={vi.fn()}
          onConfirm={vi.fn()}
        />
      </div>,
    );

    // Unmount trigger button while dialog is open
    showTrigger = false;
    rerender(
      <div>
        {showTrigger && <button type="button" id="trigger-btn">Trigger</button>}
        <ConfirmDialog
          open={true}
          title="Title"
          description="Desc"
          onCancel={vi.fn()}
          onConfirm={vi.fn()}
        />
      </div>,
    );

    // Close dialog
    expect(() => {
      rerender(
        <div>
          <ConfirmDialog
            open={false}
            title="Title"
            description="Desc"
            onCancel={vi.fn()}
            onConfirm={vi.fn()}
          />
        </div>,
      );
    }).not.toThrow();
  });

  it("Quick unmount before requestAnimationFrame fires cancels pending animation frame cleanly", () => {
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame");

    const { unmount } = render(
      <ConfirmDialog
        open={true}
        title="Title"
        description="Desc"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    // Unmount synchronously before RAF frame executes
    unmount();

    expect(cancelSpy).toHaveBeenCalled();
  });

  it.each([
    ["enabled Composer", true, false, "Composer"],
    ["Workspace when Composer is disabled", true, true, "Workspace"],
    ["no target when Composer and Workspace are unavailable", false, false, null],
  ] as const)(
    "ApprovalFeedbackDialog closes from a removed Approval card to %s",
    async (_variant, renderFallbacks, composerDisabled, expectedFocus) => {
      const frames = controlAnimationFrames();
      const user = userEvent.setup();
      let removeApprovalCard = () => {};

      function Harness() {
        const [approval, setApproval] = useState<ApprovalRequest | null>(null);
        const [cardVisible, setCardVisible] = useState(true);
        const composerRef = useRef<HTMLTextAreaElement>(null);
        const workspaceRef = useRef<HTMLElement>(null);
        removeApprovalCard = () => setCardVisible(false);
        return (
          <main>
            {cardVisible && (
              <article aria-label="Shell Approval">
                <p>{mockApproval.summary}</p>
                <button type="button" onClick={() => setApproval(mockApproval)}>
                  Reject
                </button>
              </article>
            )}
            {renderFallbacks && (
              <>
                <textarea
                  ref={composerRef}
                  aria-label="Composer"
                  disabled={composerDisabled}
                />
                <section ref={workspaceRef} aria-label="Workspace" tabIndex={-1} />
              </>
            )}
            <ApprovalFeedbackDialog
              approval={approval}
              getFallbackFocus={() => {
                const composer = composerRef.current;
                return composer?.isConnected && !composer.disabled
                  ? composer
                  : workspaceRef.current;
              }}
              onCancel={() => setApproval(null)}
              onConfirm={() => setApproval(null)}
            />
          </main>
        );
      }

      render(<Harness />);
      await user.click(screen.getByRole("button", { name: "Reject" }));
      frames.flush();
      expect(screen.getByRole("textbox", { name: "拒绝原因（可选）" })).toHaveFocus();
      act(removeApprovalCard);
      expect(screen.queryByRole("article", { name: "Shell Approval" })).not.toBeInTheDocument();
      await expect(
        user.click(screen.getByRole("button", { name: "取消" })),
      ).resolves.toBeUndefined();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      if (expectedFocus) {
        expect(screen.getByRole(expectedFocus === "Composer" ? "textbox" : "region", {
          name: expectedFocus,
        })).toHaveFocus();
      }
    },
  );
});

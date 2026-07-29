import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import { useRef, useState } from "react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "./settings/ConfirmDialog.js";

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

  it("Confirm dialog initial closed mount does not move focus or schedule work", () => {
    const requestFrame = vi.spyOn(window, "requestAnimationFrame");
    const focus = vi.spyOn(HTMLElement.prototype, "focus");

    function Harness() {
      const fallbackRef = useRef<HTMLDivElement>(null);
      return (
        <>
          <input aria-label="External input" autoFocus />
          <div ref={fallbackRef}>Workspace</div>
          <ConfirmDialog
            open={false}
            title="Title"
            description="Description"
            getFallbackFocus={() => fallbackRef.current}
            onCancel={vi.fn()}
            onConfirm={vi.fn()}
          />
        </>
      );
    }

    render(<Harness />);

    expect(screen.getByRole("textbox", { name: "External input" })).toHaveFocus();
    expect(requestFrame).not.toHaveBeenCalled();
    expect(focus).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Workspace")).not.toHaveAttribute("tabindex");
  });

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

});

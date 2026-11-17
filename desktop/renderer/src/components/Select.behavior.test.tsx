import { afterEach, describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { applyPlatformDataset } from "../platform.js";
import { Select } from "./Select.js";
import { ModelDialog } from "./settings/ModelDialog.js";
import { HandoffDialog } from "./HandoffDialog.js";

afterEach(() => {
  delete document.documentElement.dataset.platform;
  vi.restoreAllMocks();
});

describe("Select", () => {
  it("renders a plain native select on macOS without injecting a customizable button slot", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const selectRef = createRef<HTMLSelectElement>();
    applyPlatformDataset(document.documentElement, false);

    render(
      <Select
        ref={selectRef}
        aria-label="执行方式"
        value="local"
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="local">本地</option>
        <option value="worktree">新建本地工作树</option>
      </Select>,
    );

    const combobox = screen.getByRole("combobox", { name: "执行方式" });
    expect(selectRef.current).toBe(combobox);
    expect(combobox.querySelector("button")).toBeNull();
    expect(combobox.querySelector("selectedcontent")).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);

    await user.selectOptions(combobox, "worktree");
    expect(onChange).toHaveBeenCalledWith("worktree");
  });

  it("renders the customizable selectedcontent slot on Windows without exposing extra button roles", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    applyPlatformDataset(document.documentElement, true);

    render(
      <Select aria-label="时区" value="Asia/Shanghai" onChange={(event) => onChange(event.target.value)}>
        <optgroup label="亚洲">
          <option value="Asia/Shanghai">Asia/Shanghai</option>
          <option value="Asia/Tokyo">Asia/Tokyo</option>
        </optgroup>
      </Select>,
    );

    const combobox = screen.getByRole("combobox", { name: "时区" });
    const slotButton = combobox.querySelector("button");
    expect(slotButton).not.toBeNull();
    expect(slotButton).toHaveAttribute("tabindex", "-1");
    expect(slotButton).toHaveAttribute("aria-hidden", "true");
    expect(slotButton?.querySelector("selectedcontent")).not.toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);

    await user.selectOptions(combobox, "Asia/Tokyo");
    expect(onChange).toHaveBeenCalledWith("Asia/Tokyo");
  });

  it("stops Escape propagation while an open Windows select picker is open, and lets Escape bubble once closed", () => {
    applyPlatformDataset(document.documentElement, true);
    const onWindowEscape = vi.fn();
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape") onWindowEscape();
    };
    window.addEventListener("keydown", listener);

    try {
      render(
        <Select aria-label="模式" defaultValue="local">
          <option value="local">本地</option>
        </Select>,
      );

      const combobox = screen.getByRole("combobox", { name: "模式" });
      let open = true;
      const querySpy = vi.spyOn(document, "querySelector").mockImplementation((selector: string) => {
        if (selector === "select:open") return open ? combobox : null;
        return null;
      });

      fireEvent.keyDown(combobox, { key: "Escape" });
      expect(onWindowEscape).not.toHaveBeenCalled();

      open = false;
      fireEvent.keyDown(combobox, { key: "Escape" });
      expect(onWindowEscape).toHaveBeenCalledTimes(1);
      querySpy.mockRestore();
    } finally {
      window.removeEventListener("keydown", listener);
    }
  });

  it("keeps ModelDialog and HandoffDialog Tab focus traps skipping the hidden select slot button on Windows", async () => {
    applyPlatformDataset(document.documentElement, true);

    const { unmount } = render(
      <ModelDialog
        draft={{ provider: "deepseek", modelId: "deepseek-chat", apiKey: "" }}
        presets={{
          providers: [{ id: "deepseek", name: "DeepSeek", models: [{ id: "deepseek-chat", name: "DeepSeek Chat" }] }],
        }}
        busy={false}
        onChange={vi.fn()}
        onCancel={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const providerSelect = screen.getByRole("combobox", { name: "提供商" });
    const slotButton = providerSelect.querySelector("button")!;
    expect(slotButton).not.toBeNull();
    // jsdom reports empty client rects by default; mock non-empty rects so ModelDialog's visibility filter runs.
    vi.spyOn(Element.prototype, "getClientRects").mockImplementation(
      () => [{ width: 100, height: 32 }] as unknown as DOMRectList,
    );

    providerSelect.focus();
    expect(document.activeElement).toBe(providerSelect);
    await userEvent.keyboard("{Shift>}{Tab}{/Shift}");
    const saveButton = screen.getByRole("button", { name: "保存" });
    expect(document.activeElement).toBe(saveButton);
    expect(document.activeElement).not.toBe(slotButton);

    unmount();

    render(
      <HandoffDialog
        open
        currentMode="local"
        currentBranch="main"
        branches={["main", "feature/ui"]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("radio", { name: "本地" }));
    const branchSelect = screen.getByRole("combobox", { name: "本地分支" });
    const handoffSlot = branchSelect.querySelector("button")!;
    expect(handoffSlot).not.toBeNull();
    const cancelButton = screen.getByRole("button", { name: "取消" });
    cancelButton.focus();
    await userEvent.keyboard("{Tab}");
    expect(document.activeElement).not.toBe(handoffSlot);
    branchSelect.focus();
    await userEvent.keyboard("{Tab}");
    expect(document.activeElement).toBe(cancelButton);
  });
});

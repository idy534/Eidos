import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { DropdownMenu, ContextMenu } from "./DropdownMenu.js";

describe("DropdownMenu & ContextMenu DOM interaction behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const mockItems = [
    { key: "item-1", label: "Item 1", onClick: vi.fn() },
    { key: "item-2", label: "Item 2 Disabled", disabled: true, onClick: vi.fn() },
    { key: "item-3", label: "Item 3", onClick: vi.fn() },
  ];

  it("Trigger has correct ARIA attributes and toggles on click", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    expect(triggerBtn).toHaveAttribute("aria-haspopup", "menu");
    expect(triggerBtn).toHaveAttribute("aria-expanded", "false");

    await user.click(triggerBtn);
    expect(triggerBtn).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.click(triggerBtn);
    expect(triggerBtn).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("Keyboard navigation: ArrowDown/ArrowUp/Home/End and skip disabled items", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    triggerBtn.focus();

    await user.keyboard("{ArrowDown}");
    const menuItems = screen.getAllByRole("menuitem");
    expect(menuItems[0]).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(menuItems[2]).toHaveFocus();

    await user.keyboard("{ArrowUp}");
    expect(menuItems[0]).toHaveFocus();

    await user.keyboard("{End}");
    expect(menuItems[2]).toHaveFocus();

    await user.keyboard("{Home}");
    expect(menuItems[0]).toHaveFocus();
  });

  it("Enter activates the focused item exactly once and restores trigger focus", async () => {
    const user = userEvent.setup();
    const onClickSpy = vi.fn();
    const items = [{ key: "item-1", label: "Item 1", onClick: onClickSpy }];

    render(<DropdownMenu trigger="Menu Trigger" items={items} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);

    const item1 = screen.getByRole("menuitem", { name: "Item 1" });
    item1.focus();

    await user.keyboard("{Enter}");
    expect(onClickSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(triggerBtn).toHaveFocus();
  });

  it("Space activates the focused item exactly once and restores trigger focus", async () => {
    const user = userEvent.setup();
    const onClickSpy = vi.fn();
    const items = [{ key: "item-1", label: "Item 1", onClick: onClickSpy }];

    render(<DropdownMenu trigger="Menu Trigger" items={items} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);

    const item1 = screen.getByRole("menuitem", { name: "Item 1" });
    item1.focus();

    await user.keyboard(" ");
    expect(onClickSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(triggerBtn).toHaveFocus();
  });

  it("Tab closes menu without forcing focus back to trigger", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.keyboard("{Tab}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(triggerBtn).not.toHaveFocus();
  });

  it("Shift+Tab closes menu without forcing focus back to trigger", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    fireEvent.keyDown(menu, { key: "Tab", shiftKey: true });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(triggerBtn).not.toHaveFocus();
  });

  it("Disabled item cannot activate on click or enter", async () => {
    const user = userEvent.setup();
    const disabledSpy = vi.fn();
    const items = [{ key: "item-1", label: "Disabled Item", disabled: true, onClick: disabledSpy }];

    render(<DropdownMenu trigger="Menu Trigger" items={items} />);

    await user.click(screen.getByRole("button", { name: "Menu Trigger" }));
    const disabledItem = screen.getByRole("menuitem", { name: "Disabled Item" });

    await user.click(disabledItem);
    expect(disabledSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("Window resize closes menu without focus reversal", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent(window, new Event("resize"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("Window scroll closes menu without focus reversal", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent(window, new Event("scroll"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("Escape closes menu and restores trigger focus", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);

    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(triggerBtn).toHaveFocus();
  });

  it("Pointer click outside button leaves that button focused", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <DropdownMenu trigger="Menu Trigger" items={mockItems} />
        <button type="button" id="outside-btn">Outside Button</button>
      </div>,
    );

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    const outsideBtn = screen.getByRole("button", { name: "Outside Button" });
    await user.click(outsideBtn);

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(outsideBtn).toHaveFocus();
  });

  it("Pointer click outside input leaves that input focused", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <DropdownMenu trigger="Menu Trigger" items={mockItems} />
        <input type="text" data-testid="outside-input" />
      </div>,
    );

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    const outsideInput = screen.getByTestId("outside-input");
    await user.click(outsideInput);

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(outsideInput).toHaveFocus();
  });

  it("ContextMenu positions at coordinates and handles keyboard Escape & Tab close", async () => {
    const user = userEvent.setup();
    const onCloseSpy = vi.fn();

    render(
      <ContextMenu
        x={100}
        y={200}
        items={mockItems}
        onClose={onCloseSpy}
      />,
    );

    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();
    expect(menu).toHaveStyle({ left: "100px", top: "200px" });

    const menuItems = screen.getAllByRole("menuitem");
    expect(menuItems[0]).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(onCloseSpy).toHaveBeenCalledTimes(1);
  });

  it.each(["button", "input"] as const)(
    "ContextMenu outside click leaves the external %s focused",
    async (kind) => {
      const user = userEvent.setup();
      function Harness() {
        const [open, setOpen] = useState(true);
        return (
          <>
            {open && <ContextMenu x={10} y={10} items={mockItems} onClose={() => setOpen(false)} />}
            {kind === "button"
              ? <button type="button">Outside target</button>
              : <input aria-label="Outside target" />}
          </>
        );
      }
      render(<Harness />);
      const outside = screen.getByRole(kind === "button" ? "button" : "textbox", { name: "Outside target" });
      await user.click(outside);
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
      expect(outside).toHaveFocus();
    },
  );

  it.each([
    ["Tab", { key: "Tab" }],
    ["Shift+Tab", { key: "Tab", shiftKey: true }],
    ["resize", null],
    ["scroll", null],
  ] as const)("ContextMenu %s closes without restoring its trigger", (kind, key) => {
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const onClose = vi.fn();
    render(
      <ContextMenu
        x={10}
        y={10}
        items={mockItems}
        restoreFocusElement={trigger}
        onClose={onClose}
      />,
    );
    if (key) fireEvent.keyDown(screen.getByRole("menu"), key);
    else fireEvent(window, new Event(kind));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(trigger).not.toHaveFocus();
    trigger.remove();
  });

  it("ContextMenu disabled item cannot activate and enabled item activates exactly once", async () => {
    const user = userEvent.setup();
    const disabled = vi.fn();
    const enabled = vi.fn();
    const onClose = vi.fn();
    render(
      <ContextMenu
        x={10}
        y={10}
        items={[
          { key: "disabled", label: "Disabled", disabled: true, onClick: disabled },
          { key: "enabled", label: "Enabled", onClick: enabled },
        ]}
        onClose={onClose}
      />,
    );
    await user.click(screen.getByRole("menuitem", { name: "Disabled" }));
    expect(disabled).not.toHaveBeenCalled();
    await user.click(screen.getByRole("menuitem", { name: "Enabled" }));
    expect(enabled).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

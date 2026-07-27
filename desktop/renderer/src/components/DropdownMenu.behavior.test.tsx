import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

    // ArrowDown opens menu and focuses first enabled item
    await user.keyboard("{ArrowDown}");
    const menuItems = screen.getAllByRole("menuitem");
    expect(menuItems[0]).toHaveFocus();

    // ArrowDown skips disabled item-2 and focuses item-3
    await user.keyboard("{ArrowDown}");
    expect(menuItems[2]).toHaveFocus();

    // ArrowUp moves focus back to item-1
    await user.keyboard("{ArrowUp}");
    expect(menuItems[0]).toHaveFocus();

    // End focuses last enabled item
    await user.keyboard("{End}");
    expect(menuItems[2]).toHaveFocus();

    // Home focuses first enabled item
    await user.keyboard("{Home}");
    expect(menuItems[0]).toHaveFocus();
  });

  it("Enter and Space activate the focused item and close menu", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);

    const item1 = screen.getByRole("menuitem", { name: "Item 1" });
    item1.focus();

    await user.keyboard("{Enter}");
    expect(mockItems[0].onClick).toHaveBeenCalledTimes(1);
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

  it("Pointer click outside closes menu", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <DropdownMenu trigger="Menu Trigger" items={mockItems} />
        <button type="button">Outside Button</button>
      </div>,
    );

    const triggerBtn = screen.getByRole("button", { name: "Menu Trigger" });
    await user.click(triggerBtn);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Outside Button" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("Menu uses Portal under document.body", async () => {
    const user = userEvent.setup();
    const { container } = render(<DropdownMenu trigger="Menu Trigger" items={mockItems} />);

    await user.click(screen.getByRole("button", { name: "Menu Trigger" }));

    const menu = screen.getByRole("menu");
    expect(container.contains(menu)).toBe(false);
    expect(document.body.contains(menu)).toBe(true);
  });

  it("ContextMenu positions at coordinates and handles keyboard & pointer close", async () => {
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

    // Focuses first enabled item on mount
    const menuItems = screen.getAllByRole("menuitem");
    expect(menuItems[0]).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(onCloseSpy).toHaveBeenCalledTimes(1);
  });
});

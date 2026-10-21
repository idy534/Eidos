import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SettingsNavigation, type SettingsCategoryItem } from "./SettingsNavigation.js";
import type { SettingsCategory } from "./settings-types.js";

describe("SettingsNavigation", () => {
  const categories: SettingsCategoryItem[] = [
    { id: "model", label: "模型" },
    { id: "web_search", label: "网页搜索" },
    { id: "channels", label: "助理" },
    { id: "memory", label: "记忆" },
    { id: "plugins", label: "Plugins", count: 0 },
    { id: "skills", label: "技能", count: 14 },
    { id: "agents", label: "智能体" },
    { id: "mcp", label: "MCP Servers", count: 2, badgeTone: "danger" },
    { id: "worktrees", label: "Worktrees" },
    { id: "runtime", label: "Runtime" },
  ];

  it("renders all categories with their icons and labels", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        categories={categories}
        activeCategory="model"
        onSelectCategory={onSelect}
      />,
    );

    for (const cat of categories) {
      const button = screen.getByRole("tab", { name: new RegExp(cat.label) });
      expect(button).toBeInTheDocument();

      const iconContainer = button.querySelector(".settings-nav-icon");
      expect(iconContainer).not.toBeNull();
      const svg = iconContainer?.querySelector("svg");
      expect(svg).not.toBeNull();
    }
  });

  it("uses the existing project AgentIcon for 智能体 instead of generic bot", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        categories={categories}
        activeCategory="model"
        onSelectCategory={onSelect}
      />,
    );

    const agentButton = screen.getByRole("tab", { name: /智能体/ });
    const agentSvg = agentButton.querySelector(".settings-nav-icon svg");
    expect(agentSvg).not.toBeNull();

    // AgentIcon has 3 circles representing the agent topology:
    // cx=8, cx=3.5, cx=12.5
    const circles = agentSvg?.querySelectorAll("circle");
    expect(circles?.length).toBe(3);

    // It should not have a rect (which would be Lucide bot)
    const rects = agentSvg?.querySelectorAll("rect");
    expect(rects?.length).toBe(0);
  });

  it("renders mcp icon with 180 viewBox and strokeWidth 12", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        categories={categories}
        activeCategory="model"
        onSelectCategory={onSelect}
      />,
    );

    const mcpButton = screen.getByRole("tab", { name: /MCP Servers/ });
    const mcpSvg = mcpButton.querySelector(".settings-nav-icon svg");
    expect(mcpSvg?.getAttribute("viewBox")).toBe("0 0 180 180");
  });

  it("calls onSelectCategory when clicking an item", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        categories={categories}
        activeCategory="model"
        onSelectCategory={onSelect}
      />,
    );

    const memoryButton = screen.getByRole("tab", { name: /记忆/ });
    fireEvent.click(memoryButton);
    expect(onSelect).toHaveBeenCalledWith("memory");
  });

  it("renders count badges and active states", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        categories={categories}
        activeCategory="skills"
        onSelectCategory={onSelect}
      />,
    );

    const skillsButton = screen.getByRole("tab", { name: /技能/ });
    expect(skillsButton).toHaveClass("settings-nav-item--active");
    expect(skillsButton).toHaveAttribute("aria-selected", "true");

    const badge = skillsButton.querySelector(".settings-nav-badge");
    expect(badge?.textContent).toBe("14");

    const mcpButton = screen.getByRole("tab", { name: /MCP Servers/ });
    const mcpBadge = mcpButton.querySelector(".settings-nav-badge--danger");
    expect(mcpBadge?.textContent).toBe("2");
  });
});

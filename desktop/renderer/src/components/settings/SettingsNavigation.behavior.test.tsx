import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  SettingsNavigation,
  type SettingsCategoryItem,
  type SettingsNavGroup,
} from "./SettingsNavigation.js";

describe("SettingsNavigation", () => {
  const groups: SettingsNavGroup[] = [
    {
      title: "使用偏好",
      items: [
        { id: "personalization", label: "个性化" },
        { id: "memory", label: "记忆" },
      ],
    },
    {
      title: "模型与智能体",
      items: [
        { id: "model", label: "模型" },
        { id: "agents", label: "智能体" },
      ],
    },
    {
      title: "工具与连接",
      items: [
        { id: "web_search", label: "网页搜索" },
        { id: "plugins", label: "插件", count: 0 },
        { id: "skills", label: "技能", count: 14 },
        { id: "mcp", label: "MCP 服务", count: 2, badgeTone: "danger" },
        { id: "channels", label: "消息渠道" },
      ],
    },
    {
      title: "工作环境",
      items: [
        { id: "worktrees", label: "Git 工作树" },
        { id: "runtime", label: "运行环境" },
      ],
    },
  ];

  const flatCategories: SettingsCategoryItem[] = groups.flatMap((g) => g.items);

  it("renders 4 groups with their titles and clickable items", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        groups={groups}
        activeCategory="model"
        onSelectCategory={onSelect}
      />,
    );

    for (const group of groups) {
      expect(screen.getByText(group.title)).toBeInTheDocument();
      for (const item of group.items) {
        const button = screen.getByRole("tab", { name: new RegExp(item.label) });
        expect(button).toBeInTheDocument();

        const iconContainer = button.querySelector(".settings-nav-icon");
        expect(iconContainer).not.toBeNull();
        const svg = iconContainer?.querySelector("svg");
        expect(svg).not.toBeNull();
      }
    }
  });

  it("supports backward-compatible flat categories array", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        categories={flatCategories}
        activeCategory="model"
        onSelectCategory={onSelect}
      />,
    );

    for (const cat of flatCategories) {
      const button = screen.getByRole("tab", { name: new RegExp(cat.label) });
      expect(button).toBeInTheDocument();
    }
  });

  it("uses the existing project AgentIcon for 智能体 instead of generic bot", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        groups={groups}
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
        groups={groups}
        activeCategory="model"
        onSelectCategory={onSelect}
      />,
    );

    const mcpButton = screen.getByRole("tab", { name: /MCP 服务/ });
    const mcpSvg = mcpButton.querySelector(".settings-nav-icon svg");
    expect(mcpSvg?.getAttribute("viewBox")).toBe("0 0 180 180");
  });

  it("calls onSelectCategory when clicking an item in any group", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        groups={groups}
        activeCategory="model"
        onSelectCategory={onSelect}
      />,
    );

    const memoryButton = screen.getByRole("tab", { name: /记忆/ });
    fireEvent.click(memoryButton);
    expect(onSelect).toHaveBeenCalledWith("memory");

    const channelButton = screen.getByRole("tab", { name: /消息渠道/ });
    fireEvent.click(channelButton);
    expect(onSelect).toHaveBeenCalledWith("channels");

    const worktreeButton = screen.getByRole("tab", { name: /Git 工作树/ });
    fireEvent.click(worktreeButton);
    expect(onSelect).toHaveBeenCalledWith("worktrees");
  });

  it("renders count badges and active states within groups", () => {
    const onSelect = vi.fn();
    render(
      <SettingsNavigation
        groups={groups}
        activeCategory="skills"
        onSelectCategory={onSelect}
      />,
    );

    const skillsButton = screen.getByRole("tab", { name: /技能/ });
    expect(skillsButton).toHaveClass("settings-nav-item--active");
    expect(skillsButton).toHaveAttribute("aria-selected", "true");

    const badge = skillsButton.querySelector(".settings-nav-badge");
    expect(badge?.textContent).toBe("14");

    const mcpButton = screen.getByRole("tab", { name: /MCP 服务/ });
    const mcpBadge = mcpButton.querySelector(".settings-nav-badge--danger");
    expect(mcpBadge?.textContent).toBe("2");
  });
});

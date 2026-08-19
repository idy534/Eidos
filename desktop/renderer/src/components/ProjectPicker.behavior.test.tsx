import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Project } from "../contracts.js";
import { ProjectPicker } from "./ProjectPicker.js";

const projects: Project[] = [
  {
    id: "project-eidos",
    name: "Eidos",
    workspaceRoot: "/work/eidos",
    gitAvailable: true,
    createdAt: 1,
    updatedAt: 1,
  },
  {
    id: "project-agentic",
    name: "agentic-kit",
    workspaceRoot: "/work/agentic-kit",
    gitAvailable: false,
    createdAt: 2,
    updatedAt: 2,
  },
];

describe("ProjectPicker", () => {
  it("filters projects and selects the matching project", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    render(
      <ProjectPicker
        open
        projects={projects}
        selectedProjectId="project-eidos"
        onSelect={onSelect}
        onCreate={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await user.type(screen.getByRole("searchbox", { name: "搜索项目" }), "agent");

    expect(screen.queryByRole("option", { name: "Eidos" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "agentic-kit" }));
    expect(onSelect).toHaveBeenCalledWith(projects[1]);
  });

  it("opens the create-project flow from the picker", () => {
    const onCreate = vi.fn();
    render(
      <ProjectPicker
        open
        projects={[]}
        onSelect={vi.fn()}
        onCreate={onCreate}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });
});

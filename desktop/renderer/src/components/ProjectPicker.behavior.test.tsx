import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

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

  it("anchors the picker to the top edge of the composer", () => {
    const anchor = document.createElement("form");
    anchor.className = "composer";
    document.body.append(anchor);
    vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({
      bottom: 420,
      height: 80,
      left: 24,
      right: 1000,
      top: 340,
      width: 976,
      x: 24,
      y: 340,
      toJSON: () => ({}),
    });

    render(
      <ProjectPicker
        open
        projects={projects}
        anchorRef={{ current: anchor }}
        onSelect={vi.fn()}
        onCreate={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const layer = screen.getByRole("dialog").parentElement;
    expect(layer).toHaveClass("project-picker-layer--anchored");
    expect(layer).toHaveStyle({
      "--project-picker-left": "24px",
      "--project-picker-bottom": `${window.innerHeight - 340}px`,
    });
    anchor.remove();
  });

  it("does not focus the search field when opened", async () => {
    const requestFrame = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>选择项目</button>
          <ProjectPicker
            open={open}
            projects={projects}
            onSelect={vi.fn()}
            onCreate={vi.fn()}
            onClose={() => setOpen(false)}
          />
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "选择项目" });
    await user.click(trigger);

    expect(trigger).toHaveFocus();
    expect(requestFrame).not.toHaveBeenCalled();
    requestFrame.mockRestore();
  });
});

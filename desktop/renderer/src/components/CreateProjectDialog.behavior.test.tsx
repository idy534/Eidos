import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { CreateProjectDialog } from "./CreateProjectDialog.js";

describe("CreateProjectDialog", () => {
  it("creates a project with only a source folder when the name is omitted", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    const onSelectFolder = vi.fn();

    render(
      <CreateProjectDialog
        open
        sourceFolder="/work/eidos"
        onCreate={onCreate}
        onSelectFolder={onSelectFolder}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "创建项目" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "创建项目" }));
    expect(onCreate).toHaveBeenCalledWith(undefined, "/work/eidos");
    expect(onSelectFolder).not.toHaveBeenCalled();
  });

  it("passes a supplied project name", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();

    render(
      <CreateProjectDialog
        open
        sourceFolder="/work/eidos"
        onCreate={onCreate}
        onSelectFolder={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "项目名称（可选）" }), "Eidos");
    await user.click(screen.getByRole("button", { name: "创建项目" }));
    expect(onCreate).toHaveBeenCalledWith("Eidos", "/work/eidos");
  });

  it("lets the user choose the source folder", () => {
    const onSelectFolder = vi.fn();
    render(
      <CreateProjectDialog
        open
        onCreate={vi.fn()}
        onSelectFolder={onSelectFolder}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "添加 Eidos 可读写的文件夹" }));
    expect(onSelectFolder).toHaveBeenCalledTimes(1);
  });

  it("does not focus the project name when opened", async () => {
    const requestFrame = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>创建项目</button>
          <CreateProjectDialog
            open={open}
            onCreate={vi.fn()}
            onSelectFolder={vi.fn()}
            onCancel={() => setOpen(false)}
          />
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "创建项目" });
    await user.click(trigger);

    expect(trigger).toHaveFocus();
    expect(requestFrame).not.toHaveBeenCalled();
    requestFrame.mockRestore();
  });
});

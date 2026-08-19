import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CreateProjectDialog } from "./CreateProjectDialog.js";

describe("CreateProjectDialog", () => {
  it("creates a project only after a name and source folder are supplied", async () => {
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

    expect(screen.getByRole("button", { name: "创建项目" })).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "项目名称" }), "Eidos");
    expect(screen.getByRole("button", { name: "创建项目" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "创建项目" }));
    expect(onCreate).toHaveBeenCalledWith("Eidos", "/work/eidos");
    expect(onSelectFolder).not.toHaveBeenCalled();
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

    fireEvent.click(screen.getByRole("button", { name: "添加 Codex 可读写的文件夹" }));
    expect(onSelectFolder).toHaveBeenCalledTimes(1);
  });
});

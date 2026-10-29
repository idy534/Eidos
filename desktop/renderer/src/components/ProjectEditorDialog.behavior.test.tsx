import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Project } from "./contracts.js";
import { ProjectEditorDialog } from "./ProjectEditorDialog.js";

const originalRuntimeDescriptor = Object.getOwnPropertyDescriptor(
  window,
  "eidosRuntime",
);

afterEach(() => {
  if (originalRuntimeDescriptor) {
    Object.defineProperty(window, "eidosRuntime", originalRuntimeDescriptor);
  } else {
    Reflect.deleteProperty(window, "eidosRuntime");
  }
});

const project: Project = {
  id: "project-main",
  name: "Original project",
  workspaceRoot: "/work/main",
  gitAvailable: true,
  revision: 3,
  primaryRootId: "main-root",
  roots: [
    { id: "main-root", path: "/work/main", repositoryRoot: "/work/main" },
    { id: "docs-root", path: "/work/docs", repositoryRoot: null },
  ],
  createdAt: 1,
  updatedAt: 2,
};

function installFolderPicker(selectWorkspace: () => Promise<string | undefined>) {
  Object.defineProperty(window, "eidosRuntime", {
    configurable: true,
    writable: true,
    value: { selectWorkspace } as unknown as typeof window.eidosRuntime,
  });
}

describe("ProjectEditorDialog", () => {
  it("adds and removes folders, changes the primary folder, and saves the edited project", async () => {
    const user = userEvent.setup();
    const selectWorkspace = vi.fn(async () => "/work/secondary");
    const onSave = vi.fn();
    installFolderPicker(selectWorkspace);

    render(
      <ProjectEditorDialog
        open
        project={project}
        onSave={onSave}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "项目名称（可选）" }), {
      target: { value: "  Renamed project  " },
    });
    await user.click(screen.getByRole("button", { name: "添加文件夹" }));
    expect(await screen.findByText("secondary")).toBeInTheDocument();
    expect(selectWorkspace).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "移除文件夹 docs" }));
    await user.click(screen.getByRole("button", { name: "将 secondary 设为主要" }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    const [savedName, roots, primaryRootId] = onSave.mock.calls[0] as [
      string | undefined,
      { id: string; path: string }[],
      string,
    ];
    expect(savedName).toBe("Renamed project");
    expect(roots.map((root) => root.path)).toEqual([
      "/work/main",
      "/work/secondary",
    ]);
    expect(roots.some((root) => root.id === primaryRootId && root.path === "/work/secondary")).toBe(true);
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("cancels edits without saving", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    const onCancel = vi.fn();

    render(
      <ProjectEditorDialog
        open
        project={project}
        onSave={onSave}
        onCancel={onCancel}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "项目名称（可选）" }), {
      target: { value: "Unsaved name" },
    });
    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("keeps the draft folders and name visible when saving returns an error", async () => {
    const user = userEvent.setup();
    const selectWorkspace = vi.fn(async () => "/work/secondary");
    installFolderPicker(selectWorkspace);
    const props = {
      open: true,
      project,
      onSave: vi.fn(),
      onCancel: vi.fn(),
    };
    const { rerender } = render(<ProjectEditorDialog {...props} />);

    fireEvent.change(screen.getByRole("textbox", { name: "项目名称（可选）" }), {
      target: { value: "Draft name" },
    });
    await user.click(screen.getByRole("button", { name: "添加文件夹" }));
    expect(await screen.findByText("secondary")).toBeInTheDocument();

    rerender(<ProjectEditorDialog {...props} error="保存失败，请重试。" />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("保存失败，请重试。");
    });
    expect(screen.getByRole("textbox", { name: "项目名称（可选）" })).toHaveValue("Draft name");
    expect(screen.getByText("secondary")).toBeInTheDocument();
  });
});

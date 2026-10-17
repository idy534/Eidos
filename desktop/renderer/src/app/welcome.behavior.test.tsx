import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell.js";
import { deriveRuntimePresentation } from "../session-state.js";
import { createWelcomeRuntime } from "../../test/welcome-runtime.js";
import type { Session } from "../contracts.js";

function renderShell() {
  const status = { state: "ready", protocolVersion: 1, runtimeVersion: "test",
    runShell: true, modelConfigured: true, storageHealth: { state: "ready" } } as const;
  return render(<AppShell runtime={{ status, presentation: deriveRuntimePresentation(status),
    isStorageReady: true, restarting: false, restartRuntime: vi.fn() }} />);
}

beforeEach(() => {
  window.localStorage.clear();
  window.eidosRuntime = createWelcomeRuntime();
  window.scrollTo = vi.fn();
});

describe("welcome conversation", () => {
  it("opens a ready draft and starts its first task from the same Composer", async () => {
    const create = vi.spyOn(window.eidosRuntime, "createSession");
    const submit = vi.spyOn(window.eidosRuntime, "startRun");
    const queue = vi.spyOn(window.eidosRuntime, "listQueuedRuns");
    const { container } = renderShell();
    const input = await screen.findByRole("textbox");
    await waitFor(() => expect(input).toBeEnabled());
    expect(screen.getByRole("heading", { name: "让思考抵达现实" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "新会话" })).not.toBeInTheDocument();
    expect(screen.queryByText(/还没有执行记录/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择项目" })).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
    expect(queue).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "帮我整理任务" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(container.querySelector(".session-canvas--welcome")).toBeNull());
    expect(create).toHaveBeenCalledExactlyOnceWith(null, { executionMode: "local", workspaceNameHint: "帮我整理任务" });
    expect(submit.mock.calls[0]?.slice(1, 3)).toEqual(["帮我整理任务", "welcome-model"]);
    expect(screen.getByRole("textbox")).toBe(input);
    expect(screen.queryByRole("heading", { name: "让思考抵达现实" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消 Run" })).toBeInTheDocument();
  });

  it.each(["create", "start"])("restores the welcome draft and its text after %s fails", async (stage) => {
    if (stage === "create") vi.spyOn(window.eidosRuntime, "createSession").mockRejectedValueOnce(new Error("failed"));
    else vi.spyOn(window.eidosRuntime, "startRun").mockRejectedValueOnce(new Error("failed"));
    const { container } = renderShell();
    const input = await screen.findByRole("textbox");
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "保留这份输入" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(container.querySelector(".session-canvas--welcome")).not.toBeNull());
    await waitFor(() => expect(input).toBeEnabled());
    expect(input).toHaveValue("保留这份输入");
    expect(screen.queryByRole("heading", { name: "新会话" })).not.toBeInTheDocument();
  });

  it("moves the Composer as soon as sending begins, while session creation is pending", async () => {
    let resolve!: (session: Session) => void;
    const realCreate = window.eidosRuntime.createSession;
    vi.spyOn(window.eidosRuntime, "createSession").mockImplementation(() => new Promise((done) => { resolve = done; }));
    const submit = vi.spyOn(window.eidosRuntime, "startRun");
    const { container } = renderShell();
    const input = await screen.findByRole("textbox");
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "开始" } });
    fireEvent.submit(input.closest("form")!);
    expect(container.querySelector(".session-canvas--welcome")).toBeNull();
    expect(submit).not.toHaveBeenCalled();
    await act(async () => resolve(await realCreate(null)));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  });

  it("opens the same welcome page when the user creates another conversation", async () => {
    const { container } = renderShell();
    const input = await screen.findByRole("textbox");
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "开始" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(screen.getByRole("button", { name: "取消 Run" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
    expect(container.querySelector(".session-canvas--welcome")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "让思考抵达现实" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "新会话" })).not.toBeInTheDocument();
    expect(screen.queryByText(/还没有执行记录/)).not.toBeInTheDocument();
  });

  it("keeps the welcome page when the draft is bound to a project", async () => {
    vi.spyOn(window.eidosRuntime, "listProjects").mockResolvedValue({ items: [{
      id: "welcome-project", name: "示例项目", workspaceRoot: "/example/project", gitAvailable: false,
      createdAt: 1, updatedAt: 1,
    }] });
    const { container } = renderShell();
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "选择项目" }));
    fireEvent.click(await screen.findByRole("option", { name: "示例项目" }));
    expect(container.querySelector(".session-canvas--welcome")).not.toBeNull();
    expect(container.querySelector(".composer-context-project")).toHaveTextContent("示例项目");
    expect(screen.queryByRole("heading", { name: "新会话" })).not.toBeInTheDocument();
  });
});

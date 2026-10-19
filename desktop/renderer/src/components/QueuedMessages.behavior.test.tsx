import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeNotification } from "../contracts.js";
import type { QueuedMessage } from "../../../shared/queued-runs.js";
import { QueuedMessages } from "./QueuedMessages.js";

const first: QueuedMessage = { sessionId: "s", runId: "first", userInput: "下一步，修改页面", references: [] };
const second: QueuedMessage = { sessionId: "s", runId: "second", userInput: "再补充文档", references: [] };
let rows: QueuedMessage[];
let notify: (notice: RuntimeNotification) => void;
const onChanged = vi.fn();

beforeEach(() => {
  rows = [first, second];
  onChanged.mockReset();
  window.eidosRuntime = {
    listQueuedRuns: vi.fn(async () => ({ items: rows })),
    onNotification: vi.fn((callback) => { notify = callback; return () => {}; }),
    updateQueuedRun: vi.fn(async (request) => {
      rows = rows.map((row) => row.runId === request.runId ? { ...row, userInput: request.userInput } : row);
      return { change: { kind: "updated", sessionId: request.sessionId, runId: request.runId, userInput: request.userInput, updatedAt: 2 } };
    }),
    deleteQueuedRun: vi.fn(async (request) => {
      rows = rows.filter((row) => row.runId !== request.runId);
      return { change: { kind: "deleted", sessionId: request.sessionId, runId: request.runId } };
    }),
    steerQueuedRun: vi.fn(async (request) => {
      rows = rows.filter((row) => row.runId !== request.runId);
      return { change: { kind: "steered", sessionId: request.sessionId, runId: request.runId, targetRunId: request.targetRunId, itemId: "steering-item" } };
    }),
  } as unknown as typeof window.eidosRuntime;
});

function show(activeRunId: string | undefined = "active") {
  return render(<><QueuedMessages sessionId="s" ready activeRunId={activeRunId} onChanged={onChanged} onRefresh={vi.fn()} /><textarea aria-label="Session 草稿" defaultValue="未发送的草稿" /></>);
}

describe("queued message list", () => {
  it("steers only the chosen queued message and retains the main draft", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "将第 2 条排队消息用于引导当前任务" }));
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(1));
    expect(window.eidosRuntime.steerQueuedRun).toHaveBeenCalledWith(expect.objectContaining({ runId: "second", targetRunId: "active", expectedUserInput: second.userInput }));
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ kind: "steered", runId: "second" }));
    expect(screen.getByText(first.userInput)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Session 草稿" })).toHaveValue("未发送的草稿");
  });

  it("retains the queued message when its steering target has ended", async () => {
    vi.mocked(window.eidosRuntime.steerQueuedRun).mockRejectedValue(new Error("EIDOS_RUNTIME_ERROR:STEER_TARGET_UNAVAILABLE"));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "将第 1 条排队消息用于引导当前任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("消息仍保留在队列中");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("disables steering while editing to retain unsaved text", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "编辑第 1 条排队消息", exact: true }));
    expect(screen.getByRole("button", { name: "将第 1 条排队消息用于引导当前任务" })).toBeDisabled();
  });
  it("lists messages in order and edits inline without overwriting the Session draft", async () => {
    show();
    await screen.findByRole("list", { name: "待发送消息" });
    expect(screen.getAllByRole("listitem")[0]).toHaveTextContent(first.userInput);
    fireEvent.click(screen.getByRole("button", { name: "编辑第 1 条排队消息", exact: true }));
    const editor = screen.getByRole("textbox", { name: "编辑第 1 条排队消息内容" });
    fireEvent.change(editor, { target: { value: "修改后的消息" } });
    fireEvent.click(screen.getByRole("button", { name: "保存", exact: true }));
    await screen.findByRole("button", { name: "编辑第 1 条排队消息：修改后的消息" });
    expect(window.eidosRuntime.updateQueuedRun).toHaveBeenCalledWith(expect.objectContaining({ runId: "first", expectedUserInput: first.userInput, userInput: "修改后的消息" }));
    expect(screen.getByRole("textbox", { name: "Session 草稿" })).toHaveValue("未发送的草稿");
  });

  it("cancels an edit, deletes the chosen message, and hides an empty queue", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "编辑第 1 条排队消息", exact: true }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑第 1 条排队消息内容" }), { target: { value: "未保存" } });
    fireEvent.click(screen.getByRole("button", { name: "取消", exact: true }));
    expect(window.eidosRuntime.updateQueuedRun).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "删除第 2 条排队消息" }));
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(1));
    expect(window.eidosRuntime.deleteQueuedRun).toHaveBeenCalledWith(expect.objectContaining({ runId: "second" }));
    fireEvent.click(screen.getByRole("button", { name: "删除第 1 条排队消息" }));
    await waitFor(() => expect(screen.queryByRole("list", { name: "待发送消息" })).not.toBeInTheDocument());
  });

  it("keeps the editor and message when saving fails, then refreshes facts", async () => {
    vi.mocked(window.eidosRuntime.updateQueuedRun).mockRejectedValue({ businessCode: "QUEUED_RUN_INPUT_CONFLICT" });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "编辑第 1 条排队消息", exact: true }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑第 1 条排队消息内容" }), { target: { value: "保留编辑" } });
    fireEvent.click(screen.getByRole("button", { name: "保存", exact: true }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "编辑第 1 条排队消息内容" })).toHaveValue("保留编辑");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("removes a message when it starts and closes its editor", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "编辑第 1 条排队消息", exact: true }));
    rows = [second];
    act(() => notify({ method: "run/started", params: { sessionId: "s", run: { id: "first", sessionId: "s", status: "running", modelId: "m", modelStepCount: 0, createdAt: 1, updatedAt: 2, startedAt: 2 } } }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "编辑第 1 条排队消息内容" })).not.toBeInTheDocument());
    expect(window.eidosRuntime.deleteQueuedRun).not.toHaveBeenCalled();
  });

  it("prevents duplicate mutation clicks while the request is pending", async () => {
    let finish: (() => void) | undefined;
    vi.mocked(window.eidosRuntime.deleteQueuedRun).mockImplementation(() => new Promise((resolve) => {
      finish = () => resolve({ change: { kind: "deleted", sessionId: "s", runId: "first" } });
    }));
    show();
    const button = await screen.findByRole("button", { name: "删除第 1 条排队消息" });
    fireEvent.click(button); fireEvent.click(button);
    expect(window.eidosRuntime.deleteQueuedRun).toHaveBeenCalledTimes(1);
    await act(async () => finish?.());
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI, RuntimeNotification } from "../contracts.js";
import type { MemoryDocument, MemoryReadResult, MemoryState } from "../../../shared/memory.generated.js";
import { MemorySettings } from "./settings/MemorySettings.js";
import { DataSettings } from "./settings/DataSettings.js";

const note: MemoryDocument = {
  id: "note", scopeId: "project", path: "feedback_testing.md", revision: 3,
  title: "测试约定", summary: "全量测试需要明确要求", kind: "feedback", userOwned: false,
  createdAt: 1, updatedAt: 2,
  evidence: [{ itemId: "source", sessionId: "origin", runId: "run", itemRevision: 1, sourceRevision: 1, evidenceClass: "explicit_user" }],
};
const state: MemoryState = {
  scopes: [
    { id: "global", kind: "global", mode: "auto", privacyEpoch: 0, generation: 1 },
    { id: "project", kind: "project", mode: "read", privacyEpoch: 0, generation: 2 },
  ], documents: [note],
};
type Handler = (method: string, request: Record<string, unknown>) => Promise<unknown>;

function mount(handler?: Handler) {
  let notify: ((notice: RuntimeNotification) => void) | undefined;
  const memory = vi.fn<Handler>(handler ?? (async (method, request) => {
    if (method === "memory/list" || method === "memory/policy") return structuredClone(state);
    if (method === "memory/read") return read(request);
    return { operationId: request.operationId, documentId: "note", path: request.path, revision: 4, code: "memory_saved" };
  }));
  const exportMemoryNotes = vi.fn(async () => {});
  const unsubscribe = vi.fn();
  window.eidosRuntime = {
    memory: memory as EidosRuntimeAPI["memory"], exportMemoryNotes, backupData: vi.fn(async () => {}),
    onNotification: vi.fn((callback) => { notify = callback; return unsubscribe; }),
  } as unknown as EidosRuntimeAPI;
  const onOpenSource = vi.fn();
  return { ...render(<MemorySettings sessionId="session" onOpenSource={onOpenSource} />), memory, exportMemoryNotes,
    onOpenSource, unsubscribe, notify: (notice: RuntimeNotification) => notify?.(notice) };
}

function read(request: Record<string, unknown>, content = "用户只在明确要求时运行全量测试。", nextOffset: number | null = null): MemoryReadResult {
  const index = request.path === "MEMORY.md";
  return {
    path: String(request.path), scopeId: "project", revision: index ? 2 : Number(request.revision ?? 3), currentRevision: index ? 2 : 3,
    revisions: index ? [] : [3, 2], document: index ? null : note,
    content: index ? "# Memory\n\n[feedback_testing.md](feedback_testing.md)" : content,
    offset: Number(request.offset ?? 0), nextOffset,
  };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("shows one mode selector and opens a note from the Markdown index", async () => {
  const { memory, onOpenSource } = mount();
  const user = userEvent.setup();
  await screen.findByRole("link", { name: note.path });
  expect(screen.getByRole("combobox", { name: "记忆模式" })).toHaveValue("read");
  expect(screen.queryByText(/回填|预算|候选|重试任务|完整备份/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("link", { name: note.path }));
  await screen.findByText("用户只在明确要求时运行全量测试。");
  expect(memory).toHaveBeenCalledWith("memory/read", expect.objectContaining({ path: note.path, scope: "current" }));
  await user.click(screen.getByText("来源和版本"));
  await user.click(screen.getByRole("button", { name: "查看来源对话" }));
  expect(onOpenSource).toHaveBeenCalledWith("origin");
});

it("changes the selected scope mode and exports through the native save action", async () => {
  const { memory, exportMemoryNotes } = mount();
  const user = userEvent.setup();
  await screen.findByRole("link", { name: note.path });
  await user.selectOptions(screen.getByRole("combobox", { name: "记忆模式" }), "auto");
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/policy", { sessionId: "session", scope: "current", mode: "auto" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "记忆范围" }), "global");
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/list", expect.objectContaining({ scope: "global" })));
  await user.click(screen.getByRole("button", { name: "导出 Markdown" }));
  expect(exportMemoryNotes).toHaveBeenCalledWith("global", "session");
});

it("loads every body page at the same revision and preserves the draft after a conflict", async () => {
  const memoryHandler: Handler = async (method, request) => {
    if (method === "memory/list") return state;
    if (method === "memory/edit") throw new Error("memory_revision_conflict");
    return read(request, request.offset ? "保留尾部" : "完整正文", request.path !== "MEMORY.md" && !request.offset ? 4 : null);
  };
  const { memory } = mount(memoryHandler);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("link", { name: note.path }));
  await user.click(await screen.findByRole("button", { name: "编辑" }));
  const editor = screen.getByRole("textbox", { name: "笔记正文" });
  expect(editor).toHaveValue("完整正文保留尾部");
  expect(memory).toHaveBeenCalledWith("memory/read", expect.objectContaining({ path: note.path, offset: 4, revision: 3 }));
  await user.type(editor, "补充");
  expect(screen.getByRole("combobox", { name: "记忆范围" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "保存笔记" }));
  await screen.findByRole("alert");
  expect(editor).toHaveValue("完整正文保留尾部补充");
  expect(memory).toHaveBeenCalledWith("memory/edit", expect.objectContaining({ expectedRevision: 3, content: "完整正文保留尾部补充", action: "write" }));
});

it("keeps the editor closed when a body cannot be read", async () => {
  mount(async (method, request) => {
    if (method === "memory/list") return state;
    if (request.path !== "MEMORY.md") throw new Error("memory_body_unavailable");
    return read(request);
  });
  await userEvent.click(await screen.findByRole("link", { name: note.path }));
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "编辑" })).not.toBeInTheDocument();
});

it("confirms deletion and sends the visible revision", async () => {
  const { memory } = mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("link", { name: note.path }));
  await user.click(await screen.findByText("来源和版本"));
  await user.click(screen.getByRole("button", { name: "删除笔记" }));
  expect(memory.mock.calls.some(([method]) => method === "memory/edit")).toBe(false);
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).toHaveTextContent("原始聊天会保留");
  fireEvent.click(dialog.querySelector<HTMLButtonElement>(".btn-danger") ?? screen.getAllByRole("button", { name: "删除笔记" }).at(-1)!);
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/edit", expect.objectContaining({ action: "delete", expectedRevision: 3, path: note.path })));
});

it("displays history without edit or delete actions", async () => {
  const { memory } = mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("link", { name: note.path }));
  await user.click(await screen.findByText("来源和版本"));
  await user.selectOptions(screen.getByRole("combobox", { name: "记忆版本" }), "2");
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/read", expect.objectContaining({ revision: 2 })));
  await waitFor(() => expect(screen.queryByRole("button", { name: "编辑" })).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "删除笔记" })).not.toBeInTheDocument();
});

it("creates a note directly without a maintenance or candidate workflow", async () => {
  const { memory } = mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "新建笔记" }));
  await user.type(screen.getByRole("textbox", { name: "笔记文件名" }), "project_setup.md");
  await user.type(screen.getByRole("textbox", { name: "笔记摘要" }), "项目启动条件");
  await user.type(screen.getByRole("textbox", { name: "笔记正文" }), "用户要求先检查本地依赖。");
  await user.click(screen.getByRole("button", { name: "保存笔记" }));
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/edit", expect.objectContaining({ path: "project_setup.md", expectedRevision: 0, action: "write" })));
});

describe("memory refresh", () => {
  async function advance(ms = 0) { await act(async () => vi.advanceTimersByTimeAsync(ms)); }

  it("uses notifications and focus without polling while idle", async () => {
    vi.useFakeTimers();
    const { memory, notify, unmount, unsubscribe } = mount();
    await advance();
    const lists = () => memory.mock.calls.filter(([method]) => method === "memory/list").length;
    expect(lists()).toBe(1);
    await advance(60_000);
    expect(lists()).toBe(1);
    act(() => notify({ method: "memory/changed", params: { scopeId: "project", generation: 3 } }));
    await advance();
    expect(lists()).toBe(2);
    fireEvent(window, new Event("focus"));
    await advance();
    expect(lists()).toBe(3);
    unmount();
    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it("debounces a Chinese search and keeps the latest query", async () => {
    vi.useFakeTimers();
    const { memory } = mount();
    await advance();
    const search = screen.getByRole("searchbox", { name: "搜索记忆" });
    fireEvent.change(search, { target: { value: "全" } });
    await advance(200);
    fireEvent.change(search, { target: { value: "全量" } });
    await advance(249);
    expect(memory.mock.calls.filter(([method]) => method === "memory/list")).toHaveLength(1);
    await advance(1);
    expect(memory).toHaveBeenCalledWith("memory/list", expect.objectContaining({ query: "全量", cursor: 0 }));
  });
});

it("places full backup in local data settings and reports a failure", async () => {
  const backupData = vi.fn(async () => { throw new Error("disk full"); });
  window.eidosRuntime = { backupData } as unknown as EidosRuntimeAPI;
  render(<DataSettings />);
  await userEvent.click(screen.getByRole("button", { name: "备份本地数据" }));
  await screen.findByRole("alert");
  expect(backupData).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "备份本地数据" })).toBeEnabled();
});

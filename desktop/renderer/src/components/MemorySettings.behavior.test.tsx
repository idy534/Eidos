import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  expect(screen.getByRole("combobox", { name: "模式" })).toHaveValue("read");
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
  await user.selectOptions(screen.getByRole("combobox", { name: "模式" }), "auto");
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/policy", { sessionId: "session", scope: "current", mode: "auto" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "范围" }), "global");
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/list", expect.objectContaining({ scope: "global" })));
  await user.click(screen.getByRole("button", { name: "导出" }));
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
  expect(screen.getByRole("combobox", { name: "范围" })).toBeDisabled();
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

it("shows archived notes and restores them with the visible revision using the keyboard", async () => {
  let restored = false;
  const { memory } = mount(async (method, request) => {
    if (method === "memory/list") return { ...state, documents: [{ ...note, archived: !restored }] };
    if (method === "memory/edit") { restored = true; return { operationId: request.operationId, documentId: note.id, path: note.path, revision: 4, code: "memory_saved" }; }
    const page = read(request);
    if (request.path === "MEMORY.md") return { ...page, content: restored ? page.content : "# Memory\n暂无生效笔记" };
    return { ...page, revision: restored ? 4 : 3, currentRevision: restored ? 4 : 3,
      revisions: restored ? [4, 3, 2] : [3, 2], document: { ...note, archived: !restored } };
  });
  const user = userEvent.setup();
  await screen.findByText("已归档");
  expect(memory).toHaveBeenCalledWith("memory/list", expect.objectContaining({ includeArchived: true }));
  screen.getByRole("button", { name: /feedback_testing.md.*已归档/ }).focus();
  await user.keyboard("{Enter}");
  await user.click(await screen.findByRole("button", { name: "恢复笔记" }));
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/edit", expect.objectContaining({
    path: note.path, expectedRevision: 3, action: "write", content: "用户只在明确要求时运行全量测试。",
  })));
  await screen.findByRole("button", { name: "编辑" });
  expect(screen.queryByText("已归档")).not.toBeInTheDocument();
});

it("keeps an archived note visible when restoration conflicts", async () => {
  mount(async (method, request) => {
    if (method === "memory/list") return { ...state, documents: [{ ...note, archived: true }] };
    if (method === "memory/edit") throw new Error("memory_revision_conflict");
    const page = read(request);
    return { ...page, document: request.path === "MEMORY.md" ? null : { ...note, archived: true } };
  });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /feedback_testing.md.*已归档/ }));
  await user.click(await screen.findByRole("button", { name: "恢复笔记" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "恢复笔记" })).toBeEnabled();
  expect(screen.getByText("用户只在明确要求时运行全量测试。")).toBeInTheDocument();
});

it("can permanently forget an archived note after confirmation", async () => {
  const { memory } = mount(async (method, request) => {
    if (method === "memory/list") return { ...state, documents: [{ ...note, archived: true }] };
    if (method === "memory/edit") return { operationId: request.operationId, documentId: note.id, path: note.path, revision: 4, code: "memory_deleted" };
    return { ...read(request), document: request.path === "MEMORY.md" ? null : { ...note, archived: true } };
  });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /feedback_testing.md.*已归档/ }));
  await user.click(await screen.findByText("来源和版本"));
  await user.click(screen.getByRole("button", { name: "删除笔记" }));
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).toHaveTextContent("正文和历史版本将被删除");
  await user.click(within(dialog).getByRole("button", { name: "删除笔记" }));
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/edit", expect.objectContaining({ action: "delete", expectedRevision: 3, path: note.path })));
});

it("allows a new note without a separate summary", async () => {
  const { memory } = mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "新建笔记" }));
  await user.type(screen.getByRole("textbox", { name: "笔记文件名" }), "preference.md");
  await user.type(screen.getByRole("textbox", { name: "笔记正文" }), "用户偏好简短回复。");
  await user.click(screen.getByRole("button", { name: "保存笔记" }));
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/edit", expect.objectContaining({ summary: "", content: "用户偏好简短回复。" })));
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

it("removes the memory policy banner and places mode and export controls in the header without status badge", async () => {
  mount();
  await screen.findByRole("link", { name: note.path });
  expect(screen.queryByText(/模型在当前对话中及时记下有长期价值的信息/)).not.toBeInTheDocument();
  expect(screen.queryByText(/自动记录|只读生效|已停用/)).not.toBeInTheDocument();
  const header = document.querySelector(".memory-header");
  expect(header).toBeInTheDocument();
  expect(within(header as HTMLElement).getByRole("combobox", { name: "模式" })).toBeInTheDocument();
  expect(within(header as HTMLElement).getByRole("button", { name: "导出" })).toBeInTheDocument();
});

it("collapses and expands the notes list using the divider toggle button", async () => {
  mount();
  const user = userEvent.setup();
  await screen.findByRole("link", { name: note.path });

  const collapseBtn = screen.getByRole("button", { name: "折叠笔记列表" });
  expect(collapseBtn).toHaveAttribute("aria-expanded", "true");
  const workspace = document.querySelector(".memory-workspace")!;
  const filesNav = screen.getByRole("navigation", { name: "记忆文件" });
  expect(workspace).not.toHaveClass("memory-workspace--sidebar-collapsed");
  expect(filesNav).toHaveAttribute("aria-hidden", "false");

  // 点击折叠
  await user.click(collapseBtn);
  expect(screen.getByRole("button", { name: "展开笔记列表" })).toHaveAttribute("aria-expanded", "false");
  expect(workspace).toHaveClass("memory-workspace--sidebar-collapsed");
  expect(filesNav).toHaveAttribute("aria-hidden", "true");

  // 再次点击展开
  await user.click(screen.getByRole("button", { name: "展开笔记列表" }));
  expect(screen.getByRole("button", { name: "折叠笔记列表" })).toHaveAttribute("aria-expanded", "true");
  expect(workspace).not.toHaveClass("memory-workspace--sidebar-collapsed");
  expect(filesNav).toHaveAttribute("aria-hidden", "false");
});

it("places cancel and save buttons in top header row and allows Markdown preview in note editor", async () => {
  const { memory } = mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("link", { name: note.path }));

  // 进入编辑状态
  await user.click(await screen.findByRole("button", { name: "编辑" }));

  // 取消编辑和保存笔记按钮在顶部操作栏
  const headerActions = document.querySelector(".memory-note-actions-header")!;
  expect(headerActions).toBeInTheDocument();
  expect(within(headerActions as HTMLElement).getByRole("button", { name: "取消编辑" })).toBeInTheDocument();
  expect(within(headerActions as HTMLElement).getByRole("button", { name: "保存笔记" })).toBeInTheDocument();

  // MarkdownEditor 源码与预览切换
  expect(screen.getByRole("tab", { name: "源码" })).toBeInTheDocument();
  const previewTab = screen.getByRole("tab", { name: "预览" });
  await user.click(previewTab);
  expect(screen.queryByRole("textbox", { name: "笔记正文" })).not.toBeInTheDocument();

  // 切回源码
  await user.click(screen.getByRole("tab", { name: "源码" }));
  const textarea = screen.getByRole("textbox", { name: "笔记正文" });
  expect(textarea).toBeInTheDocument();

  // 点击顶部取消编辑
  await user.click(within(headerActions as HTMLElement).getByRole("button", { name: "取消编辑" }));
  expect(screen.queryByRole("button", { name: "保存笔记" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "编辑" })).toBeInTheDocument();
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI } from "../contracts.js";
import type { MemoryState } from "../../../shared/memory.generated.js";
import { MemorySettings } from "./settings/MemorySettings.js";

const state: MemoryState = {
  scopes: [{id: "project", kind: "project", settings: {useEnabled: true, generateEnabled: false, dailyCallLimit: 20, dailyTokenLimit: 100000}, privacyEpoch: 0, generation: 1}],
  entries: [{id: "entry", scopeId: "project", revision: 3, kind: "preference", status: "active", title: "回答语言", content: "默认使用中文", aliases: [], evidenceClass: "explicit_user", evidence: [{itemId: "source", sessionId: "origin", runId: "run", itemRevision: 1, sourceRevision: 1, evidenceClass: "explicit_user"}], pinned: false, userOwned: false, createdAt: 1, updatedAt: 2}],
  jobs: [{id: "job", scopeId: "project", sessionId: "session", sourceRevision: 1, kind: "extract", state: "blocked_model", modelId: "deepseek-v4-flash", attempts: 0, notBefore: 0, tokens: 0, estimatedUsage: false}],
  temporary: false, trigramAvailable: true,
};

function mount(snapshot = state, list?: () => Promise<MemoryState>) {
  const memory = vi.fn(async (method: string) => method === "memory/list" ? list ? list() : structuredClone(snapshot) : {status: "applied", operationId: "operation", code: "memory_saved"});
  const api: Partial<EidosRuntimeAPI> = {memory: memory as EidosRuntimeAPI["memory"], backupMemory: vi.fn(async () => {})};
  (window as unknown as {eidosRuntime: EidosRuntimeAPI}).eidosRuntime = api as EidosRuntimeAPI;
  const onOpenSource = vi.fn();
  const view = render(<MemorySettings sessionId="session" onOpenSource={onOpenSource}/>);
  return {memory, onOpenSource, ...view};
}

afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.useRealTimers();});

async function flushRequests() {
  await act(async () => {});
}

async function advanceTime(milliseconds: number) {
  await act(async () => {await vi.advanceTimersByTimeAsync(milliseconds);});
}

describe("Memory settings refresh", () => {
  beforeEach(() => {vi.useFakeTimers();});

  it("does not poll an idle memory list", async () => {
    const {memory} = mount({...state, jobs: []});
    await flushRequests();
    await advanceTime(60_000);
    expect(memory).toHaveBeenCalledTimes(1);
  });

  it.each(["succeeded", "failed", "canceled", "superseded", "blocked_model"] as const)("does not poll a %s job", async (jobState) => {
    const {memory} = mount({...state, jobs: [{...state.jobs[0]!, state: jobState}]});
    await flushRequests();
    await advanceTime(60_000);
    expect(memory).toHaveBeenCalledTimes(1);
  });

  it.each(["queued", "running", "retry_wait"] as const)("refreshes a %s job and stops when it finishes", async (jobState) => {
    const {memory} = mount({...state, jobs: [{...state.jobs[0]!, state: jobState}]});
    await flushRequests();
    memory.mockResolvedValue({...state, jobs: [{...state.jobs[0]!, state: "succeeded"}]});
    await advanceTime(4_999);
    expect(memory).toHaveBeenCalledTimes(1);
    await advanceTime(1);
    expect(memory).toHaveBeenCalledTimes(2);
    expect(screen.getByText("已完成")).toBeInTheDocument();
    await advanceTime(60_000);
    expect(memory).toHaveBeenCalledTimes(2);
  });

  it("waits for the budget resume time instead of polling a paused job", async () => {
    const {memory} = mount({...state, jobs: [{...state.jobs[0]!, state: "paused_budget", notBefore: Date.now() + 60_000}]});
    await flushRequests();
    memory.mockResolvedValue({...state, jobs: []});
    await advanceTime(59_999);
    expect(memory).toHaveBeenCalledTimes(1);
    await advanceTime(1);
    expect(memory).toHaveBeenCalledTimes(2);
  });

  it("combines rapid search edits into one request for the latest query", async () => {
    const {memory} = mount();
    await flushRequests();
    const search = screen.getByRole("textbox", {name: "搜索记忆"});
    fireEvent.change(search, {target: {value: "中"}});
    await advanceTime(200);
    fireEvent.change(search, {target: {value: "中文"}});
    await advanceTime(249);
    expect(memory).toHaveBeenCalledTimes(1);
    await advanceTime(1);
    expect(memory).toHaveBeenCalledTimes(2);
    expect(memory).toHaveBeenLastCalledWith("memory/list", expect.objectContaining({query: "中文", cursor: 0}));
  });

  it("refreshes after filter changes and a successful memory action", async () => {
    const {memory} = mount();
    await flushRequests();
    fireEvent.change(screen.getByRole("combobox", {name: "记忆作用域"}), {target: {value: "global"}});
    await flushRequests();
    expect(memory).toHaveBeenLastCalledWith("memory/list", expect.objectContaining({scope: "global"}));
    fireEvent.click(screen.getByRole("checkbox", {name: "显示归档和历史状态"}));
    await flushRequests();
    expect(memory).toHaveBeenLastCalledWith("memory/list", expect.objectContaining({includeHistory: true}));
    fireEvent.click(screen.getByRole("button", {name: "固定"}));
    await flushRequests();
    expect(memory).toHaveBeenCalledWith("memory/manage", expect.objectContaining({action: "pin"}));
    expect(memory.mock.calls.filter(([method]) => method === "memory/list")).toHaveLength(4);
  });

  it("pauses refreshes while hidden and resumes when visible", async () => {
    let visibility: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
    const {memory, unmount} = mount({...state, jobs: [{...state.jobs[0]!, state: "running"}]});
    await flushRequests();
    visibility = "hidden";
    fireEvent(document, new Event("visibilitychange"));
    await advanceTime(30_000);
    expect(memory).toHaveBeenCalledTimes(1);
    visibility = "visible";
    fireEvent(document, new Event("visibilitychange"));
    await flushRequests();
    expect(memory).toHaveBeenCalledTimes(2);
    unmount();
    await advanceTime(30_000);
    fireEvent(window, new Event("focus"));
    await flushRequests();
    expect(memory).toHaveBeenCalledTimes(2);
  });

  it("refreshes an idle list on focus and manual refresh", async () => {
    const {memory} = mount();
    await flushRequests();
    fireEvent(window, new Event("focus"));
    await flushRequests();
    expect(memory).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", {name: "刷新记忆"}));
    await flushRequests();
    expect(memory).toHaveBeenCalledTimes(3);
  });

  it("allows manual retry after an initial read failure", async () => {
    let failed = true;
    const {memory} = mount(state, async () => {
      if (failed) throw new Error("Runtime unavailable");
      return state;
    });
    await flushRequests();
    expect(screen.getByRole("alert")).toHaveTextContent("无法读取记忆");
    await advanceTime(60_000);
    expect(memory).toHaveBeenCalledTimes(1);
    failed = false;
    fireEvent.click(screen.getByRole("button", {name: "刷新记忆"}));
    await flushRequests();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("默认使用中文")).toBeInTheDocument();
  });

  it("waits for a slow refresh before scheduling the next one", async () => {
    const {memory} = mount({...state, jobs: [{...state.jobs[0]!, state: "running"}]});
    await flushRequests();
    let resolve!: (value: MemoryState) => void;
    memory.mockImplementation(() => new Promise<MemoryState>((done) => {resolve = done;}));
    await advanceTime(5_000);
    await advanceTime(30_000);
    fireEvent(window, new Event("focus"));
    await flushRequests();
    expect(memory).toHaveBeenCalledTimes(2);
    await act(async () => {resolve({...state, jobs: []});});
    await advanceTime(30_000);
    expect(memory).toHaveBeenCalledTimes(2);
  });

  it("discards a previous session's late response and job refresh", async () => {
    let resolve!: (value: MemoryState) => void;
    const first = new Promise<MemoryState>((done) => {resolve = done;});
    let calls = 0;
    const current = {...state, entries: [{...state.entries[0]!, content: "当前会话记忆"}], jobs: []};
    const {memory, rerender} = mount(state, () => ++calls === 1 ? first : Promise.resolve(current));
    rerender(<MemorySettings sessionId="other-session"/>);
    await flushRequests();
    await act(async () => {resolve({...state, jobs: [{...state.jobs[0]!, state: "running"}]});});
    expect(screen.getByText("当前会话记忆")).toBeInTheDocument();
    expect(screen.queryByText("默认使用中文")).not.toBeInTheDocument();
    await advanceTime(30_000);
    expect(memory).toHaveBeenCalledTimes(2);
  });
});

describe("Memory settings", () => {
  it("shows provenance, model blocking and explicit learning consent", async () => {
    const {memory, onOpenSource} = mount();
    const user = userEvent.setup();
    await screen.findByText("默认使用中文");
    expect(screen.getByText(/模型配置不可用/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", {name: "查看来源对话"}));
    expect(onOpenSource).toHaveBeenCalledWith("origin");
    await user.click(screen.getByRole("switch", {name: "自动记忆"}));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("后续对话");
    expect(memory.mock.calls.filter(([method]) => method === "memory/settingsUpdate")).toHaveLength(0);
    await user.click(screen.getByRole("button", {name: "确认"}));
    await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/settingsUpdate", expect.objectContaining({scope: "current", sessionId: "session", settings: expect.objectContaining({generateEnabled: true})})));
  });

  it("allows a bounded history selection while automatic memory is off", async () => {
    const {memory} = mount();
    const user = userEvent.setup();
    await screen.findByText("默认使用中文");
    await user.click(screen.getByRole("button", {name: "回填当前对话"}));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("后续消息需要再次选择");
    expect(memory.mock.calls.filter(([method]) => method === "memory/backfill")).toHaveLength(0);
    await user.click(screen.getByRole("button", {name: "确认"}));
    await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/backfill", expect.objectContaining({sessionId: "session"})));
  });

  it("requires confirmation for forget and submits the visible revision", async () => {
    const {memory} = mount();
    const user = userEvent.setup();
    await screen.findByText("默认使用中文");
    await user.click(screen.getByRole("button", {name: "遗忘"}));
    expect(memory.mock.calls.filter(([method]) => method === "memory/manage")).toHaveLength(0);
    expect(screen.getByRole("alertdialog")).toHaveTextContent("旧备份");
    await user.click(screen.getByRole("button", {name: "确认"}));
    await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/manage", expect.objectContaining({entryId: "entry", expectedRevision: 3, action: "forget"})));
  });

  it("reports failures without claiming a memory change", async () => {
    const {memory} = mount();
    const user = userEvent.setup();
    await screen.findByText("默认使用中文");
    memory.mockImplementation(async (method) => {if (method === "memory/manage") throw new Error("MEMORY_REVISION_CONFLICT"); return state;});
    await user.click(screen.getByRole("button", {name: "固定"}));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent("MEMORY_REVISION_CONFLICT");
    expect(screen.getByRole("button", {name: "固定"})).toBeEnabled();
  });
});

it("loads the full current revision before correcting a search preview", async () => {
  const {memory} = mount();
  const user = userEvent.setup();
  const body = "默认使用中文" + "完整内容".repeat(200) + "保留尾部";
  memory.mockImplementation(async (method) => {
    if (method === "memory/get") return {entry: {...state.entries[0], revision: 4, content: body}};
    return state;
  });
  await screen.findByText("默认使用中文");
  await user.type(screen.getByRole("textbox", {name: "搜索记忆"}), "中文");
  await user.click(screen.getByRole("button", {name: "纠正"}));
  const editor = await screen.findByRole("textbox", {name: "纠正记忆正文"});
  expect(memory).toHaveBeenCalledWith("memory/get", {sessionId: "session", entryId: "entry"});
  await new Promise((resolve) => requestAnimationFrame(resolve));
  await user.type(editor, "补充");
  await user.click(screen.getByRole("button", {name: "确认"}));
  await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/manage", expect.objectContaining({
    expectedRevision: 4, content: body + "补充", action: "correct",
  })));
});

it("keeps the correction dialog closed when the full body cannot be loaded", async () => {
  const {memory} = mount();
  const user = userEvent.setup();
  memory.mockImplementation(async (method) => {
    if (method === "memory/get") throw new Error("MEMORY_ENTRY_UNAVAILABLE");
    return state;
  });
  await screen.findByText("默认使用中文");
  await user.click(screen.getByRole("button", {name: "纠正"}));
  await screen.findByText("MEMORY_ENTRY_UNAVAILABLE");
  expect(screen.queryByRole("textbox", {name: "纠正记忆正文"})).not.toBeInTheDocument();
});

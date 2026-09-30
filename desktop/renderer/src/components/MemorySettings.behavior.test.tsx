import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

function mount() {
  const memory = vi.fn(async (method: string) => method === "memory/list" ? structuredClone(state) : {status: "applied", operationId: "operation", code: "memory_saved"});
  const api: Partial<EidosRuntimeAPI> = {memory: memory as EidosRuntimeAPI["memory"], backupMemory: vi.fn(async () => {})};
  (window as unknown as {eidosRuntime: EidosRuntimeAPI}).eidosRuntime = api as EidosRuntimeAPI;
  const onOpenSource = vi.fn();
  render(<MemorySettings sessionId="session" onOpenSource={onOpenSource}/>);
  return {memory, onOpenSource};
}

afterEach(() => {cleanup(); vi.restoreAllMocks();});

describe("Memory settings", () => {
  it("shows provenance, model blocking and explicit learning consent", async () => {
    const {memory, onOpenSource} = mount();
    const user = userEvent.setup();
    await screen.findByText("默认使用中文");
    expect(screen.getByText(/模型配置不可用/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", {name: "查看来源对话"}));
    expect(onOpenSource).toHaveBeenCalledWith("origin");
    await user.click(screen.getByRole("switch", {name: "自动生成记忆"}));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("后续对话");
    expect(memory.mock.calls.filter(([method]) => method === "memory/settingsUpdate")).toHaveLength(0);
    await user.click(screen.getByRole("button", {name: "确认"}));
    await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/settingsUpdate", expect.objectContaining({scope: "current", sessionId: "session", settings: expect.objectContaining({generateEnabled: true})})));
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
  expect(editor).toHaveValue(body);
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

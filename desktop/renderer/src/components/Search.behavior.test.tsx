import { fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ChatSearch } from "./ChatSearch.js";
import { FindBar } from "./FindBar.js";
import { findText, DEFAULT_FIND_OPTIONS, textNodes } from "./find-text.js";
import type { Project, Session } from "../contracts.js";

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
});

describe("search", () => {
  it("shows the 10 most recently active conversations on an empty query, filters before limiting and opens with Enter", () => {
    const now = Date.now();
    const project: Project = { id: "p", name: "Eidos", workspaceRoot: "/project", gitAvailable: false, createdAt: 1, updatedAt: 1 };
    const sessions: Session[] = Array.from({ length: 60 }, (_, index) => ({
      id: `s-${index}`, title: `最近会话 ${index}`, taskStatus: "completed", workspaceRoot: "/project",
      createdAt: now - (100 - index) * 86_400_000, updatedAt: now - (30 + index * 5) * 60_000,
      ...(index % 2 === 0 ? { project } : { projectless: true }),
    }));
    const searchSessions = vi.fn(); const onSelect = vi.fn(async () => {});
    window.eidosRuntime = { searchSessions } as unknown as typeof window.eidosRuntime;
    const { container } = render(<ChatSearch mode="global" sessions={[{ ...sessions[0]!, id: "blank-draft", title: "", taskStatus: "new", updatedAt: now }, ...sessions.toReversed()]} projects={[project]} onSelect={onSelect} onClose={() => {}} />);
    const titles = () => Array.from(container.querySelectorAll(".chat-search-result-title"), (element) => element.textContent);
    expect(titles()).toEqual(Array.from({ length: 10 }, (_, index) => `最近会话 ${index}`));
    expect(container.querySelector("time")).toHaveTextContent("30 分钟前");
    expect(container.querySelectorAll(".chat-search-recent-project")).toHaveLength(5);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "p" } });
    expect(titles()).toEqual(Array.from({ length: 10 }, (_, index) => `最近会话 ${index * 2}`));
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ sessionId: "s-2", itemId: null }), "");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "projectless" } });
    expect(titles()).toEqual(Array.from({ length: 10 }, (_, index) => `最近会话 ${index * 2 + 1}`));
    expect(container.querySelectorAll(".chat-search-recent-project")).toHaveLength(0);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
    expect(titles()).toHaveLength(10);
    expect(searchSessions).not.toHaveBeenCalled();
  });

  it("returns to recent conversations when the query is cleared and ignores a late search response", async () => {
    const session: Session = { id: "recent", title: "最近的会话", taskStatus: "completed", workspaceRoot: "/workspace", createdAt: 1, updatedAt: 2 };
    let finish: (value: unknown) => void = () => {};
    const searchSessions = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    window.eidosRuntime = { searchSessions } as unknown as typeof window.eidosRuntime;
    render(<ChatSearch mode="global" sessions={[session]} projects={[]} onSelect={vi.fn()} onClose={() => {}} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "needle" } });
    await waitFor(() => expect(searchSessions).toHaveBeenCalledOnce());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
    expect(screen.getByRole("button", { name: /最近的会话/ })).toBeInTheDocument();
    await act(async () => finish({ items: [{ sessionId: "stale", title: "迟到搜索结果", projectName: "", snippet: "needle", occurrence: 0, matchKind: "title" }], indexing: false }));
    expect(screen.queryByText("迟到搜索结果")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /最近的会话/ })).toBeInTheDocument();
  });

  it("uses the same today and yesterday labels and exact time as the sidebar hover card", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-20T12:00:00"));
    const base: Session = { id: "today", title: "今天的会话", taskStatus: "completed", workspaceRoot: "/workspace", createdAt: 1, updatedAt: new Date("2026-10-20T01:21:00").getTime() };
    try {
      const view = render(<ChatSearch mode="global" projects={[]} sessions={[base, { ...base, id: "yesterday", title: "昨天的会话", updatedAt: new Date("2026-10-19T18:30:00").getTime() }]} onSelect={vi.fn()} onClose={() => {}} />);
      expect(screen.getByText("今天 01:21")).toHaveAttribute("title", "2026-10-20 01:21");
      expect(screen.getByText("昨天 18:30")).toHaveAttribute("title", "2026-10-19 18:30");
      view.unmount();
    } finally { vi.useRealTimers(); }
  });

  it("finds Unicode text across syntax spans and ignores line numbers and controls", () => {
    const root = document.createElement("article");
    root.innerHTML = '<div class="preview-line"><span class="preview-line-number">99</span><span>中</span><span>文 😀</span></div><button>中文</button>';
    const content = textNodes(root);
    expect(content.text).toBe("中文 😀");
    expect(findText(content.text, "中文", DEFAULT_FIND_OPTIONS).matches).toEqual([{ start: 0, end: 2 }]);
    expect(findText(content.text, "😀", DEFAULT_FIND_OPTIONS).matches).toEqual([{ start: 3, end: 5 }]);
    expect(findText("Aa aa cat cats 猫", "aa", { ...DEFAULT_FIND_OPTIONS, caseSensitive: true }).matches).toHaveLength(1);
    expect(findText("cat cats", "cat", { ...DEFAULT_FIND_OPTIONS, wholeWord: true }).matches).toHaveLength(1);
    expect(findText("a1 b2", "[ab]\\d", { ...DEFAULT_FIND_OPTIONS, regex: true }).matches).toHaveLength(2);
    expect(() => findText("x", "[", { ...DEFAULT_FIND_OPTIONS, regex: true })).toThrow();
    expect(findText("a".repeat(3000), "a", DEFAULT_FIND_OPTIONS).truncated).toBe(true);
  });

  it("does not submit composition text and navigates with keyboard", () => {
    const query = vi.fn(); const next = vi.fn(); const previous = vi.fn(); const close = vi.fn();
    render(<FindBar query="" onQuery={query} onNext={next} onPrevious={previous} onClose={close} status="0/0" />);
    const input = screen.getByRole("textbox");
    fireEvent.compositionStart(input); fireEvent.change(input, { target: { value: "中" } });
    expect(query).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input, { data: "中文", target: { value: "中文" } });
    expect(query).toHaveBeenCalledWith("中文");
    fireEvent.keyDown(input, { key: "Enter" }); expect(next).toHaveBeenCalledOnce();
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true }); expect(previous).toHaveBeenCalledOnce();
    fireEvent.keyDown(input, { key: "Escape" }); expect(close).toHaveBeenCalledOnce();
  });

  it("discards late query responses and opens the current message anchor", async () => {
    let resolveOld: (value: unknown) => void = () => {};
    const onSelect = vi.fn(async () => {});
    const searchSessions = vi.fn().mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValue({ items: [{ sessionId: "s", title: "当前结果", projectName: "Eidos", itemId: "i", snippet: "new", occurrence: 0, matchKind: "message" }], indexing: false });
    window.eidosRuntime = { searchSessions } as unknown as typeof window.eidosRuntime;
    render(<ChatSearch mode="global" projects={[]} onSelect={onSelect} onClose={() => {}} />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "old" } });
    await waitFor(() => expect(searchSessions).toHaveBeenCalledOnce());
    fireEvent.change(input, { target: { value: "new" } });
    await screen.findByText("当前结果");
    await act(async () => resolveOld({ items: [{ sessionId: "old", title: "旧结果", projectName: "", snippet: "", matchKind: "title" }], indexing: false }));
    expect(screen.queryByText("旧结果")).not.toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ itemId: "i" }), "new");
  });

  it("searches all persisted messages in the active Session rather than loaded DOM", async () => {
    const searchSessions = vi.fn(async () => ({ items: [{ sessionId: "s", title: "历史", projectName: "", itemId: "unloaded", snippet: "更早记录", occurrence: 0, matchKind: "message" }], indexing: false }));
    window.eidosRuntime = { searchSessions } as unknown as typeof window.eidosRuntime;
    const onSelect = vi.fn(async () => {});
    render(<ChatSearch mode="session" sessionId="s" projects={[]} onSelect={onSelect} onClose={() => {}} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "记录" } });
    await waitFor(() => expect(onSelect).toHaveBeenCalled());
    expect(searchSessions).toHaveBeenCalledWith({ query: "记录", sessionId: "s" });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ itemId: "unloaded" }), "记录");
  });

  it("keeps results visible while indexing refreshes and places scope beside the query", async () => {
    const response = { items: [{ sessionId: "s", title: "巴厘岛旅行", projectName: "无项目", itemId: null, snippet: "巴厘岛旅行", occurrence: 0, matchKind: "title" }], indexing: true };
    let finish: (value: unknown) => void = () => {};
    const searchSessions = vi.fn().mockResolvedValueOnce(response).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    window.eidosRuntime = { searchSessions } as unknown as typeof window.eidosRuntime;
    render(<ChatSearch mode="global" projects={[]} onSelect={vi.fn()} onClose={() => {}} />);
    const input = screen.getByRole("textbox");
    expect(screen.getByRole("combobox").parentElement).toBe(input.parentElement);
    fireEvent.change(input, { target: { value: "巴厘岛" } });
    const result = await screen.findByRole("button", { name: /巴厘岛旅行/ });
    await waitFor(() => expect(searchSessions).toHaveBeenCalledTimes(2));
    expect(result).toBeInTheDocument();
    expect(screen.queryByText(/建立索引|标题与聊天正文/)).not.toBeInTheDocument();
    await act(async () => finish({ ...response, indexing: false }));
    expect(result).toBeInTheDocument();
  });
});

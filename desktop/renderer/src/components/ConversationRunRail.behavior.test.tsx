import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Item, Run } from "../contracts.js";
import {
  ConversationRunRail,
  buildRunAnchors,
  cleanAssistantPreviewText,
  computeItemHeight,
  computeMarkerWidths,
  extractAssistantPreview,
  extractUserPreview,
} from "./ConversationRunRail.js";

function makeRun(id: string, overrides: Partial<Run> = {}): Run {
  return {
    id,
    sessionId: "session-1",
    status: "succeeded",
    createdAt: Date.now(),
    modelId: "model-1",
    ...overrides,
  };
}

function makeUserItem(id: string, runId: string, content: string, ordinal = 1): Item {
  return {
    id,
    sessionId: "session-1",
    runId,
    kind: "user_message",
    ordinal,
    status: "completed",
    content,
    createdAt: Date.now(),
  };
}

function makeAssistantItem(id: string, runId: string, content: string, ordinal = 2): Item {
  return {
    id,
    sessionId: "session-1",
    runId,
    kind: "assistant_message",
    ordinal,
    status: "completed",
    content,
    createdAt: Date.now() + 10,
  };
}

function makeToolItem(id: string, runId: string, toolName: string, ordinal = 2): Item {
  return {
    id,
    sessionId: "session-1",
    runId,
    kind: "tool_use",
    ordinal,
    status: "completed",
    createdAt: Date.now() + 5,
    toolCall: {
      callId: `call-${id}`,
      toolName,
      arguments: {},
    },
  };
}

describe("ConversationRunRail pure helpers", () => {
  it("cleans assistant preview markdown and truncates to 280 chars", () => {
    const raw = "### 标题\n```ts\nconst x = 1;\n```\n这里是**加粗**与*斜体*，还有[链接文字](https://example.com)与`代码`。\n> 引用块";
    const cleaned = cleanAssistantPreviewText(raw);
    expect(cleaned).toContain("标题");
    expect(cleaned).toContain("const x = 1;");
    expect(cleaned).toContain("这里是加粗与斜体，还有链接文字与代码。");
    expect(cleaned).not.toContain("###");
    expect(cleaned).not.toContain("**");
    expect(cleaned).not.toContain("https://example.com");

    const longText = "a".repeat(500);
    expect(cleanAssistantPreviewText(longText).length).toBe(280);
  });

  it("extracts user preview and formats inline references as plain text", () => {
    expect(extractUserPreview(undefined)).toBe("查看提问");
    const itemWithRef = makeUserItem("u1", "r1", "请检查 [@main.ts](ref:1234567890ab) 和 [$git-commit](ref:abcdef123456) 的修改");
    expect(extractUserPreview(itemWithRef)).toBe("请检查 @main.ts 和 $git-commit 的修改");
  });

  it("extracts assistant preview selecting the final response after tool calls", () => {
    const run = makeRun("r1");
    const items: Item[] = [
      makeUserItem("u1", "r1", "问题", 1),
      makeAssistantItem("a1", "r1", "我先思考一下并调用工具", 2),
      makeToolItem("t1", "r1", "read_file", 3),
      makeAssistantItem("a2", "r1", "这是经过分析后的最终回答，请查阅。", 4),
    ];

    const result = extractAssistantPreview(run, items);
    expect(result.preview).toBe("这是经过分析后的最终回答，请查阅。");
    expect(result.isStatusPlaceholder).toBe(false);
  });

  it("extracts plan summary or status placeholders when no final text exists", () => {
    const planRun = makeRun("r1", { status: "running" });
    const planItems = [makeUserItem("u1", "r1", "问题", 1), makeToolItem("t1", "r1", "write_plan", 2)];
    expect(extractAssistantPreview(planRun, planItems).preview).toBe("已生成执行计划");

    const runningRun = makeRun("r2", { status: "running" });
    expect(extractAssistantPreview(runningRun, []).preview).toBe("正在思考…");

    const waitingRun = makeRun("r3", { status: "waiting_approval" });
    expect(extractAssistantPreview(waitingRun, []).preview).toBe("等待批准…");

    const canceledRun = makeRun("r4", { status: "canceled" });
    expect(extractAssistantPreview(canceledRun, []).preview).toBe("任务已取消");
  });

  it("computes marker lens widths proportionally", () => {
    const resting = computeMarkerWidths(5, -1);
    expect(resting).toEqual([6, 6, 6, 6, 6]);

    const active = computeMarkerWidths(7, 3);
    // scale: [1, 0.7, 0.4, 0.2] -> 26, 20, 14, 10, 6
    expect(active[3]).toBe(26);
    expect(active[2]).toBe(20);
    expect(active[4]).toBe(20);
    expect(active[1]).toBe(14);
    expect(active[5]).toBe(14);
    expect(active[0]).toBe(10);
    expect(active[6]).toBe(10);
  });

  it("computes item height adapting to available vertical height", () => {
    expect(computeItemHeight(600, 20)).toBe(18);
    expect(computeItemHeight(600, 50)).toBe(12);
    expect(computeItemHeight(600, 100)).toBe(8);
  });

  it("builds stable anchors and filters queued and superseded runs", () => {
    const r1 = makeRun("r1");
    const r2 = makeRun("r2");
    const r3 = makeRun("r3");
    const r4 = makeRun("r4");
    const superseded = makeRun("r-old");
    const queued = makeRun("r-queued", { status: "queued", queuedAt: Date.now() });

    const items = [
      makeUserItem("u1", "r1", "问1"),
      makeUserItem("u2", "r2", "问2"),
      makeUserItem("u3", "r3", "问3"),
      makeUserItem("u4", "r4", "问4"),
      makeUserItem("u-old", "r-old", "旧问"),
    ];

    const anchors = buildRunAnchors({
      runs: [r1, r2, r3, r4, superseded, queued],
      items,
      supersededRunIds: new Set(["r-old"]),
    });

    expect(anchors.length).toBe(4);
    expect(anchors.map((a) => a.runId)).toEqual(["r1", "r2", "r3", "r4"]);
    expect(anchors[0]?.userPreview).toBe("问1");
    expect(anchors[0]?.isMounted).toBe(true);
  });
});

describe("ConversationRunRail component interaction", () => {
  let mockFeed: HTMLDivElement;

  beforeEach(() => {
    mockFeed = document.createElement("div");
    mockFeed.className = "feed";
    document.body.appendChild(mockFeed);
  });

  afterEach(() => {
    cleanup();
    mockFeed.remove();
    vi.restoreAllMocks();
  });

  it("does not render when effective user runs count is less than 4", () => {
    const runs = [makeRun("r1"), makeRun("r2"), makeRun("r3")];
    const items = [
      makeUserItem("u1", "r1", "问1"),
      makeUserItem("u2", "r2", "问2"),
      makeUserItem("u3", "r3", "问3"),
    ];

    const { container } = render(
      <ConversationRunRail
        runs={runs}
        items={items}
        feedRef={{ current: mockFeed }}
        pauseFollowing={vi.fn()}
      />,
    );

    expect(container.querySelector(".conversation-run-rail")).toBeNull();
  });

  it("renders when runs count is at least 4, and shows preview card on hover", () => {
    const runs = [makeRun("r1"), makeRun("r2"), makeRun("r3"), makeRun("r4")];
    const items = [
      makeUserItem("u1", "r1", "第 1 轮提问"),
      makeAssistantItem("a1", "r1", "第 1 轮回答内容"),
      makeUserItem("u2", "r2", "第 2 轮提问"),
      makeAssistantItem("a2", "r2", "第 2 轮回答内容"),
      makeUserItem("u3", "r3", "第 3 轮提问"),
      makeAssistantItem("a3", "r3", "第 3 轮回答内容"),
      makeUserItem("u4", "r4", "第 4 轮提问"),
      makeAssistantItem("a4", "r4", "第 4 轮回答内容"),
    ];

    render(
      <ConversationRunRail
        runs={runs}
        items={items}
        feedRef={{ current: mockFeed }}
        pauseFollowing={vi.fn()}
      />,
    );

    const rail = screen.getByRole("navigation", { name: "会话导航" });
    expect(rail).toBeInTheDocument();

    const buttons = screen.getAllByRole("button");
    expect(buttons.length).toBe(4);

    // Initial state: no preview card
    expect(screen.queryByRole("tooltip")).toBeNull();

    // Hover second button
    fireEvent.focus(buttons[1]!);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    expect(screen.getByText("第 2 轮提问")).toBeInTheDocument();
    expect(screen.getByText("第 2 轮回答内容")).toBeInTheDocument();

    // Blur closes tooltip
    fireEvent.blur(buttons[1]!);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("calls pauseFollowing and smooth scrolls to item on click", () => {
    const runs = [makeRun("r1"), makeRun("r2"), makeRun("r3"), makeRun("r4")];
    const items = [
      makeUserItem("u1", "r1", "问1"),
      makeUserItem("u2", "r2", "问2"),
      makeUserItem("u3", "r3", "问3"),
      makeUserItem("u4", "r4", "问4"),
    ];

    // Mock DOM elements
    const itemEl = document.createElement("div");
    itemEl.id = "item-u2";
    mockFeed.appendChild(itemEl);

    const pauseFollowing = vi.fn();
    const scrollToSpy = vi.fn();
    mockFeed.scrollTo = scrollToSpy;

    render(
      <ConversationRunRail
        runs={runs}
        items={items}
        feedRef={{ current: mockFeed }}
        pauseFollowing={pauseFollowing}
      />,
    );

    const buttons = screen.getAllByRole("button");
    fireEvent.click(buttons[1]!);

    expect(pauseFollowing).toHaveBeenCalledTimes(1);
    expect(scrollToSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        behavior: "smooth",
      }),
    );
  });

  it("calls onEnsureItemMounted when clicking an unmounted historical turn", async () => {
    const runs = [makeRun("r1"), makeRun("r2"), makeRun("r3"), makeRun("r4")];
    const resultItems = [
      makeUserItem("u1", "r1", "远古问1"),
      makeUserItem("u2", "r2", "问2"),
      makeUserItem("u3", "r3", "问3"),
      makeUserItem("u4", "r4", "问4"),
    ];
    // u1 is not in mounted items!
    const mountedItems = [resultItems[1]!, resultItems[2]!, resultItems[3]!];

    const pauseFollowing = vi.fn();
    const onEnsureItemMounted = vi.fn().mockImplementation(async () => {
      // Simulate mounting the element
      const el = document.createElement("div");
      el.id = "item-u1";
      mockFeed.appendChild(el);
      return true;
    });

    const scrollToSpy = vi.fn();
    mockFeed.scrollTo = scrollToSpy;

    render(
      <ConversationRunRail
        runs={runs}
        items={mountedItems}
        resultItems={resultItems}
        feedRef={{ current: mockFeed }}
        pauseFollowing={pauseFollowing}
        onEnsureItemMounted={onEnsureItemMounted}
      />,
    );

    const buttons = screen.getAllByRole("button");
    await act(async () => {
      fireEvent.click(buttons[0]!);
    });

    expect(pauseFollowing).toHaveBeenCalledTimes(1);
    expect(onEnsureItemMounted).toHaveBeenCalledWith("u1");
  });

  it("supports roving tabIndex keyboard navigation with arrow keys", () => {
    const runs = [makeRun("r1"), makeRun("r2"), makeRun("r3"), makeRun("r4")];
    const items = [
      makeUserItem("u1", "r1", "问1"),
      makeUserItem("u2", "r2", "问2"),
      makeUserItem("u3", "r3", "问3"),
      makeUserItem("u4", "r4", "问4"),
    ];

    render(
      <ConversationRunRail
        runs={runs}
        items={items}
        feedRef={{ current: mockFeed }}
        pauseFollowing={vi.fn()}
      />,
    );

    const buttons = screen.getAllByRole("button");
    // Initially first button has tabIndex 0
    expect(buttons[0]?.tabIndex).toBe(0);
    expect(buttons[1]?.tabIndex).toBe(-1);

    // Arrow down moves focus to next item
    fireEvent.keyDown(buttons[0]!, { key: "ArrowDown" });
    expect(screen.getByText("问2")).toBeInTheDocument();

    // End key moves to last item
    fireEvent.keyDown(buttons[1]!, { key: "End" });
    expect(screen.getByText("问4")).toBeInTheDocument();

    // Home key moves back to first item
    fireEvent.keyDown(buttons[3]!, { key: "Home" });
    expect(screen.getByText("问1")).toBeInTheDocument();

    // Escape closes preview
    fireEvent.keyDown(buttons[0]!, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});

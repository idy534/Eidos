import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { TeamMethods, TeamProposalView, TeamSessionState, TeamView } from "../../../../shared/teams.js";
import type { ModelOption, RuntimeNotification } from "../../contracts.js";
import { TeamPanel } from "./TeamPanel.js";
import { TeamRoster } from "./TeamRoster.js";
import { useTeam } from "./useTeam.js";

const sessionId = "11111111-1111-1111-1111-111111111111";
const models: ModelOption[] = [
  {
    id: "custom-uuid-1",
    name: "DeepSeek-V4.1-Flash",
    vendor: "DeepSeek",
    provider: "deepseek",
    url: "https://api.deepseek.com",
    supportsToolCall: true,
    supportsImages: false,
    supportsReasoning: false,
  },
  {
    id: "custom-uuid-2",
    name: "Claude Sonnet 4.6",
    vendor: "Anthropic",
    provider: "custom",
    custom: {
      id: "custom-uuid-2",
      name: "Claude Sonnet 4.6",
      providerName: "Anthropic",
      protocol: "openai_compatible",
      modelId: "claude-sonnet-4-6",
      baseUrl: "https://api.example.com/v1",
    },
    url: "https://api.example.com/v1",
    supportsToolCall: true,
    supportsImages: true,
    supportsReasoning: true,
  },
];
const team: TeamView = {
  id: "team-1", leadSessionId: sessionId, status: "active", objective: "重构登录模块", autoWakeLead: true,
  runSettings: { modelId: "custom-uuid-1" }, version: 1, createdAt: 1, updatedAt: 1,
  members: [
    { id: "m-1", name: "builder", role: "worker", brief: "实现接口", modelId: "custom-uuid-1", workspaceMode: "worktree",
      status: "active", sessionId: "member-session-1", runId: "run-b", runStatus: "running", createdAt: 1 },
    { id: "m-2", name: "critic", role: "verifier", brief: "审查", modelId: "custom-uuid-2", workspaceMode: "shared",
      status: "active", sessionId: "member-session-2", createdAt: 1 },
  ],
  tasks: [
    { id: "t-1", key: "api.1", title: "接口", status: "in_review", owner: "builder", reviewer: "lead", milestone: "M1",
      evidence: [{ toolCallId: "call-1" }], version: 2, createdBy: "lead", updatedAt: 2 },
    { id: "t-2", key: "gate.1.critic", kind: "gate", title: "Gate: M1", status: "open", owner: "critic", blocked: true,
      milestone: "M1", version: 1, createdBy: "system", updatedAt: 2 },
  ],
};
let state: TeamSessionState;
let boardMessages: Array<Record<string, unknown>>;
let listeners: ((notice: RuntimeNotification) => void)[];
const calls: { method: string; request: unknown }[] = [];
const runtime = vi.fn(async (method: keyof TeamMethods, request: unknown): Promise<unknown> => {
  calls.push({ method, request });
  if (method === "team/read") return state;
  if (method === "team/messages") {
    const after = (request as { after?: number }).after ?? 0;
    const fresh = boardMessages.filter((message) => Number(message.seq ?? 0) > after);
    const nextCursor = fresh.length > 0 ? Math.max(...fresh.map((message) => Number(message.seq ?? 0))) : after;
    return { messages: fresh, nextCursor, hasMore: false };
  }
  if (method === "team/memberChanges") return { member: "builder", status: "ready", files: [] };
  return team;
});

function Harness({ memberName, onOpenMember = () => {}, onBack = () => {} }: {
  memberName?: string;
  onOpenMember?: (name: string) => void;
  onBack?: () => void;
} = {}) {
  const value = useTeam(sessionId, true);
  if (!value.team) return null;
  return (
    <TeamPanel
      team={value.team}
      usage={value.usage}
      actions={value}
      memberName={memberName}
      onOpenMember={onOpenMember}
      onBack={onBack}
      onCancelRun={() => {}}
      approvals={[]}
      onApprove={() => {}}
      onReject={() => {}}
      models={models}
    />
  );
}

beforeEach(() => {
  state = { team, usage: [{ member: "builder", inputTokens: 1500, outputTokens: 500, incomplete: true }] };
  boardMessages = [
    { id: "sys-1", seq: 1, channel: "board", author: "system", authorKind: "system", recipients: ["builder"],
      content: "You joined the team. Read your brief, the task board and the roster, then start.", createdAt: 1 },
    { id: "sys-2", seq: 2, channel: "board", author: "system", authorKind: "system", recipients: ["critic"],
      content: "You joined the team. Read your brief, the task board and the roster, then start.", createdAt: 1 },
    { id: "msg-1", seq: 3, channel: "direct", author: "builder", authorKind: "member", recipients: ["lead"],
      task: "api.1", content: "接口已完成", createdAt: 2 },
  ];
  listeners = [];
  calls.length = 0;
  runtime.mockClear();
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: {
    team: runtime,
    readSession: vi.fn(async () => ({ session: { id: "member-session-1", title: "builder" }, items: [], runs: [] })),
    onNotification: (listener: (notice: RuntimeNotification) => void) => {
      listeners.push(listener);
      return () => { listeners = listeners.filter((value) => value !== listener); };
    },
  } });
});

it("hides system notices by default, reveals them on demand, groups time dividers and posts with an operation id", async () => {
  const base = 1_700_000_000_000;
  boardMessages = [
    { id: "sys-1", seq: 1, channel: "board", author: "system", authorKind: "system", recipients: ["builder"],
      content: "You joined the team. Read your brief, the task board and the roster, then start.", createdAt: base },
    { id: "sys-2", seq: 2, channel: "board", author: "system", authorKind: "system", recipients: ["critic"],
      content: "You joined the team. Read your brief, the task board and the roster, then start.", createdAt: base + 1000 },
    { id: "msg-1", seq: 3, channel: "direct", author: "builder", authorKind: "member", recipients: ["lead"],
      task: "api.1", content: "接口已完成", createdAt: base + 60_000 },
    { id: "msg-2", seq: 4, channel: "board", author: "critic", authorKind: "member", toAll: true,
      content: "已开始复核", createdAt: base + 7 * 60_000 },
  ];
  render(<Harness />);
  expect(await screen.findByText("接口已完成")).toBeInTheDocument();
  // System notices stay out of the default collaboration stream; 6-minute gap yields 2 time dividers.
  expect(screen.queryByText(/You joined the team/)).not.toBeInTheDocument();
  expect(document.querySelectorAll(".team-time-divider")).toHaveLength(2);

  // Toggling "系统" reveals collapsed system group; clicking expands individual notices.
  await userEvent.click(screen.getByRole("button", { name: "系统日志（2 条）" }));
  const summaryBtn = screen.getByRole("button", { name: /2 名成员加入/ });
  expect(summaryBtn).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(summaryBtn);
  expect(summaryBtn).toHaveAttribute("aria-expanded", "true");
  expect(screen.getAllByText(/You joined the team/)).toHaveLength(2);

  // Header status tooltip carries formatted usage with unit in narrow mode.
  const statusWrap = document.querySelector(".team-panel__status-wrap")!;
  await userEvent.hover(statusWrap);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(/≥2\.0k tokens/);
  await userEvent.unhover(statusWrap);

  await userEvent.type(screen.getByRole("textbox", { name: "发给 lead" }), "先合并接口{Enter}");
  await waitFor(() => expect(calls.some((call) => call.method === "team/post")).toBe(true));
  const post = calls.find((call) => call.method === "team/post")!.request as { operationId: string; message: unknown };
  expect(post.operationId).toMatch(/.+/);
  expect(post.message).toEqual({ to: ["lead"], channel: "board", text: "先合并接口" });

  const reads = calls.filter((call) => call.method === "team/read").length;
  boardMessages = [
    ...boardMessages,
    { id: "msg-live", seq: 5, channel: "board", author: "builder", authorKind: "member", toAll: true,
      content: "实时推送的新进展", createdAt: base + 8 * 60_000 },
  ];
  act(() => { for (const listener of listeners) listener({ method: "team/updated", params: { sessionId } }); });
  await waitFor(() => expect(calls.filter((call) => call.method === "team/read").length).toBeGreaterThan(reads));
  expect(await screen.findByText("实时推送的新进展")).toBeInTheDocument();

  // Manual refresh button re-syncs team state and board messages even without new notifications.
  const readsBeforeManual = calls.filter((call) => call.method === "team/read").length;
  boardMessages = [
    ...boardMessages,
    { id: "msg-manual", seq: 6, channel: "board", author: "critic", authorKind: "member", toAll: true,
      content: "手动刷新后的最新汇报", createdAt: base + 9 * 60_000 },
  ];
  await userEvent.click(screen.getByRole("button", { name: "刷新" }));
  await waitFor(() => expect(calls.filter((call) => call.method === "team/read").length).toBeGreaterThan(readsBeforeManual));
  expect(await screen.findByText("手动刷新后的最新汇报")).toBeInTheDocument();

  const before = calls.filter((call) => call.method === "team/read").length;
  act(() => { for (const listener of listeners) listener({ method: "run/completed", params: { sessionId: "member-session-1", run: {} as never } }); });
  await waitFor(() => expect(calls.filter((call) => call.method === "team/read").length).toBeGreaterThan(before));
});

it("applies a channel filter chosen while an earlier board page is still loading", async () => {
  const original = runtime.getMockImplementation()!;
  let release: (() => void) | undefined;
  boardMessages = [
    ...boardMessages,
    { id: "msg-board", seq: 4, channel: "board", author: "critic", authorKind: "member", toAll: true,
      content: "公开的复核进展", createdAt: 3 },
  ];
  runtime.mockImplementation(async (method: keyof TeamMethods, request: unknown): Promise<unknown> => {
    if (method !== "team/messages") return original(method, request);
    calls.push({ method, request });
    const value = request as { after?: number; channel?: string };
    if (!release) await new Promise<void>((resolve) => { release = resolve; });
    const fresh = boardMessages.filter((message) => Number(message.seq ?? 0) > (value.after ?? 0)
      && (!value.channel || message.channel === value.channel));
    const nextCursor = fresh.length > 0 ? Math.max(...fresh.map((message) => Number(message.seq ?? 0))) : value.after ?? 0;
    return { messages: fresh, nextCursor, hasMore: false };
  });
  try {
    render(<Harness />);
    await waitFor(() => expect(release).toBeDefined());
    // The first, unfiltered page is still in flight when the user picks 私聊.
    await userEvent.click(within(screen.getByRole("group", { name: "按频道筛选" })).getByRole("button", { name: "私聊" }));
    await act(async () => { release!(); });
    expect(await screen.findByText("接口已完成")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("公开的复核进展")).not.toBeInTheDocument());
    const pages = calls.filter((call) => call.method === "team/messages").map((call) => call.request as { after: number; channel?: string });
    expect(pages.at(-1)).toMatchObject({ after: 0, channel: "direct" });
  } finally {
    runtime.mockImplementation(original);
  }
});

it("groups tasks by milestone by default, supports switching to status, rejects under review and marks gates", async () => {
  render(<Harness />);
  await userEvent.click(await screen.findByRole("tab", { name: /任务/ }));
  expect(screen.getByRole("region", { name: "M1" })).toBeInTheDocument();
  expect(document.querySelector('[data-task-kind="gate"]')).toBeInTheDocument();

  // Switch grouping to status and reject in_review task.
  await userEvent.click(screen.getByRole("button", { name: "切换任务分组方式" }));
  const review = screen.getByRole("region", { name: "待审查" });
  await userEvent.click(within(review).getByRole("button", { name: "退回" }));
  const reason = screen.getByRole("textbox", { name: "原因" });
  await userEvent.type(reason, "缺少测试");
  await userEvent.click(within(review).getAllByRole("button", { name: "退回" })[0]!);
  await waitFor(() => expect(calls.some((call) => call.method === "team/taskUpdate")).toBe(true));
  expect((calls.find((call) => call.method === "team/taskUpdate")!.request as { request: unknown }).request)
    .toEqual({ op: "reject", key: "api.1", note: "缺少测试" });
});

it("sorts milestones naturally regardless of backend task order", async () => {
  state = {
    team: {
      ...team,
      tasks: [
        { id: "t-m2", key: "build", title: "制作成品", status: "done", milestone: "M2-制作", version: 1, createdBy: "lead", updatedAt: 3 },
        { id: "t-m3", key: "polish", title: "终版打磨", status: "open", milestone: "M3-打磨", version: 1, createdBy: "lead", updatedAt: 4 },
        { id: "t-m1", key: "plan", title: "策划大纲", status: "done", milestone: "M1-策划", version: 1, createdBy: "lead", updatedAt: 2 },
        { id: "t-other", key: "misc", title: "杂项", status: "open", version: 1, createdBy: "lead", updatedAt: 1 },
      ],
    },
    usage: [],
  };
  render(<Harness />);
  await userEvent.click(await screen.findByRole("tab", { name: /任务/ }));
  const headings = Array.from(document.querySelectorAll(".team-tasks__group h4")).map((node) => node.textContent);
  expect(headings).toEqual(["M1-策划", "M2-制作", "M3-打磨", "其他"]);

  // Reopening a done milestone task keeps "重新打开…" in the menu and uses a 2-row textarea + stacked 确认/取消 actions.
  await userEvent.click(screen.getByRole("button", { name: "策划大纲 操作" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "重新打开…" }));
  const textarea = screen.getByRole("textbox", { name: "原因" });
  expect(textarea.tagName).toBe("TEXTAREA");
  expect(textarea).toHaveAttribute("rows", "2");
  const actionStack = document.querySelector(".team-task__reject-actions")!;
  const stackButtons = within(actionStack as HTMLElement).getAllByRole("button").map((btn) => btn.textContent);
  expect(stackButtons).toEqual(["确认", "取消"]);
  await userEvent.type(textarea, "需要补充大纲细节");
  await userEvent.click(within(actionStack as HTMLElement).getByRole("button", { name: "确认" }));
  await waitFor(() => expect(calls.some((call) => call.method === "team/taskUpdate")).toBe(true));
  expect((calls.find((call) => call.method === "team/taskUpdate")!.request as { request: unknown }).request)
    .toEqual({ op: "reopen", key: "plan", note: "需要补充大纲细节" });
});

it("lists members with visible status, resolved model names, structured hover tooltip and confirms dissolve via overflow menu", async () => {
  render(<Harness />);
  await userEvent.click(await screen.findByRole("tab", { name: /成员/ }));
  const builderBtn = screen.getByRole("button", { name: /builder · 工作 · 正在执行/ });
  const criticBtn = screen.getByRole("button", { name: /critic · 核查 · 空闲/ });
  // Per-member usage stays out of the card face and is presented with units inside the hover tooltip.
  expect(builderBtn).not.toHaveTextContent("≥2.0k");
  expect(builderBtn).toHaveTextContent("正在执行");
  expect(criticBtn).toHaveTextContent("空闲");
  // Raw model UUID never leaks; only heterogeneous model (critic) appears on the card face.
  expect(builderBtn).not.toHaveTextContent("custom-uuid-1");
  expect(criticBtn).toHaveTextContent("Anthropic / Claude Sonnet 4.6");

  // Hovering renders the multi-line structured tooltip card with separate label/value rows and scaled usage units.
  await userEvent.hover(builderBtn);
  const tooltip = await screen.findByRole("tooltip");
  expect(tooltip).toHaveClass("team-detail-tooltip");
  const rows = Array.from(tooltip.querySelectorAll(".team-tooltip-card__row"));
  expect(rows.length).toBeGreaterThanOrEqual(4);
  expect(tooltip).toHaveTextContent("状态正在执行");
  expect(tooltip).toHaveTextContent("模型深度求索 / DeepSeek-V4.1-Flash");
  expect(tooltip).toHaveTextContent("工作区独立 Worktree");
  expect(tooltip).toHaveTextContent("用量≥2.0k tokens");
  await userEvent.unhover(builderBtn);

  // Open overflow menu for auto-wake and dissolve.
  await userEvent.click(screen.getByRole("button", { name: "更多团队操作" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "解散团队…" }));
  expect(calls.some((call) => call.method === "team/setStatus")).toBe(false);
  await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "解散" }));
  await waitFor(() => expect(calls.find((call) => call.method === "team/setStatus")?.request)
    .toEqual(expect.objectContaining({ teamId: "team-1", status: "dissolving" })));
});

it("renders clean member detail without duplicated brief bar or team wake markers and removes via overflow menu", async () => {
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: {
    ...window.eidosRuntime,
    readSession: vi.fn(async () => ({
      session: { id: "member-session-1", title: "builder" },
      runs: [{
        id: "run-b",
        sessionId: "member-session-1",
        status: "running",
        allowedActions: ["cancel"],
        modelId: "custom-uuid-1",
        modelStepCount: 1,
        createdAt: 1,
        updatedAt: 2,
        teamOrigin: { origin: "wake", member: "builder" },
      }],
      items: [
        {
          id: "item-1",
          sessionId: "member-session-1",
          runId: "run-b",
          kind: "user_message",
          ordinal: 1,
          status: "completed",
          content: "实现接口",
          createdAt: 1,
        },
        {
          id: "item-2",
          sessionId: "member-session-1",
          runId: "run-b",
          kind: "assistant_message",
          ordinal: 2,
          status: "completed",
          content: "接口逻辑已完成并自测通过。",
          createdAt: 2,
        },
      ],
    })),
  } });
  render(<Harness memberName="builder" />);
  expect(await screen.findByRole("article", { name: "成员 builder" })).toBeInTheDocument();
  expect(await screen.findByText("接口逻辑已完成并自测通过。")).toBeInTheDocument();
  expect(document.querySelector(".team-member-detail__brief")).not.toBeInTheDocument();
  expect(document.querySelector(".team-feed-marker")).not.toBeInTheDocument();
  expect(screen.queryByText(/团队续跑/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "停止" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "成员操作" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "移除成员…" }));
  expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
});

it("renders wide dual-pane command center when panel width >= 760px", async () => {
  const originalRO = globalThis.ResizeObserver;
  class WideResizeObserver {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) { this.cb = cb; }
    observe(target: Element) {
      this.cb([{ target, contentRect: { width: 920, height: 700 } as DOMRectReadOnly } as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = WideResizeObserver as unknown as typeof ResizeObserver;
  try {
    render(<Harness />);
    expect(await screen.findByText("接口已完成")).toBeInTheDocument();
    // Dual-pane: board on left AND members + tasks rail simultaneously visible on right, no narrow tablist.
    expect(screen.queryByRole("tablist", { name: "团队视图" })).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "成员与任务概览" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /builder · 工作 · 正在执行/ })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "M1" })).toBeInTheDocument();
    expect(screen.getByText("1/2 运行中")).toBeInTheDocument();
    // Wide header places Refresh button next to status badge and moves cumulative usage into status hover tooltip.
    expect(document.querySelector(".team-panel__tokens")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toBeInTheDocument();
    const wideStatusWrap = document.querySelector(".team-panel__status-wrap")!;
    await userEvent.hover(wideStatusWrap);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/累计用量≥2\.0k tokens/);
    await userEvent.unhover(wideStatusWrap);
  } finally {
    globalThis.ResizeObserver = originalRO;
  }
});

it("lets the user drop members, switch workspaces and toggle auto-wake before confirming the roster", async () => {
  const proposal: TeamProposalView = {
    id: "proposal-1", sessionId, runId: "run-1", decision: "pending", createdAt: 1,
    proposal: { objective: "重构登录模块", members: [
      { name: "builder", role: "worker", brief: "实现接口", workspace: "worktree" },
      { name: "critic", role: "verifier", brief: "审查", workspace: "shared" },
    ], tasks: [{ key: "api.1", title: "接口", owner: "builder" }] },
  };
  const decide = vi.fn();
  render(<TeamRoster proposal={proposal} approvalMode="manual" modelLabel="Model A" worktreeAvailable busy={false}
    error={undefined} onDecide={decide} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "保留 critic" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "builder 的工作区" }), "shared");
  await userEvent.click(screen.getByRole("switch", { name: "自动续跑" }));
  await userEvent.click(screen.getByRole("button", { name: "组建团队" }));
  expect(decide).toHaveBeenCalledWith({ confirmed: true, removedMembers: ["critic"], workspaces: { builder: "shared" },
    approvalMode: "manual", autoWakeLead: false });
});

it("renders single-row member/task roster with 1-line expandable summary, on-demand brief drawer and unassigned strip", async () => {
  const longSummary = "第一阶段先由 scout 建立事实底座，第二阶段由 analyst 产出逐页叙事大纲，第三阶段由 verifier 完成成片视觉核验与门禁放行。";
  const proposal: TeamProposalView = {
    id: "proposal-2", sessionId, runId: "run-2", decision: "pending", createdAt: 1,
    proposal: {
      objective: "制作 GTA6 首发复盘 PPT",
      summary: longSummary,
      members: [
        { name: "scout", role: "explorer", brief: "详细事实核验简报：核对发售时间与首日销量口径。", workspace: "shared" },
        { name: "verifier", role: "verifier", brief: "独立门禁核查简报。", workspace: "shared" },
      ],
      tasks: [
        { key: "facts", title: "建立事实底座", owner: "scout" },
        { key: "m1_gate", title: "门禁：事实口径核验", owner: "verifier", kind: "gate", milestone: "M1" },
        { key: "m2_gate", title: "门禁：成片视觉验收", owner: "verifier", kind: "gate", milestone: "M2" },
        { key: "build", title: "页面 SVG 制作与 PPTX 导出" },
      ],
    },
  };
  render(<TeamRoster proposal={proposal} approvalMode="full_access" modelLabel="DeepSeek-V4.1-Flash" worktreeAvailable={false}
    busy={false} error={undefined} onDecide={vi.fn()} />);

  // 1-line summary is clamped by default and expands on click.
  const summaryEl = screen.getByText(longSummary);
  expect(summaryEl).toHaveClass("team-roster__summary--clamped");
  await userEvent.click(screen.getByRole("button", { name: "展开方案说明" }));
  expect(summaryEl).not.toHaveClass("team-roster__summary--clamped");
  await userEvent.click(screen.getByRole("button", { name: "收起方案说明" }));
  expect(summaryEl).toHaveClass("team-roster__summary--clamped");

  // Default single-row view shows Chinese task titles & +1 badge, hiding full briefs and raw keys.
  expect(screen.getByText("建立事实底座")).toBeInTheDocument();
  expect(screen.getByText("门禁：事实口径核验")).toBeInTheDocument();
  expect(screen.getByText("+1")).toBeInTheDocument();
  expect(screen.queryByText(/详细事实核验简报/)).not.toBeInTheDocument();
  expect(screen.queryByText("#facts")).not.toBeInTheDocument();

  // Clicking a member row expands its full brief and complete task list along the rail.
  await userEvent.click(screen.getByRole("button", { name: "展开 verifier 详情" }));
  expect(screen.getByText("独立门禁核查简报。")).toBeInTheDocument();
  expect(screen.getByText("门禁：成片视觉验收")).toBeInTheDocument();
  expect(screen.getByText("#m1_gate")).toBeInTheDocument();
  expect(screen.getByText("#m2_gate")).toBeInTheDocument();

  // Unassigned / Lead tasks sit in their own labelled strip without raw keys exposed on the face.
  const leadStrip = screen.getByLabelText("待领取任务");
  expect(leadStrip).toHaveTextContent("未指派 / Lead");
  expect(leadStrip).toHaveTextContent("页面 SVG 制作与 PPTX 导出");
  expect(leadStrip).not.toHaveTextContent("build");

  // full_access carries warning styling.
  expect(screen.getByText("完全访问")).toHaveClass("team-roster__setting-chip--warn");
});

it("hides workspace selectors and keeps every member shared when the Session has no Git", () => {
  const proposal: TeamProposalView = {
    id: "proposal-1", sessionId, runId: "run-1", decision: "pending", createdAt: 1,
    proposal: { objective: "调研", members: [{ name: "scout", role: "explorer", brief: "调研", workspace: "worktree" }] },
  };
  const decide = vi.fn();
  render(<TeamRoster proposal={proposal} approvalMode="full_access" modelLabel={undefined} worktreeAvailable={false} busy={false}
    error={undefined} onDecide={decide} />);
  expect(screen.queryByRole("combobox", { name: "scout 的工作区" })).not.toBeInTheDocument();
  screen.getByRole("button", { name: "组建团队" }).click();
  expect(decide).toHaveBeenCalledWith(expect.objectContaining({ workspaces: { scout: "shared" },
    approvalMode: "full_access", fullAccessConfirmation: "full-access-v1" }));
});

it("hides the header status badge when an active team has no running members and restores it when work resumes or status changes", async () => {
  const idleMembers: TeamView["members"] = team.members.map((entry) => ({ ...entry, runStatus: "succeeded" }));
  state = { team: { ...team, status: "active", members: idleMembers }, usage: [] };
  const { rerender } = render(<Harness />);
  expect(await screen.findByText("接口已完成")).toBeInTheDocument();
  expect(document.querySelector(".team-panel__status-wrap")).not.toBeInTheDocument();
  expect(screen.queryByText("运行中")).not.toBeInTheDocument();

  // When a member begins executing, the running status badge appears.
  state = { team, usage: [] };
  act(() => { for (const listener of listeners) listener({ method: "team/updated", params: { sessionId } }); });
  await waitFor(() => expect(document.querySelector(".team-panel__status-wrap")).toBeInTheDocument());
  expect(screen.getByText("运行中")).toBeInTheDocument();

  // Non-active lifecycle statuses stay visible even when no members are running.
  for (const [status, label] of [["paused", "已暂停"], ["dissolving", "正在解散"], ["dissolved", "已解散"]] as const) {
    state = { team: { ...team, status, members: idleMembers }, usage: [] };
    act(() => { for (const listener of listeners) listener({ method: "team/updated", params: { sessionId } }); });
    await waitFor(() => expect(screen.getByText(label)).toBeInTheDocument());
    expect(document.querySelector(".team-panel__status-wrap")).toBeInTheDocument();
  }

  // Re-rendering with active + idle hides the badge again.
  state = { team: { ...team, status: "active", members: idleMembers }, usage: [] };
  act(() => { for (const listener of listeners) listener({ method: "team/updated", params: { sessionId } }); });
  rerender(<Harness />);
  await waitFor(() => expect(document.querySelector(".team-panel__status-wrap")).not.toBeInTheDocument());
});

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { Automation, AutomationResponse } from "../../../../shared/automation.js";
import type { RuntimeNotification } from "../../../../shared/domain-contracts.js";
import type { ModelOption, Session } from "../../contracts.js";
import { AutomationPage } from "./AutomationPage.js";
import { AutomationForm } from "./AutomationForm.js";
import { formatAutomationTime } from "./automation-time.js";

const model = { id: "model", name: "本地配置模型", reasoning: { defaultSelection: "high", selections: ["none", "high"] } } as ModelOption;
const session = { id: "22222222-2222-2222-2222-222222222222", title: "部署检查", projectless: true } as Session;
const task: Automation = {
  id: "11111111-1111-1111-1111-111111111111", revision: 2, name: "检查部署", prompt: "检查部署状态。",
  kind: "cron", schedule: { expression: "*/10 * * * *", timezone: "Asia/Shanghai", runAt: null },
  status: "ACTIVE", model: { modelId: "model", reasoningSelection: "high" }, approvalMode: "manual",
  target: { kind: "existing_session", sessionId: session.id, projectId: null, execution: { executionMode: "local", baseRef: null } },
  createdAt: 1, updatedAt: 2,
};

beforeEach(() => {
  window.eidosRuntime = {
    automation: vi.fn(async ({ command }) => command.mode === "view" && !command.id
      ? { kind: "list", items: [task] } : { kind: "detail", automation: task }),
    automationHistory: vi.fn(async () => ({ kind: "history", occurrences: [] })),
    automationRunNow: vi.fn(async () => ({ kind: "run" })),
    onNotification: vi.fn(() => () => {}),
  } as unknown as typeof window.eidosRuntime;
});

describe("local automation management", () => {
  it("displays history in the occurrence snapshot timezone after the plan timezone changes", async () => {
    const scheduledFor = "2026-10-28T00:30:00Z";
    vi.mocked(window.eidosRuntime.automationHistory).mockResolvedValue({ kind: "history", occurrences: [{
      id: "occurrence", automationId: task.id, revision: 1, scheduledFor, status: "submitted", createdAt: 1,
      snapshot: { ...task, schedule: { expression: "30 20 * * *", timezone: "America/New_York" } },
    }] });
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} initialAutomationId={task.id} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    expect(await screen.findByText((_, element) => element?.tagName === "SPAN"
      && element.textContent?.startsWith(formatAutomationTime(scheduledFor, "America/New_York")) === true)).toHaveTextContent("2026/10/27");
  });

  it("requires a danger confirmation before saving a new full-access plan", async () => {
    const save = vi.fn(async (_request: unknown, _confirmation?: string) => {});
    render(<AutomationForm projects={[]} sessions={[]} models={[model]} busy={false} onSave={save} />);
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "本地整理" } });
    fireEvent.change(screen.getByRole("textbox", { name: "任务内容" }), { target: { value: "整理目录。" } });
    const approval = screen.getByRole("combobox", { name: "审批模式" });
    fireEvent.change(approval, { target: { value: "full_access" } });
    const dialog = screen.getByRole("alertdialog", { name: "开启完全访问？" });
    expect(dialog).toHaveTextContent("此计划今后的每次执行");
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "取消" })).toHaveFocus());
    expect(approval).toHaveValue("auto_review");
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    expect(save).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(approval).toHaveValue("auto_review");
    fireEvent.change(approval, { target: { value: "full_access" } });
    fireEvent.click(screen.getByRole("button", { name: "开启完全访问" }));
    expect(approval).toHaveValue("full_access");
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ mode: "create", approvalMode: "full_access" }), "full-access-v1"));
  });

  it("confirms full-access edits and preserves already confirmed plans on ordinary edits", async () => {
    const save = vi.fn(async (_request: unknown, _confirmation?: string) => {});
    const { rerender } = render(<AutomationForm task={task} projects={[]} sessions={[session]} models={[model]} busy={false} onSave={save} />);
    fireEvent.change(screen.getByRole("combobox", { name: "审批模式" }), { target: { value: "full_access" } });
    fireEvent.click(screen.getByRole("button", { name: "开启完全访问" }));
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ mode: "update", id: task.id, expectedRevision: 2,
      changes: { approvalMode: "full_access" } }, "full-access-v1"));
    const confirmed: Automation = { ...task, approvalMode: "full_access", fullAccessConfirmation: "full-access-v1" };
    save.mockClear();
    rerender(<AutomationForm key="confirmed" task={confirmed} projects={[]} sessions={[session]} models={[model]} busy={false} onSave={save} />);
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "新名称" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith({ mode: "update", id: task.id, expectedRevision: 2, changes: { name: "新名称" } }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("cancels the full-access dialog with Escape and forwards confirmed Desktop authorization", async () => {
    const close = vi.fn();
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={close} onOpenSession={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "本地整理" } });
    fireEvent.change(screen.getByRole("textbox", { name: "任务内容" }), { target: { value: "整理目录。" } });
    const approval = screen.getByRole("combobox", { name: "审批模式" });
    fireEvent.change(approval, { target: { value: "full_access" } });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    expect(approval).toHaveValue("auto_review");
    fireEvent.change(approval, { target: { value: "full_access" } });
    fireEvent.click(screen.getByRole("button", { name: "开启完全访问" }));
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(window.eidosRuntime.automation).toHaveBeenCalledWith(expect.objectContaining({
      fullAccessConfirmation: "full-access-v1", command: expect.objectContaining({ mode: "create", approvalMode: "full_access" }),
    })));
  });
  it("opens a source task directly even when it is outside the loaded task page", async () => {
    vi.mocked(window.eidosRuntime.automation).mockImplementation(async ({ command }) => command.id
      ? { kind: "detail", automation: task } : { kind: "list", items: [] });
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} initialAutomationId={task.id}
      onClose={vi.fn()} onOpenSession={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: task.name })).toBeInTheDocument();
    expect(window.eidosRuntime.automation).toHaveBeenCalledWith({ command: { mode: "view", id: task.id } });
    expect(window.eidosRuntime.automationHistory).toHaveBeenCalledWith({ id: task.id });
  });
  it("keeps upcoming tasks separate from paused and fired one-time tasks", async () => {
    const waiting = { ...task, id: "33333333-3333-3333-3333-333333333333", name: "稍后提醒", kind: "at" as const,
      schedule: { runAt: "2026-10-17T09:00:00+08:00", timezone: "Asia/Shanghai" } };
    const paused = { ...task, id: "44444444-4444-4444-4444-444444444444", name: "暂停检查", status: "PAUSED" as const };
    const pausedOnce = { ...waiting, id: "55555555-5555-5555-5555-555555555555", name: "暂停提醒", status: "PAUSED" as const };
    const fired = { ...waiting, id: "66666666-6666-6666-6666-666666666666", name: "提醒收尾", status: "PAUSED" as const, pauseReason: "one_shot_fired" };
    const tasks = [task, waiting, paused, pausedOnce, fired];
    vi.mocked(window.eidosRuntime.automation).mockImplementation(async ({ command }) => command.id
      ? { kind: "detail", automation: tasks.find((item) => item.id === command.id)! }
      : { kind: "list", items: tasks });
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    const upcoming = within(await screen.findByRole("region", { name: "即将执行" }));
    const inactive = within(screen.getByRole("region", { name: "已完成与暂停" }));
    expect(upcoming.getAllByRole("button")).toHaveLength(2);
    expect(upcoming.getByRole("button", { name: /稍后提醒/ })).toBeInTheDocument();
    expect(inactive.getAllByRole("button")).toHaveLength(3);
    expect(inactive.getByRole("button", { name: /暂停检查/ })).toBeInTheDocument();
    expect(inactive.getByRole("button", { name: /暂停提醒/ })).toHaveTextContent("已暂停 · 一次性");
    expect(inactive.getByRole("button", { name: /提醒收尾/ })).toHaveTextContent("已结束 · 一次性");
    fireEvent.click(inactive.getByRole("button", { name: /提醒收尾/ }));
    expect(await screen.findByRole("heading", { name: "提醒收尾" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "删除计划" })).toBeEnabled();
  });

  it("moves a selected task between groups when paused and resumed", async () => {
    let saved = task;
    vi.mocked(window.eidosRuntime.automation).mockImplementation(async ({ command }) => {
      if (command.mode === "update") saved = { ...saved, status: command.changes!.status!, revision: saved.revision + 1 };
      return command.mode === "view" && !command.id ? { kind: "list", items: [saved] } : { kind: "mutation", automation: saved };
    });
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /检查部署/ }));
    fireEvent.click(await screen.findByRole("button", { name: "暂停" }));
    const inactive = within(screen.getByRole("region", { name: "已完成与暂停" }));
    expect(await inactive.findByRole("button", { name: /检查部署/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    const upcoming = within(screen.getByRole("region", { name: "即将执行" }));
    expect(await upcoming.findByRole("button", { name: /检查部署/ })).toHaveAttribute("aria-pressed", "true");
    expect(inactive.queryByRole("button", { name: /检查部署/ })).not.toBeInTheDocument();
  });

  it("moves a one-time task after a scheduled execution notification", async () => {
    let saved: Automation = { ...task, kind: "at", name: "稍后提醒",
      schedule: { runAt: "2026-10-17T09:00:00+08:00", timezone: "Asia/Shanghai" } };
    const listeners = new Set<(notification: RuntimeNotification) => void>();
    vi.mocked(window.eidosRuntime.onNotification).mockImplementation((listener) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    });
    vi.mocked(window.eidosRuntime.automation).mockImplementation(async () => ({ kind: "list", items: [saved] }));
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    const upcoming = within(await screen.findByRole("region", { name: "即将执行" }));
    expect(upcoming.getByRole("button", { name: /稍后提醒/ })).toBeInTheDocument();
    saved = { ...saved, status: "PAUSED", pauseReason: "one_shot_fired", revision: saved.revision + 1 };
    await act(async () => {
      for (const listener of listeners) listener({ method: "automation/changed", params: { id: saved.id } });
    });
    const inactive = within(screen.getByRole("region", { name: "已完成与暂停" }));
    expect(await inactive.findByRole("button", { name: /稍后提醒/ })).toHaveTextContent("已结束 · 一次性");
    expect(upcoming.queryByRole("button", { name: /稍后提醒/ })).not.toBeInTheDocument();
  });

  it("searches both groups and retains both empty states", async () => {
    const paused = { ...task, id: "44444444-4444-4444-4444-444444444444", name: "暂停检查", status: "PAUSED" as const };
    const fired = { ...task, id: "55555555-5555-5555-5555-555555555555", name: "提醒收尾", kind: "at" as const,
      schedule: { runAt: "2026-10-17T09:00:00+08:00", timezone: "Asia/Shanghai" }, status: "PAUSED" as const, pauseReason: "one_shot_fired" };
    vi.mocked(window.eidosRuntime.automation).mockResolvedValue({ kind: "list", items: [task, paused, fired] });
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    await screen.findByRole("region", { name: "即将执行" });
    fireEvent.click(screen.getByRole("button", { name: "搜索任务" }));
    const search = screen.getByRole("searchbox", { name: "搜索定时任务" });
    fireEvent.change(search, { target: { value: "检查" } });
    expect(screen.getByRole("button", { name: /检查部署/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /暂停检查/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /提醒收尾/ })).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: "没有这个任务" } });
    expect(screen.getAllByText("没有匹配的任务。")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "搜索任务" }));
    expect(screen.getByRole("button", { name: /提醒收尾/ })).toBeInTheDocument();
  });

  it("loads paused tasks from later pages into the inactive group", async () => {
    const paused = { ...task, id: "44444444-4444-4444-4444-444444444444", name: "旧的暂停任务", status: "PAUSED" as const };
    vi.mocked(window.eidosRuntime.automation).mockImplementation(async ({ command }) => command.cursor
      ? { kind: "list", items: [paused] } : { kind: "list", items: [task], nextCursor: "next" });
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "加载更多任务" }));
    const inactive = within(screen.getByRole("region", { name: "已完成与暂停" }));
    expect(await inactive.findByRole("button", { name: /旧的暂停任务/ })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "即将执行" })).getByRole("button", { name: /检查部署/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载更多任务" })).not.toBeInTheDocument();
  });

  it("defaults new tasks to auto review and a new projectless Session", async () => {
    const save = vi.fn(async () => {});
    render(<AutomationForm projects={[]} sessions={[session]} models={[model]} currentSessionId={session.id} busy={false} onSave={save} />);
    expect(screen.getByRole("combobox", { name: "审批模式" })).toHaveValue("auto_review");
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "日报" } });
    fireEvent.change(screen.getByRole("textbox", { name: "任务内容" }), { target: { value: "生成日报。" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    const request = save.mock.calls[0]?.[0] as unknown as Record<string, unknown>;
    expect(request).toMatchObject({ mode: "create", projectId: null, approvalMode: "auto_review" });
    expect(request).not.toHaveProperty("sessionId");
  });

  it("includes the selected existing Session explicitly", async () => {
    const save = vi.fn(async (_request: unknown) => {});
    render(<AutomationForm projects={[]} sessions={[session]} models={[model]} currentSessionId={session.id} busy={false} onSave={save} />);
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "跟进" } });
    fireEvent.change(screen.getByRole("textbox", { name: "任务内容" }), { target: { value: "继续检查。" } });
    fireEvent.change(screen.getByRole("combobox", { name: "执行会话" }), { target: { value: "session" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ sessionId: session.id })));
  });

  it("offers only configured reasoning levels and disables changes while saving", () => {
    const { rerender } = render(<AutomationForm projects={[]} sessions={[]} models={[model]} busy={false} onSave={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /本地配置模型.*思考强度/ }));
    expect(screen.getByRole("slider", { name: "本地配置模型 思考强度" })).toHaveAttribute("max", "1");
    expect(screen.queryByRole("option", { name: "max" })).not.toBeInTheDocument();
    rerender(<AutomationForm projects={[]} sessions={[]} models={[model]} busy onSave={async () => {}} />);
    expect(screen.getByRole("textbox", { name: "名称" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "审批模式" })).toBeDisabled();
  });

  it("edits one field without resubmitting target, schedule, model or approval", async () => {
    const save = vi.fn(async (_request: unknown) => {});
    render(<AutomationForm task={task} projects={[]} sessions={[session]} models={[model]} busy={false} onSave={save} />);
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "新名称" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ mode: "update", id: task.id, expectedRevision: 2, changes: { name: "新名称" } }));
  });

  it("shows floating toast instead of inline error when saving task without changes", async () => {
    const save = vi.fn(async (_request: unknown) => {});
    const onShowToast = vi.fn();
    render(<AutomationForm task={task} projects={[]} sessions={[session]} models={[model]} busy={false} onSave={save} onShowToast={onShowToast} />);
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    expect(save).not.toHaveBeenCalled();
    expect(onShowToast).toHaveBeenCalledWith("配置没有变化。", "info");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("preserves a saved Session that is outside the loaded sidebar page", async () => {
    const save = vi.fn(async (_request: unknown) => {});
    render(<AutomationForm task={task} projects={[]} sessions={[]} models={[model]} busy={false} onSave={save} />);
    expect(screen.getByRole("combobox", { name: "目标会话" })).toHaveValue(session.id);
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "继续检查" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ changes: { name: "继续检查" } })));
  });

  it("previews time without executing or saving task content", async () => {
    vi.mocked(window.eidosRuntime.automation).mockResolvedValue({ kind: "preview", nextRuns: ["2026-10-16T01:00:00Z"] } as AutomationResponse);
    const save = vi.fn(async () => {});
    render(<AutomationForm projects={[]} sessions={[]} models={[model]} busy={false} onSave={save} />);
    fireEvent.click(screen.getByRole("button", { name: "预览执行时间" }));
    await waitFor(() => expect(window.eidosRuntime.automation).toHaveBeenCalledWith({ command: expect.objectContaining({ mode: "preview", kind: "cron" }) }));
    expect(save).not.toHaveBeenCalled();
    expect(await screen.findByRole("list")).toBeInTheDocument();
  });

  it("loads real revisions before pause and opens execution results", async () => {
    const open = vi.fn();
    vi.mocked(window.eidosRuntime.automationHistory).mockResolvedValue({ kind: "history", occurrences: [{ id: "o", automationId: task.id, revision: 2, scheduledFor: "2026-10-15T01:00:00Z", status: "submitted", snapshot: task, sessionId: session.id, runId: "r", createdAt: 1 }] });
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={open} />);
    fireEvent.click(await screen.findByRole("button", { name: /检查部署/ }));
    fireEvent.click(await screen.findByRole("button", { name: "查看执行结果" }));
    expect(open).toHaveBeenCalledWith(session.id);
    fireEvent.click(screen.getByRole("button", { name: "暂停" }));
    await waitFor(() => expect(window.eidosRuntime.automation).toHaveBeenCalledWith(expect.objectContaining({ command: { mode: "update", id: task.id, expectedRevision: 2, changes: { status: "PAUSED" } } })));
  });

  it("shows a concrete revision conflict in floating toast and supports manual/auto dismissal", async () => {
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /检查部署/ }));
    await screen.findByRole("button", { name: "暂停" });
    vi.mocked(window.eidosRuntime.automation).mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:AUTOMATION_REVISION_CONFLICT"));
    fireEvent.click(screen.getByRole("button", { name: "暂停" }));

    // Floating alert displays message without the old inline refresh button
    expect(await screen.findByRole("alert")).toHaveTextContent("任务已被修改，请重新打开后再编辑");
    expect(screen.queryByRole("button", { name: "刷新列表" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();

    // Supports manual close
    const closeBtn = screen.getByRole("button", { name: "关闭提示" });
    fireEvent.click(closeBtn);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("automatically dismisses floating toast after 3.5 seconds", async () => {
    vi.useFakeTimers();
    try {
      render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      fireEvent.click(screen.getByRole("button", { name: /检查部署/ }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      vi.mocked(window.eidosRuntime.automation).mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:AUTOMATION_REVISION_CONFLICT"));
      fireEvent.click(screen.getByRole("button", { name: "暂停" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      expect(screen.getByRole("alert")).toBeInTheDocument();

      // 推进 3500 毫秒后自动关闭
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3500);
      });

      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("calls onClose on '返回 Eidos' button click and Escape key", async () => {
    const onClose = vi.fn();
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={onClose} onOpenSession={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /检查部署/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "返回 Eidos" }));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("displays hero state with template cards and prefills form on template click", async () => {
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /检查部署/ })).toBeInTheDocument();
    expect(screen.getByText("安排任务")).toBeInTheDocument();
    expect(screen.getByText("Eidos 可以帮你处理持续性任务，让你不必亲自动手。")).toBeInTheDocument();

    const templateBtn = screen.getByRole("button", { name: /每日一个为什么/ });
    fireEvent.click(templateBtn);

    expect(screen.getByRole("textbox", { name: "名称" })).toHaveValue("每日一个为什么");
    expect(screen.getByRole("textbox", { name: "任务内容" })).toHaveValue(
      "随机挑选一个有趣的冷知识或生活百科问题，并给出详细、有趣、通俗易懂的解答。每次问题尽量不重复，覆盖科学、生活、历史、自然、食物、文化、动物、人体等多个领域；先抛出问题再揭晓答案；语气轻松有趣；答案控制在 200-300 字。",
    );

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByText("安排任务")).toBeInTheDocument();
  });

  it("saves the reasoning selected through the shared picker", async () => {
    const save = vi.fn(async (_request: unknown) => {});
    render(<AutomationForm task={task} projects={[]} sessions={[session]} models={[model]} busy={false} onSave={save} />);
    fireEvent.click(screen.getByRole("button", { name: /本地配置模型.*思考强度/ }));
    fireEvent.change(screen.getByRole("slider"), { target: { value: "0" } });
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ changes: { model: { modelId: "model", reasoningSelection: "none" } } })));
  });

});

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Automation, AutomationResponse } from "../../../../shared/automation.js";
import type { ModelOption, Session } from "../../contracts.js";
import { AutomationPage } from "./AutomationPage.js";
import { AutomationForm } from "./AutomationForm.js";

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
    expect(screen.getByRole("combobox", { name: "思考强度" }).querySelectorAll("option")).toHaveLength(3);
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

  it("shows a concrete revision conflict and preserves the saved selection", async () => {
    render(<AutomationPage projects={[]} sessions={[session]} models={[model]} onClose={vi.fn()} onOpenSession={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /检查部署/ }));
    await screen.findByRole("button", { name: "暂停" });
    vi.mocked(window.eidosRuntime.automation).mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:AUTOMATION_REVISION_CONFLICT"));
    fireEvent.click(screen.getByRole("button", { name: "暂停" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("任务已被修改，请重新打开后再编辑");
    expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
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

    const templateBtn = screen.getByRole("button", { name: /Agent 长期记忆研究速览/ });
    fireEvent.click(templateBtn);

    expect(screen.getByRole("textbox", { name: "名称" })).toHaveValue("Agent 长期记忆研究速览");
    expect(screen.getByRole("textbox", { name: "任务内容" })).toHaveValue(
      "筛选本周影响桌面 Agent 记忆架构、会话持久化和经验检索的新论文与工程实践，输出重点速览与选型建议。",
    );

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByText("安排任务")).toBeInTheDocument();
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EidosRuntimeAPI } from "../contracts.js";
import type { PlanDocument } from "../../../shared/planning.generated.js";
import { PlanPanel } from "./PlanPanel.js";

const plan: PlanDocument = {
  id: "plan-1",
  sessionId: "session-1",
  runId: "run-plan",
  revision: 1,
  title: "重构架构计划",
  markdown: "# 重构架构计划\n\n- 步骤 1: 检查代码\n- 步骤 2: 重构组件",
  sha256: "a".repeat(64),
  status: "review",
  path: "/tmp/plan.md",
  updatedAt: 1,
  executionRunId: null,
};

const historyPlan: PlanDocument = {
  id: "plan-1",
  sessionId: "session-1",
  runId: "run-plan-old",
  revision: 0,
  title: "草稿计划",
  markdown: "# 草稿计划\n\n- 初始版本",
  sha256: "b".repeat(64),
  status: "superseded",
  path: "/tmp/plan-old.md",
  updatedAt: 0,
  executionRunId: null,
};

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("PlanPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupRuntime() {
    const api: Partial<EidosRuntimeAPI> = {
      editPlan: vi.fn().mockResolvedValue({ ...plan, revision: 2, markdown: "# Updated Plan" }),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    return api;
  }

  it("renders empty state when no plan exists", () => {
    render(
      <PlanPanel
        sessionId="session-1"
        plan={undefined}
        ready={true}
        canEdit={true}
        onExecute={vi.fn()}
        onRevise={vi.fn()}
      />,
    );

    expect(screen.getByText("当前会话暂无计划。")).toBeInTheDocument();
  });

  it("renders plan details, title, badge and markdown content", () => {
    render(
      <PlanPanel
        sessionId="session-1"
        plan={plan}
        ready={true}
        canEdit={true}
        onExecute={vi.fn()}
        onRevise={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { level: 2, name: "重构架构计划" })).toBeInTheDocument();
    expect(screen.getByText("版本 1 · 待确认")).toBeInTheDocument();
    expect(screen.getByText("步骤 1: 检查代码")).toBeInTheDocument();
  });

  it("edits and saves a plan", async () => {
    const api = setupRuntime();
    const onSaved = vi.fn();
    render(
      <PlanPanel
        sessionId="session-1"
        plan={plan}
        ready={true}
        canEdit={true}
        onExecute={vi.fn()}
        onRevise={vi.fn()}
        onSaved={onSaved}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const textarea = screen.getByPlaceholderText("输入计划的 Markdown 内容…");
    expect(textarea).toHaveValue(plan.markdown);

    fireEvent.change(textarea, { target: { value: "# Updated Plan" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => {
      expect(api.editPlan).toHaveBeenCalledWith({
        planId: "plan-1",
        expectedRevision: 1,
        markdown: "# Updated Plan",
      });
      expect(onSaved).toHaveBeenCalled();
    });
  });

  it("confirms and executes a plan", async () => {
    const onExecute = vi.fn().mockResolvedValue(true);
    render(
      <PlanPanel
        sessionId="session-1"
        plan={plan}
        ready={true}
        canEdit={true}
        onExecute={onExecute}
        onRevise={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "确认并执行计划" }));
    await waitFor(() => {
      expect(onExecute).toHaveBeenCalledWith(plan);
    });
  });

  it("submits revision feedback", async () => {
    const onRevise = vi.fn().mockResolvedValue(true);
    render(
      <PlanPanel
        sessionId="session-1"
        plan={plan}
        ready={true}
        canEdit={true}
        onExecute={vi.fn()}
        onRevise={onRevise}
      />,
    );

    const reviseInput = screen.getByPlaceholderText("说明需要调整的内容，模型将重新规划…");
    fireEvent.change(reviseInput, { target: { value: "请增加单元测试步骤" } });

    fireEvent.click(screen.getByRole("button", { name: "发送修改要求" }));
    await waitFor(() => {
      expect(onRevise).toHaveBeenCalledWith(plan, "请增加单元测试步骤");
    });
  });

  it("renders history versions when historyPlans are provided", () => {
    render(
      <PlanPanel
        sessionId="session-1"
        plan={plan}
        historyPlans={[historyPlan]}
        ready={true}
        canEdit={true}
        onExecute={vi.fn()}
        onRevise={vi.fn()}
      />,
    );

    expect(screen.getByText("历史版本 (1)")).toBeInTheDocument();
    expect(screen.getByText("草稿计划 · 版本 0")).toBeInTheDocument();
  });
});

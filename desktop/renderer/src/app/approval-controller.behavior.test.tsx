import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, renderHook, screen, fireEvent, act } from "@testing-library/react";
import { useEffect } from "react";
import { useApprovalController } from "./useApprovalController.js";
import { ExecutionFeed } from "../components/ExecutionFeed.js";
import type { ApprovalRequest, EidosRuntimeAPI, Item, Run } from "../contracts.js";

const mockApprovalA: ApprovalRequest = {
  id: "app-A",
  sessionId: "session-1",
  runId: "run-1",
  itemId: "item-A",
  toolCallId: "tc-A",
  kind: "command_execution",
  summary: "Execute shell command A",
  command: "git status",
  cwd: "/workspace",
  timeoutSeconds: 30,
};

const mockApprovalB: ApprovalRequest = {
  id: "app-B",
  sessionId: "session-1",
  runId: "run-1",
  itemId: "item-B",
  toolCallId: "tc-B",
  kind: "network_access",
  summary: "Access network B",
  toolName: "fetch_api",
  target: "https://api.github.com",
  hosts: ["api.github.com"],
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useApprovalController real behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupMockRuntime(overrides: Partial<EidosRuntimeAPI> = {}) {
    const api: Partial<EidosRuntimeAPI> = {
      respondApproval: vi.fn().mockResolvedValue(true),
      ...overrides,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    return api;
  }

  it("Approval A pending does not disable Approval B and same approval cannot be approved twice", async () => {
    let resolveA!: (val: boolean) => void;
    setupMockRuntime({
      respondApproval: vi.fn().mockImplementation((id: string) => {
        if (id === "app-A") return new Promise((r) => { resolveA = r; });
        return Promise.resolve(true);
      }),
    });

    const { result } = renderHook(() => useApprovalController());

    act(() => {
      result.current[1].mergeApprovals([mockApprovalA, mockApprovalB]);
    });

    let pA1: Promise<void>;
    let pA2: Promise<void>;
    act(() => {
      pA1 = result.current[1].approve(mockApprovalA);
      pA2 = result.current[1].approve(mockApprovalA); // Duplicate call
    });

    expect(result.current[0].respondingApprovalIds.has("app-A")).toBe(true);
    expect(result.current[0].respondingApprovalIds.has("app-B")).toBe(false);

    // Approval B can be approved independently while A is pending
    await act(async () => {
      await result.current[1].approve(mockApprovalB);
    });

    expect(result.current[0].approvals.map((a) => a.id)).toEqual(["app-A"]);

    await act(async () => {
      resolveA(true);
      await Promise.all([pA1, pA2]);
    });

    expect(result.current[0].approvals).toEqual([]);
  });

  it("Approval error renders inside relevant card and B does not display A's error", async () => {
    setupMockRuntime({
      respondApproval: vi.fn().mockRejectedValue(new Error("IPC Network Failed")),
    });

    const { result } = renderHook(() => useApprovalController());

    act(() => {
      result.current[1].mergeApprovals([mockApprovalA, mockApprovalB]);
    });

    await act(async () => {
      await result.current[1].approve(mockApprovalA);
    });

    expect(result.current[0].errorsByApprovalId["app-A"]).toBe("操作失败，请查看 Runtime 日志。");
    expect(result.current[0].errorsByApprovalId["app-B"]).toBeUndefined();
  });

  it("Expired Approval produces local feedback message", async () => {
    setupMockRuntime({
      respondApproval: vi.fn().mockResolvedValue(false), // Expired
    });

    const { result } = renderHook(() => useApprovalController());

    act(() => {
      result.current[1].mergeApprovals([mockApprovalA]);
    });

    await act(async () => {
      await result.current[1].approve(mockApprovalA);
    });

    expect(result.current[0].errorsByApprovalId["app-A"]).toBe("该审批已过期或已被处理。");
  });

  it("Reject responds immediately without requesting feedback", async () => {
    const api = setupMockRuntime();

    const { result } = renderHook(() => useApprovalController());

    act(() => {
      result.current[1].mergeApprovals([mockApprovalA]);
    });

    await act(async () => {
      await result.current[1].reject(mockApprovalA);
    });

    expect(api.respondApproval).toHaveBeenCalledWith("app-A", "reject");
    expect(result.current[0].approvals).toEqual([]);
  });

  it("Completed Run removes related Approvals", async () => {
    setupMockRuntime();
    const { result } = renderHook(() => useApprovalController());

    act(() => {
      result.current[1].mergeApprovals([mockApprovalA, mockApprovalB]);
    });

    expect(result.current[0].approvals.length).toBe(2);

    act(() => {
      result.current[1].clearApprovalsForRun("run-1");
    });

    expect(result.current[0].approvals).toEqual([]);
  });

  it("loadPending manages loading state, deduplicates synchronous calls, preserves cards, handles failure and recovery", async () => {
    let resolveListPending!: (approvals: ApprovalRequest[]) => void;
    let rejectListPending!: (err: Error) => void;

    const listPendingMock = vi.fn().mockImplementation(() => {
      return new Promise<ApprovalRequest[]>((resolve, reject) => {
        resolveListPending = resolve;
        rejectListPending = reject;
      });
    });

    setupMockRuntime({
      listPendingApprovals: listPendingMock,
    });

    const { result } = renderHook(() => useApprovalController());

    expect(result.current[0].loadingPendingApprovals).toBe(false);
    expect(result.current[0].pendingApprovalsLoadError).toBeUndefined();

    act(() => {
      result.current[1].mergeApprovals([mockApprovalA]);
    });

    let p1!: Promise<boolean>;
    let p2!: Promise<boolean>;
    act(() => {
      p1 = result.current[1].loadPending();
      p2 = result.current[1].loadPending();
    });

    expect(listPendingMock).toHaveBeenCalledTimes(1);
    expect(result.current[0].loadingPendingApprovals).toBe(true);
    expect(result.current[0].approvals).toEqual([mockApprovalA]);

    await act(async () => {
      rejectListPending(new Error("IPC list failed"));
      const [res1, res2] = await Promise.all([p1, p2]);
      expect(res1).toBe(false);
      expect(res2).toBe(false);
    });

    expect(result.current[0].loadingPendingApprovals).toBe(false);
    expect(result.current[0].pendingApprovalsLoadError).toBe("审批状态加载失败，当前任务可能仍在等待你的处理。");
    expect(result.current[0].approvals).toEqual([mockApprovalA]);

    let p3!: Promise<boolean>;
    act(() => {
      p3 = result.current[1].loadPending();
    });

    expect(listPendingMock).toHaveBeenCalledTimes(2);
    expect(result.current[0].loadingPendingApprovals).toBe(true);
    expect(result.current[0].approvals).toEqual([mockApprovalA]);

    await act(async () => {
      resolveListPending([mockApprovalA, mockApprovalB]);
      const res3 = await p3;
      expect(res3).toBe(true);
    });

    expect(result.current[0].loadingPendingApprovals).toBe(false);
    expect(result.current[0].pendingApprovalsLoadError).toBeUndefined();
    expect(result.current[0].approvals).toEqual([mockApprovalA, mockApprovalB]);
  });

  it("Approval Controller and UI recover pending approvals without hiding existing cards", async () => {
    let rejectInitial!: (error: Error) => void;
    let resolveRetry!: (approvals: ApprovalRequest[]) => void;
    const listPendingApprovals = vi.fn()
      .mockImplementationOnce(() => new Promise<ApprovalRequest[]>((_resolve, reject) => {
        rejectInitial = reject;
      }))
      .mockImplementationOnce(() => new Promise<ApprovalRequest[]>((resolve) => {
        resolveRetry = resolve;
      }));
    setupMockRuntime({ listPendingApprovals });

    const run: Run = {
      id: "run-1",
      sessionId: "session-1",
      status: "waiting_approval",
      modelId: "deepseek-v4-flash",
      modelStepCount: 1,
      allowedActions: ["approve", "reject"],
      createdAt: 1,
      updatedAt: 2,
    };
    const items: Item[] = [mockApprovalA, mockApprovalB].map((approval, index) => ({
      id: approval.itemId,
      sessionId: approval.sessionId,
      runId: approval.runId,
      ordinal: index,
      modelStepIndex: 0,
      kind: "command_execution",
      status: "in_progress",
      createdAt: index + 1,
      toolCall: {
        id: approval.toolCallId,
        itemId: approval.itemId,
        modelStepIndex: 0,
        batchOrder: index,
        providerCallId: `provider-${index}`,
        toolName: approval.kind === "command_execution" ? "run_shell" : "network_access",
        status: "running",
        startedAt: index + 1,
      },
    }));

    function Harness() {
      const [state, actions] = useApprovalController();
      useEffect(() => {
        actions.mergeApprovals([mockApprovalA]);
        void actions.loadPending();
      }, []);
      return (
        <ExecutionFeed
          items={items}
          runs={[run]}
          approvals={state.approvals}
          respondingApprovalIds={state.respondingApprovalIds}
          respondingKindByApprovalId={state.respondingKindByApprovalId}
          expiredApprovalIds={state.expiredApprovalIds}
          errorsByApprovalId={state.errorsByApprovalId}
          approvalLoadError={state.pendingApprovalsLoadError}
          loadingPendingApprovals={state.loadingPendingApprovals}
          onRetryLoadPending={() => { void actions.loadPending(); }}
          onApprove={(request) => { void actions.approve(request); }}
          onReject={(request) => { void actions.reject(request); }}
        />
      );
    }

    render(<Harness />);
    expect(screen.getByText(mockApprovalA.summary)).toBeInTheDocument();
    expect(listPendingApprovals).toHaveBeenCalledTimes(1);

    await act(async () => {
      rejectInitial(new Error("raw pending failure"));
      await Promise.resolve();
    });
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("审批状态加载失败");
    expect(banner).not.toHaveTextContent("raw pending failure");
    expect(screen.getByText(mockApprovalA.summary)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试加载审批" })).toBeInTheDocument();
    expect(screen.getByText("等待批准")).toBeInTheDocument();

    const retry = screen.getByRole("button", { name: "重试加载审批" });
    fireEvent.click(retry);
    fireEvent.click(retry);
    expect(listPendingApprovals).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "重试加载审批" })).toBeDisabled();
    expect(screen.getByText("加载中…")).toBeInTheDocument();
    expect(screen.getByText(mockApprovalA.summary)).toBeInTheDocument();

    await act(async () => {
      resolveRetry([mockApprovalB]);
      await Promise.resolve();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(mockApprovalA.summary)).not.toBeInTheDocument();
    expect(screen.getByText(mockApprovalB.summary)).toBeInTheDocument();
  });
});

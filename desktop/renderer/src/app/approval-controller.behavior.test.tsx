import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useApprovalController } from "./useApprovalController.js";
import type { ApprovalRequest, EidosRuntimeAPI } from "../contracts.js";
import { MAX_APPROVAL_FEEDBACK_BYTES } from "../../../shared/constants.js";

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

describe("useApprovalController real behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
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

  it("Reject opens feedback dialog and UTF-8 feedback limit handles Chinese and emoji", async () => {
    setupMockRuntime();
    const { result } = renderHook(() => useApprovalController());

    act(() => {
      result.current[1].mergeApprovals([mockApprovalA]);
    });

    act(() => {
      result.current[1].openRejectDialog(mockApprovalA);
    });

    expect(result.current[0].feedbackDialogApproval?.id).toBe("app-A");

    // Over UTF-8 byte limit with Chinese and emoji
    const oversizedFeedback = "测试中文 feedback 🎉 ".repeat(300); // Exceeds 1024 bytes
    expect(new TextEncoder().encode(oversizedFeedback).byteLength).toBeGreaterThan(MAX_APPROVAL_FEEDBACK_BYTES);

    await act(async () => {
      await result.current[1].submitReject(mockApprovalA, oversizedFeedback);
    });

    expect(result.current[0].feedbackDialogError).toContain(`反馈长度超过限制 (${MAX_APPROVAL_FEEDBACK_BYTES} 字节)`);
  });

  it("Reject dialog preserves feedback after failure and closes after success", async () => {
    const api = setupMockRuntime({
      respondApproval: vi.fn().mockRejectedValueOnce(new Error("Reject Failed")).mockResolvedValue(true),
    });

    const { result } = renderHook(() => useApprovalController());

    act(() => {
      result.current[1].mergeApprovals([mockApprovalA]);
      result.current[1].openRejectDialog(mockApprovalA);
    });

    // Reject attempt 1 fails
    await act(async () => {
      await result.current[1].submitReject(mockApprovalA, "Reason text");
    });

    expect(result.current[0].feedbackDialogError).toBe("操作失败，请查看 Runtime 日志。");
    expect(result.current[0].feedbackDialogApproval?.id).toBe("app-A"); // Dialog preserved

    // Reject attempt 2 succeeds
    await act(async () => {
      await result.current[1].submitReject(mockApprovalA, "Reason text");
    });

    expect(api.respondApproval).toHaveBeenCalledWith("app-A", "reject", "Reason text");
    expect(result.current[0].feedbackDialogApproval).toBeNull(); // Dialog closed
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
});

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ProjectGitContext, Session } from "../contracts.js";
import { runtimeBusinessCode, userFacingError } from "../session-state.js";
import { switchDraftGitBranch } from "./git-workspace-client.js";

interface DraftSwitch {
  key: symbol;
  branch: string;
  operationId: string;
  observed: ProjectGitContext;
}

interface WorktreeSelection {
  key: symbol;
  branch?: string | undefined;
  copyHead?: string | undefined;
}

export interface DraftGitView {
  context: ProjectGitContext | undefined;
  error: string | undefined;
  selectedBase: string | undefined;
  canCopy: boolean;
  copyChanges: boolean;
  onSelectBranch(branch: string): void;
  onCopyChanges(enabled: boolean): void;
  onRefresh(): void;
}

type Observation = { key: symbol; context: ProjectGitContext } | { key: symbol; error: string };

export type DraftSwitchResult = { status: "switched"; branch: string } | { status: "rejected"; message: string };

export function useDraftGit(session: Session | undefined, ready: boolean) {
  const primary = session?.roots?.[0];
  const projectId = session?.project?.id;
  const rootId = primary?.id;
  const draftId = session?.id;
  const key = useMemo(() => draftId && projectId && rootId ? Symbol() : undefined,
    [draftId, projectId, rootId, primary?.path]);
  const activeKey = useRef(key);
  const observationRequest = useRef(0);
  const switchInProgress = useRef(false);
  const [observation, setObservation] = useState<Observation>();
  const [selection, setSelection] = useState<WorktreeSelection>();
  const [pendingSwitch, setPendingSwitch] = useState<DraftSwitch>();
  const [switchFailure, setSwitchFailure] = useState<{ key: symbol; message: string }>();
  const [switchBusy, setSwitchBusy] = useState(false);
  const [switchCompleted, setSwitchCompleted] = useState(0);

  useLayoutEffect(() => {
    activeKey.current = key;
    return () => { activeKey.current = undefined; };
  }, [key]);

  const refresh = useCallback(async (): Promise<ProjectGitContext | undefined> => {
    if (!key || !projectId || !rootId || !ready || activeKey.current !== key) return undefined;
    const request = ++observationRequest.current;
    try {
      const context = await window.eidosRuntime.readProjectGitContext({ projectId, rootId });
      if (activeKey.current !== key || observationRequest.current !== request) return undefined;
      setObservation({ key, context });
      return context;
    } catch (cause) {
      if (activeKey.current === key && observationRequest.current === request) {
        setObservation({ key, error: userFacingError(cause) });
      }
      return undefined;
    }
  }, [key, projectId, ready, rootId]);

  useEffect(() => {
    if (!key || !ready) return;
    const update = () => { void refresh(); };
    update();
    window.addEventListener("focus", update);
    return () => {
      observationRequest.current++;
      window.removeEventListener("focus", update);
    };
  }, [key, ready, refresh]);

  const current = ready && observation?.key === key ? observation : undefined;
  const context = current && "context" in current ? current.context : undefined;
  const selected = selection?.key === key ? selection : undefined;
  const requestedSwitch = pendingSwitch?.key === key ? pendingSwitch : undefined;
  const switchError = switchFailure?.key === key ? switchFailure?.message : undefined;
  const isWorktree = session?.executionMode === "worktree";
  const canCopy = Boolean(context?.head && context.dirty && context.switchBlockedReason !== "operation"
    && context.switchBlockedReason !== "busy"
    && (!selected?.branch || selected.branch === context.currentBranch));
  const copyChanges = Boolean(canCopy && context?.head === selected?.copyHead);

  function selectBranch(branch: string): void {
    if (!key || !context) return;
    if (isWorktree) {
      setSelection({ key, branch: branch || undefined });
      return;
    }
    if (!branch || branch === context.currentBranch || context.dirty || context.switchBlockedReason
      || context.occupiedBranches?.includes(branch)) return;
    setSwitchFailure(undefined);
    setPendingSwitch({ key, branch, observed: context, operationId: crypto.randomUUID() });
  }

  /**
   * Switches to the confirmed branch. A failure that closes the confirmation comes back as `rejected`
   * for the caller to report; a retryable failure stays in the still-open confirmation (`switchError`).
   */
  async function confirmSwitch(): Promise<DraftSwitchResult | undefined> {
    if (!requestedSwitch || !draftId || !projectId || !rootId || switchInProgress.current) return undefined;
    switchInProgress.current = true;
    setSwitchBusy(true);
    setSwitchFailure(undefined);
    const { branch, operationId, observed } = requestedSwitch;
    try {
      await switchDraftGitBranch({ rootId, projectId, draftId, branch, operationId,
        expectedBranch: observed.currentBranch, expectedHead: observed.head });
      if (activeKey.current !== requestedSwitch.key) return undefined;
      setPendingSwitch(undefined);
      setSelection(undefined);
      setSwitchCompleted((previous) => previous + 1);
      await refresh();
      return { status: "switched", branch };
    } catch (cause) {
      if (activeKey.current !== requestedSwitch.key) return undefined;
      const message = userFacingError(cause);
      const code = runtimeBusinessCode(cause);
      if (code && code !== "OPERATION_IN_PROGRESS" && code !== "INTERNAL_ERROR") {
        // Reusing the confirmation after a known rejection could use a stale Git observation.
        setPendingSwitch(undefined);
        await refresh();
        return { status: "rejected", message };
      }
      setSwitchFailure({ key: requestedSwitch.key, message });
      await refresh();
      return undefined;
    } finally {
      switchInProgress.current = false;
      setSwitchBusy(false);
    }
  }

  async function prepareSubmission(): Promise<{ baseRef?: string; includeLocalChanges?: boolean }> {
    if (!key) return {};
    if (switchInProgress.current || requestedSwitch) throw new Error("请先完成或取消分支切换。");
    const fresh = await refresh();
    if (activeKey.current !== key) throw new Error("项目目录已变化。请确认后重新发送。");
    if (!fresh) {
      if (!isWorktree && !(context?.gitAvailable ?? session?.project?.gitAvailable)) return {};
      throw new Error("无法确认当前 Git 状态。请刷新后重试。");
    }
    if (!isWorktree) {
      if ((!context && fresh.gitAvailable) || (context && (context.gitAvailable !== fresh.gitAvailable
        || context.currentBranch !== fresh.currentBranch || context.head !== fresh.head))) {
        throw new Error("本地分支或提交已变化。请确认当前分支后重新发送。");
      }
      return {};
    }
    if (!fresh.gitAvailable || !fresh.head) throw new Error("项目需要先有一个 Git 提交，才能新建工作树。");
    if (selected?.branch && !fresh.branches.includes(selected.branch)) {
      throw new Error("所选起点分支已不存在。请选择其他分支后重新发送。");
    }
    if (copyChanges && (selected?.copyHead !== fresh.head || (selected?.branch && selected.branch !== fresh.currentBranch))) {
      setSelection({ key, branch: selected?.branch });
      throw new Error("源目录的提交已变化。请重新确认是否复制未提交改动。");
    }
    if (copyChanges && (fresh.switchBlockedReason === "operation" || fresh.switchBlockedReason === "busy")) {
      throw new Error("源目录正在使用。请等待任务或 Git 操作完成后再复制未提交改动。");
    }
    if (!selected?.branch && (context?.currentBranch !== fresh.currentBranch || context?.head !== fresh.head)) {
      throw new Error("当前分支或提交已变化。请确认工作树起点后重新发送。");
    }
    return {
      ...(selected?.branch ? { baseRef: `refs/heads/${selected.branch}` } : {}),
      ...(copyChanges && fresh.dirty ? { includeLocalChanges: true } : {}),
    };
  }

  const view: DraftGitView | undefined = key ? {
    context,
    error: current && "error" in current ? current.error : undefined,
    selectedBase: selected?.branch,
    canCopy,
    copyChanges,
    onSelectBranch: selectBranch,
    onCopyChanges: (enabled) => {
      if (canCopy) setSelection({ key, branch: selected?.branch, copyHead: enabled ? context?.head ?? undefined : undefined });
    },
    onRefresh: () => { void refresh(); },
  } : undefined;

  return {
    view,
    prepareSubmission,
    requestedSwitch,
    confirmSwitch,
    cancelSwitch: () => { setPendingSwitch(undefined); setSwitchFailure(undefined); },
    switchError,
    switchBusy,
    switchCompleted,
  };
}

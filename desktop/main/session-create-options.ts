import type { EidosRuntimeAPI } from "../shared/ipc-api.js";

export function validateSessionCreateOptions(value: unknown): NonNullable<Parameters<EidosRuntimeAPI["createSession"]>[1]> {
  if (value === undefined) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Session 创建参数无效。");
  }
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some((key) => !["projectId", "workspaceNameHint", "executionMode", "baseRef", "includeLocalChanges"].includes(key))) {
    throw new Error("Session 创建参数无效。");
  }
  if (
    record.executionMode !== undefined
    && record.executionMode !== "local"
    && record.executionMode !== "worktree"
  ) {
    throw new Error("Session 创建模式无效。");
  }
  if (record.baseRef !== undefined && typeof record.baseRef !== "string") {
    throw new Error("Session 起始 Ref 无效。");
  }
  if (record.includeLocalChanges !== undefined && typeof record.includeLocalChanges !== "boolean") {
    throw new Error("Session 本地修改参数无效。");
  }
  if (record.workspaceNameHint !== undefined && (
    typeof record.workspaceNameHint !== "string" || [...record.workspaceNameHint].length > 512
  )) {
    throw new Error("Session 工作目录名称无效。");
  }
  if (record.projectId !== undefined && (typeof record.projectId !== "string" || !record.projectId || record.projectId.length > 256)) {
    throw new Error("Session 项目参数无效。");
  }
  return {
    ...(record.projectId !== undefined ? { projectId: record.projectId } : {}),
    ...(record.workspaceNameHint !== undefined ? { workspaceNameHint: record.workspaceNameHint } : {}),
    ...(record.executionMode !== undefined
      ? { executionMode: record.executionMode }
      : {}),
    ...(record.baseRef !== undefined ? { baseRef: record.baseRef } : {}),
    ...(record.includeLocalChanges !== undefined
      ? { includeLocalChanges: record.includeLocalChanges }
      : {}),
  };
}


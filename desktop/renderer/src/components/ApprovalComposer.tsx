import type { ApprovalRequest, Run } from "../../../shared/domain-contracts.js";
import { Button } from "./Button.js";

export interface ApprovalComposerProps {
  run: Run;
  approval: ApprovalRequest;
  respondingApprovalIds?: ReadonlySet<string> | undefined;
  respondingKindByApprovalId?: Readonly<Record<string, "approve" | "reject">> | undefined;
  expiredApprovalIds?: ReadonlySet<string> | undefined;
  errorsByApprovalId?: Readonly<Record<string, string>> | undefined;
  onApprove: (approval: ApprovalRequest) => void;
  onReject: (approval: ApprovalRequest) => void;
}

export function ApprovalComposer({run, approval, respondingApprovalIds,
  respondingKindByApprovalId, expiredApprovalIds, errorsByApprovalId,
  onApprove, onReject}: ApprovalComposerProps) {
    const isExpired = Boolean(expiredApprovalIds?.has(approval.id));
    const localError = errorsByApprovalId?.[approval.id];
    const isResponding = Boolean(respondingApprovalIds && respondingApprovalIds.has(approval.id));
    const isApproving = isResponding && respondingKindByApprovalId?.[approval.id] === "approve";
    const isRejecting = isResponding && respondingKindByApprovalId?.[approval.id] === "reject";
    const canApprove = !isExpired && run.allowedActions?.includes("approve") && !isResponding;
    const canReject = !isExpired && run.allowedActions?.includes("reject") && !isResponding;
    const isUnsandboxed = approval.kind === "command_execution"
      && approval.executionMode === "unsandboxed";

    return (
      <article
        className={[
          "approval-card approval-composer",
          isExpired ? "approval-card--expired" : "",
          isUnsandboxed ? "approval-card--unsandboxed" : "",
        ].filter(Boolean).join(" ")}
        aria-labelledby={`approval-${approval.id}`}
      >
        <div className="approval-heading">
          <div>
            <p className="feed-label">{isExpired ? "审批已失效" : "需要你的批准"}</p>
            <h3 id={`approval-${approval.id}`}>{approval.summary}</h3>
          </div>
          <span>{isExpired ? "已过期" : approval.kind === "file_change" ? "文件变更" : approval.kind === "external_tool" ? "MCP 工具" : approval.kind === "network_access" ? "网络访问" : approval.kind === "permission_request" ? "权限申请" : "Shell 命令"}</span>
        </div>
        <pre className="diff-view">
          {approval.kind === "file_change"
            ? approval.diff
            : approval.kind === "external_tool"
              ? `${approval.toolName}\n\nPlugin: ${approval.provenance.pluginId ?? "unknown"}\nServer: ${approval.provenance.serverId ?? "unknown"}\nprofile: ${approval.permissionProfile}\ntimeout: ${approval.timeoutSeconds}s\nenv names: ${approval.envNames.join(", ") || "none"}\narguments: ${JSON.stringify(approval.arguments, null, 2)}`
              : approval.kind === "network_access"
                ? `tool: ${approval.toolName}\ntarget: ${approval.target}\napproved hosts: ${approval.hosts.join(", ")}`
                : approval.kind === "permission_request"
                  ? [approval.command, approval.cwd, approval.reason, "授权范围：当前 Run", JSON.stringify(approval.permissions, null, 2)].filter(Boolean).join("\n")
                  : commandApprovalDetails(approval)}
        </pre>
        {localError && <p className="approval-error" role="alert">{localError}</p>}
        <div className="approval-actions">
          <Button
            variant="ghost"
            size="medium"
            disabled={!canReject}
            loading={isRejecting}
            onClick={() => onReject(approval)}
          >
            拒绝
          </Button>
          <Button
            variant="primary"
            size="medium"
            disabled={!canApprove}
            loading={isApproving}
            onClick={() => onApprove(approval)}
          >
            批准
          </Button>
        </div>
      </article>
    );
}

function commandApprovalDetails(
  approval: Extract<ApprovalRequest, { kind: "command_execution" }>,
): string {
  const executionMode = approval.executionMode ?? "default_sandbox";
  const mode = executionMode === "unsandboxed"
    ? "Unsandboxed"
    : executionMode === "expanded_sandbox"
      ? "Expanded sandbox"
      : "Default sandbox";
  const warning = executionMode === "unsandboxed"
    ? "\nWARNING: This command runs with the current macOS user's permissions and may access or modify files outside the workspace, connect to services, and alter host state.\n"
    : "";
  return [
    `Execution mode: ${mode}`,
    warning,
    `$ ${approval.command}`,
    `cwd: ${approval.cwd}`,
    `network: ${approval.networkEnabled ? "enabled" : "disabled"}`,
    `timeout: ${approval.timeoutSeconds}s`,
    `additional read: ${(approval.additionalReadAccess ?? []).join(", ") || "none"}`,
    `additional write: ${(approval.additionalWriteAccess ?? []).join(", ") || "none"}`,
    `additional execute: ${(approval.additionalExecutableAccess ?? []).join(", ") || "none"}`,
    `reason: ${approval.reason || "none"}`,
    ...(approval.escalationReason
      ? [`escalation reason: ${approval.escalationReason}`]
      : []),
  ].filter(Boolean).join("\n");
}

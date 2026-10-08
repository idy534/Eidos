import type { ReactNode } from "react";
import type { ApprovalFileSystemAccess, ApprovalRequest, CommandApprovalRequest, Run } from "../../../shared/domain-contracts.js";
import { Button } from "./Button.js";
import { ToolTextView } from "./ToolTextView.js";

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
    const canApprove = !isExpired && (run.allowedActions?.includes("approve") || Boolean(approval.reviewFallback)) && !isResponding;
    const canReject = !isExpired && (run.allowedActions?.includes("reject") || Boolean(approval.reviewFallback)) && !isResponding;
    const isUnsandboxed = (approval.kind === "command_execution" && approval.executionMode === "unsandboxed")
      || (approval.kind === "file_change" && approval.sandboxPermissions === "require_escalated");

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
            <h3 id={`approval-${approval.id}`}>{approvalTitle(approval)}</h3>
          </div>
          <span>{isExpired ? "已过期" : approval.kind === "file_change" ? "文件变更" : approval.kind === "external_tool" ? "MCP 工具" : approval.kind === "network_access" ? "网络访问" : approval.kind === "permission_request" ? "权限申请" : "Shell 命令"}</span>
        </div>
        <div className="approval-body"><ApprovalContent approval={approval} /></div>
        {localError && <p className="approval-error" role="alert">{localError}</p>}
        {approval.reviewFallback && <p role="status">{approval.reviewFallback} 可在此手动决定。</p>}
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

function approvalTitle(approval: ApprovalRequest): string {
  const titles: Record<string, string> = {
    "Run shell command": "批准命令执行",
    "Run shell command with expanded sandbox permissions": "批准命令所需权限",
    "Run shell command without the macOS sandbox": "批准命令在沙盒外执行",
    "Write files without the sandbox": "批准文件在沙盒外写入",
    "Write files in the requested permission scope": "批准文件写入",
    "Allow additional permissions for this run": "批准当前任务所需权限",
    "Call an external MCP tool": "批准外部工具调用",
    "Download a public GitHub skill": "批准下载 GitHub Skill",
  };
  return titles[approval.summary] ?? approval.summary;
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return <div className="approval-detail-row"><dt>{label}</dt><dd>{children}</dd></div>;
}

function Directory({ cwd }: { cwd: string }) {
  return <DetailRow label="目录">{cwd === "." ? "当前工作区" : cwd}</DetailRow>;
}

const accessLabels = { read: "读取", write: "写入", execute: "执行", deny: "禁止访问" } as const;

function FilePermissions({ entries }: {
  entries: (Omit<ApprovalFileSystemAccess, "recursive"> & { recursive?: boolean })[];
}) {
  if (!entries.length) return null;
  return <DetailRow label="文件权限"><ul className="approval-permissions">
    {entries.map((entry, index) => <li key={`${entry.access}:${entry.path}:${index}`}>
      {`${accessLabels[entry.access]} ${entry.path}（${entry.recursive === undefined ? "子目录范围未记录" : entry.recursive ? "包含子目录" : "仅此路径"}）`}
    </li>)}
  </ul></DetailRow>;
}

function commandFilePermissions(approval: CommandApprovalRequest) {
  const entries: (Omit<ApprovalFileSystemAccess, "recursive"> & { recursive?: boolean })[] = [...(approval.additionalFileSystemAccess ?? [])];
  for (const [access, paths] of [
    ["read", approval.additionalReadAccess], ["write", approval.additionalWriteAccess],
    ["execute", approval.additionalExecutableAccess],
  ] as const) {
    for (const path of paths ?? []) {
      if (!entries.some((entry) => entry.path === path && entry.access === access)) entries.push({ path, access });
    }
  }
  return entries;
}

function ApprovalContent({ approval }: { approval: ApprovalRequest }) {
  if (approval.kind === "file_change") return <>
    {approval.sandboxPermissions === "require_escalated" && <p className="approval-warning" role="note">文件操作将在沙盒外执行。系统仍会核验目标文件的权限和版本。</p>}
    <dl className="approval-details">
      <DetailRow label="批准范围">仅本次文件操作</DetailRow>
      <FilePermissions entries={approval.additionalFileSystemAccess ?? []} />
    </dl>
    {approval.diffHash
      ? <ToolTextView key={approval.diffHash} sessionId={approval.sessionId} toolCallId={approval.toolCallId} field="diff" sha256={approval.diffHash} totalBytes={approval.diffBytes!} />
      : <pre className="diff-view">{approval.diff}</pre>}
  </>;

  if (approval.kind === "command_execution") return <>
    {approval.executionMode === "unsandboxed" && <p className="approval-warning" role="note">命令将使用当前 macOS 用户的权限，可能访问或修改工作区外的文件和网络。</p>}
    <pre className="approval-command" aria-label="完整命令">{approval.command}</pre>
    <dl className="approval-details">
      <Directory cwd={approval.cwd} />
      <DetailRow label="执行方式">{approval.executionMode === "unsandboxed" ? "沙盒外执行" : approval.executionMode === "expanded_sandbox" ? "沙盒内执行（已扩展权限）" : "默认沙盒"}</DetailRow>
      <DetailRow label="批准范围">仅本次命令</DetailRow>
      {approval.networkEnabled && approval.executionMode !== "unsandboxed" && <DetailRow label="网络权限">允许访问网络，不限域名</DetailRow>}
      <FilePermissions entries={commandFilePermissions(approval)} />
      {approval.reason && <DetailRow label="申请原因">{approval.reason}</DetailRow>}
      {approval.escalationReason && <DetailRow label="升级原因">{approval.escalationReason}</DetailRow>}
    </dl>
    <details className="approval-execution-details"><summary>执行详情</summary>
      <p>单次工具调用观察上限：{approval.timeoutSeconds} 秒。命令没有默认总运行期限。</p>
      {!approval.networkEnabled && approval.executionMode !== "unsandboxed" && <p>网络：不允许访问网络。</p>}
    </details>
  </>;

  if (approval.kind === "permission_request") return <>
    {approval.command && <pre className="approval-command" aria-label="相关命令">{approval.command}</pre>}
    <dl className="approval-details">
      <DetailRow label="授权范围">当前任务及其后续操作，任务结束后失效</DetailRow>
      {approval.cwd && <Directory cwd={approval.cwd} />}
      {approval.permissions.network && <DetailRow label="网络权限">{approval.permissions.network.enabled ? "允许访问网络，不限域名" : "不允许访问网络"}</DetailRow>}
      <FilePermissions entries={approval.permissions.fileSystem ?? []} />
      {approval.reason && <DetailRow label="申请原因">{approval.reason}</DetailRow>}
    </dl>
  </>;

  if (approval.kind === "network_access") return <dl className="approval-details">
    <DetailRow label="目标">{approval.target}</DetailRow>
    <DetailRow label="允许访问的主机">{approval.hosts.join("、")}</DetailRow>
    <DetailRow label="工具">{approval.toolName}</DetailRow>
    <DetailRow label="批准范围">仅本次网络操作</DetailRow>
  </dl>;

  return <>
    <dl className="approval-details">
      <DetailRow label="工具">{approval.toolName}</DetailRow>
      <DetailRow label="来源">{approval.provenance.serverId ?? approval.provenance.sourceId}</DetailRow>
      {approval.provenance.pluginId && <DetailRow label="插件">{approval.provenance.pluginId}</DetailRow>}
      <DetailRow label="权限配置">{approval.permissionProfile === "workspace_read" ? "工作区只读" : "连接器"}</DetailRow>
      <DetailRow label="批准范围">仅本次工具调用</DetailRow>
    </dl>
    <pre className="approval-command" aria-label="工具参数">{JSON.stringify(approval.arguments, null, 2)}</pre>
    <details className="approval-execution-details"><summary>执行详情</summary>
      <p>工具超时：{approval.timeoutSeconds} 秒。</p>
      {approval.envNames.length > 0 && <p>环境变量名称：{approval.envNames.join("、")}</p>}
    </details>
  </>;
}

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ApprovalRequest, CommandApprovalRequest, Run } from "../../../shared/domain-contracts.js";
import { ApprovalComposer } from "./ApprovalComposer.js";

const run = { id: "r", status: "waiting_approval", allowedActions: ["approve", "reject"] } as Run;
const command: CommandApprovalRequest = {
  id: "a", sessionId: "s", runId: "r", itemId: "i", toolCallId: "t",
  kind: "command_execution", summary: "Run shell command with expanded sandbox permissions",
  command: "curl -sS -I --max-time 10 https://example.com", cwd: ".",
  executionMode: "expanded_sandbox", networkEnabled: true, timeoutSeconds: 600,
  reason: "检查网络是否可用", additionalReadAccess: [], additionalWriteAccess: [],
};

function show(approval: ApprovalRequest) {
  return render(<ApprovalComposer run={run} approval={approval} onApprove={vi.fn()} onReject={vi.fn()} />);
}

describe("Approval decision details", () => {
  it("keeps the complete command and actual network scope, and folds observation settings", () => {
    show(command);
    expect(screen.getByText(command.command)).toBeVisible();
    expect(screen.getByText("当前工作区")).toBeVisible();
    expect(screen.getByText("允许访问网络，不限域名")).toBeVisible();
    expect(screen.getByText("仅本次命令")).toBeVisible();
    expect(screen.getByText(command.reason!)).toBeVisible();
    expect(screen.queryByText(/additional|none|Execution mode|watchdog/)).toBeNull();
    const details = screen.getByText("执行详情").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("单次工具调用观察上限：600 秒");
    expect(details).toHaveTextContent("命令没有默认总运行期限");
  });

  it("shows access modes and recursive scopes without empty permission rows", () => {
    show({ ...command, cwd: "packages/app", networkEnabled: false,
      additionalFileSystemAccess: [
        { path: "/output", access: "write", recursive: true },
        { path: "/sdk/config", access: "read", recursive: false },
      ],
    });
    expect(screen.getByText("packages/app")).toBeVisible();
    expect(screen.getByText("写入 /output（包含子目录）")).toBeVisible();
    expect(screen.getByText("读取 /sdk/config（仅此路径）")).toBeVisible();
    expect(screen.queryByText("执行权限")).toBeNull();
  });

  it("keeps legacy path permissions and identifies missing recursive information", () => {
    show({ ...command, additionalWriteAccess: ["/legacy-output"] });
    expect(screen.getByText("写入 /legacy-output（子目录范围未记录）")).toBeVisible();
  });

  it("keeps unsandboxed warnings and escalation reasons outside collapsed details", () => {
    show({ ...command, executionMode: "unsandboxed", escalationReason: "系统沙盒无法启动" });
    const warning = screen.getByRole("note");
    expect(warning).toHaveTextContent("命令将使用当前 macOS 用户的权限");
    expect(warning).toHaveTextContent("工作区外的文件和网络");
    expect(warning.closest("details")).toBeNull();
    expect(screen.getByText("系统沙盒无法启动")).toBeVisible();
  });

  it("distinguishes run grants and keeps explicit network restrictions", () => {
    show({ ...command, kind: "permission_request", grantScope: "run",
      permissions: { network: { enabled: false }, fileSystem: [{ path: "/output", access: "write", recursive: true }] },
    });
    expect(screen.getByText("当前任务及其后续操作，任务结束后失效")).toBeVisible();
    expect(screen.getByText("不允许访问网络")).toBeVisible();
    expect(screen.getByText("写入 /output（包含子目录）")).toBeVisible();
    expect(screen.queryByText(/fileSystem|recursive/)).toBeNull();
  });

  it("preserves the complete file diff and file sandbox warning", () => {
    const diff = "--- /outside/a.txt\n+++ /outside/a.txt\n-old\n+new";
    show({ ...command, kind: "file_change", diff, sandboxPermissions: "require_escalated",
      additionalFileSystemAccess: [{ path: "/outside/a.txt", access: "write", recursive: false }],
    });
    expect(screen.getByText((_, element) => element?.tagName === "PRE" && element.textContent === diff)).toBeVisible();
    expect(screen.getByRole("note")).toHaveTextContent("文件操作将在沙盒外执行");
    expect(screen.getByText("写入 /outside/a.txt（仅此路径）")).toBeVisible();
  });

  it("retains all unknown MCP argument values while folding runtime settings", () => {
    show({ ...command, kind: "external_tool", toolName: "delete_records",
      arguments: { recursive: false, target: "", filter: null, limit: 0 },
      provenance: { kind: "mcp", sourceId: "server-1", sourceVersion: "1", contentHash: "hash" },
      permissionProfile: "connector", timeoutSeconds: 30, envNames: [],
    });
    expect(screen.getByText("server-1")).toBeVisible();
    const argumentsView = screen.getByLabelText("工具参数");
    expect(JSON.parse(argumentsView.textContent!)).toEqual({ recursive: false, target: "", filter: null, limit: 0 });
    expect(argumentsView.closest("details")).toBeNull();
    expect(screen.queryByText("环境变量名称")).toBeNull();
    expect(screen.getByText("执行详情").closest("details")).not.toHaveAttribute("open");
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ComposerSlot } from "./ComposerSlot.js";
import type { ApprovalRequest, Run } from "../../../shared/domain-contracts.js";

const approval: ApprovalRequest = {
  id: "a", sessionId: "s", runId: "r", itemId: "i", toolCallId: "t",
  kind: "file_change", summary: "Write document", diff: "+ document",
};
const run = { id: "r", status: "waiting_approval", allowedActions: ["approve", "reject"] } as Run;

describe("Approval composer slot", () => {
  it("replaces the input and exposes only two decisions", () => {
    render(<ComposerSlot run={run} approval={approval} onApprove={vi.fn()} onReject={vi.fn()}>
      <textarea aria-label="message" />
    </ComposerSlot>);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "批准" })).toBeEnabled();
  });
  it("restores input after the run resumes", () => {
    render(<ComposerSlot run={{ ...run, status: "running" }} approval={undefined} onApprove={vi.fn()} onReject={vi.fn()}>
      <textarea aria-label="message" />
    </ComposerSlot>);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("lets the user decide after automatic review fails", () => {
    render(<ComposerSlot run={{...run, approvalMode: 'auto_review', allowedActions: ['cancel']}}
      approval={{...approval, reviewFallback: '自动审批超时'}} onApprove={vi.fn()} onReject={vi.fn()}>
      <textarea aria-label="message" />
    </ComposerSlot>);
    expect(screen.getByRole('status')).toHaveTextContent('自动审批超时');
    expect(screen.getByRole('button', {name: '批准'})).toBeEnabled();
    expect(screen.getByRole('button', {name: '拒绝'})).toBeEnabled();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it.each([
    ["auto_review", "模型正在审查操作…"],
  ] as const)("does not show manual buttons while %s is resolving", (approvalMode, message) => {
    render(
      <ComposerSlot
        run={{ ...run, approvalMode }}
        approval={approval}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      >
        <textarea aria-label="message" />
      </ComposerSlot>,
    );
    expect(screen.getByRole("status")).toHaveTextContent(message);
    expect(screen.queryByRole("button", { name: "批准" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝" })).toBeNull();
  });

  it("renders the redesigned status banner with spinner, title, hint, and badge", () => {
    render(
      <ComposerSlot
        run={{ ...run, approvalMode: "auto_review" }}
        approval={approval}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      >
        <textarea aria-label="message" />
      </ComposerSlot>,
    );
    const statusBanner = screen.getByRole("status");
    expect(statusBanner).toHaveClass("approval-status-banner");
    expect(statusBanner).toHaveTextContent("模型正在审查操作…");
    expect(statusBanner).toHaveTextContent("正在评估工具调用的安全性与潜在风险");
    expect(statusBanner).toHaveTextContent("替我审批");
    expect(statusBanner.querySelector(".approval-status-banner__spinner")).toBeInTheDocument();
  });

  it("presents dangerous requests to the user in auto_review mode", () => {
    const onApprove = vi.fn();
    render(<ComposerSlot run={{ ...run, approvalMode: "auto_review", allowedActions: ["cancel"] }}
      approval={{ ...approval, requiresHumanConfirmation: true, riskWarning: "此操作会修改系统 Skill。" }}
      onApprove={onApprove} onReject={vi.fn()}><textarea aria-label="message" /></ComposerSlot>);
    expect(screen.getByRole("heading", { name: "高危操作，需要你的确认" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("此操作将修改受保护文件，请确认变更范围。");
    expect(screen.getByRole("button", { name: "允许并执行" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeEnabled();
    expect(screen.queryByText("模型正在审查操作…")).toBeNull();
  });

  it("does not display approval controls or mode authorization in full_access", () => {
    render(<ComposerSlot run={{ ...run, approvalMode: "full_access" }} approval={approval}
      onApprove={vi.fn()} onReject={vi.fn()}><textarea aria-label="message" /></ComposerSlot>);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准" })).toBeNull();
    expect(screen.queryByText(/完全访问授权/)).toBeNull();
  });

  it("shows one short warning for a dangerous unsandboxed command", () => {
    const view = render(<ComposerSlot run={run} approval={{
      ...approval, kind: "command_execution", command: "touch example.txt", cwd: ".",
      networkEnabled: true, timeoutSeconds: 600, executionMode: "unsandboxed",
      requiresHumanConfirmation: true, riskWarning: "旧版的完整高危警告，包含多句风险说明。",
    }} onApprove={vi.fn()} onReject={vi.fn()}><textarea /></ComposerSlot>);
    expect(view.container.querySelectorAll(".approval-warning")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("命令将在沙盒外执行，可访问外部文件和网络。");
    expect(screen.queryByText(/旧版的完整高危警告/)).toBeNull();
    expect(screen.getByRole("button", { name: "允许并执行" })).toBeEnabled();
  });
});

it.each(["approve", "reject"] as const)("disables both buttons while responding %s", (kind) => {
  render(<ComposerSlot run={run} approval={approval} onApprove={vi.fn()} onReject={vi.fn()}
    respondingApprovalIds={new Set(["a"])} respondingKindByApprovalId={{ a: kind }}>
    <textarea />
  </ComposerSlot>);
  expect(screen.getByRole("button", { name: "批准" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
});

it("keeps an expired request disabled and an RPC error retryable", () => {
  const props = { run, approval, onApprove: vi.fn(), onReject: vi.fn(), children: <textarea /> };
  const view = render(<ComposerSlot {...props} errorsByApprovalId={{ a: "Connection failed" }} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Connection failed");
  expect(screen.getByRole("button", { name: "批准" })).toBeEnabled();
  view.rerender(<ComposerSlot {...props} expiredApprovalIds={new Set(["a"])} />);
  expect(screen.getByText("审批已失效")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准" })).toBeDisabled();
});

it.each([
  { ...approval, kind: "command_execution", command: "echo test", cwd: ".", networkEnabled: false, timeoutSeconds: 30 },
  { ...approval, kind: "network_access", toolName: "fetch", hosts: ["example.com"], target: "example.com" },
  { ...approval, kind: "external_tool", toolName: "mcp", arguments: {}, provenance: { kind: "mcp", sourceId: "x", sourceVersion: "1", contentHash: "h" }, permissionProfile: "connector", timeoutSeconds: 30, envNames: [] },
  { ...approval, kind: "permission_request", grantScope: "run", permissions: { network: { enabled: true } } },
] as ApprovalRequest[])("renders $kind in the same composer", (request) => {
  render(<ComposerSlot run={run} approval={request} onApprove={vi.fn()} onReject={vi.fn()}><textarea /></ComposerSlot>);
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.getAllByRole("button")).toHaveLength(2);
});

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

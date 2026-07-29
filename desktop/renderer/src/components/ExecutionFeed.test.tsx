import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import type { Item, Run } from "../contracts.js";
import { ExecutionFeed, isFeedAtBottom } from "./ExecutionFeed.js";


const run: Run = {
  id: "run-1",
  sessionId: "session-1",
  status: "succeeded",
  modelId: "deepseek-v4-flash",
  modelStepCount: 2,
  createdAt: 1_000,
  startedAt: 1_000,
  updatedAt: 66_000,
  completedAt: 66_000,
};

function item(overrides: Partial<Item> & Pick<Item, "id" | "ordinal" | "kind">): Item {
  return {
    sessionId: "session-1",
    runId: run.id,
    status: "completed",
    createdAt: 1_000,
    completedAt: 66_000,
    ...overrides,
  };
}

test("keeps auto-scroll only while the feed is at the bottom", () => {
  assert.equal(isFeedAtBottom({ scrollHeight: 1000, scrollTop: 398, clientHeight: 600 }), true);
  assert.equal(isFeedAtBottom({ scrollHeight: 1000, scrollTop: 350, clientHeight: 600 }), false);
});

test("provides an accessible jump to the latest content control", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({ id: "user", ordinal: 1, kind: "user_message", content: "向上浏览" })]}
      runs={[run]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /class="feed-jump-to-bottom"/);
  assert.match(html, /aria-label="滚动到最新内容"/);
  assert.match(html, /hidden=""/);
});

test("folds tool execution before the final answer and omits the success pill", () => {
  const items = [
    item({ id: "user", ordinal: 1, kind: "user_message", content: "检查改动" }),
    item({ id: "progress", ordinal: 2, kind: "assistant_message", content: "我先检查工作区。" }),
    item({
      id: "shell",
      ordinal: 3,
      kind: "command_execution",
      toolCall: {
        id: "tool-1",
        itemId: "shell",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "provider-1",
        toolName: "run_shell",
        status: "completed",
        startedAt: 1_000,
        completedAt: 66_000,
        argumentsJson: JSON.stringify({ command: "git diff --check" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Command completed",
          data: { stdout: "", stderr: "", exitCode: 0 },
        }),
      },
    }),
    item({ id: "final", ordinal: 4, kind: "assistant_message", content: "# 检查完成" }),
  ];

  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={items}
      runs={[run]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /<details class="process-group">/);
  assert.match(html, /已处理 1m 5s/);
  assert.match(html, /已运行 git diff --check/);
  assert.match(html, /无输出/);
  assert.doesNotMatch(html, /已完成/);
  assert.ok(html.indexOf("</details>") < html.indexOf("<h1>检查完成</h1>"));
});

test("streams a tool-free answer without a process group", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[
        item({ id: "user", ordinal: 1, kind: "user_message", content: "直接回答" }),
        item({ id: "final", ordinal: 2, kind: "assistant_message", content: "**答案**" }),
      ]}
      runs={[run]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.doesNotMatch(html, /process-group|已处理|正在处理/);
  assert.match(html, /<strong>答案<\/strong>/);
});

test("shows a thinking indicator before the first model item", () => {
  const { completedAt: _completedAt, ...runWithoutCompletion } = run;
  const activeRun = { ...runWithoutCompletion, status: "running" as const };
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({ id: "user", ordinal: 1, kind: "user_message", content: "开始任务" })]}
      runs={[activeRun]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /class="thinking-indicator"/);
  assert.match(html, /正在思考/);
});

test("shows safe MCP provenance and duration without internal diagnostics", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[
        item({ id: "user", ordinal: 1, kind: "user_message", content: "查询" }),
        item({
          id: "mcp", ordinal: 2, kind: "tool_call",
          toolCall: {
            id: "tool-mcp", itemId: "mcp", modelStepIndex: 1, batchOrder: 0,
            providerCallId: "provider-mcp", toolName: "mcp__fixture__echo",
            status: "completed", argumentsJson: JSON.stringify({ message: "hello" }),
            resultJson: JSON.stringify({ outcome: "success", code: "ok", summary: "MCP tool completed", data: {} }),
            provenance: {
              kind: "mcp", sourceId: "demo:fixture", sourceVersion: "1.0.0",
              contentHash: "a".repeat(64), pluginId: "demo", serverId: "fixture",
            },
            startedAt: 1_000, completedAt: 1_250,
          },
        }),
      ]}
      runs={[run]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /已运行 mcp__fixture__echo/);
  assert.match(html, /Plugin demo · Server fixture · 250ms/);
  assert.doesNotMatch(html, /stderr|internal path|SECRET_VALUE/);
});

test("does not describe a failed file write as edited", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "failed-write", ordinal: 1, kind: "file_change", status: "failed",
        toolCall: {
          id: "tool-write", itemId: "failed-write", modelStepIndex: 1, batchOrder: 0,
          providerCallId: "provider-write", toolName: "write_file",
          status: "failed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ path: "summary.txt" }),
          resultJson: JSON.stringify({
            outcome: "error", code: "TOOL_TIMEOUT", summary: "Tool timed out", data: {},
          }),
        },
      })]}
      runs={[run]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /失败 summary\.txt/);
  assert.doesNotMatch(html, /已编辑 summary\.txt/);
});

test("shows the exact host and target for network approval", () => {
  const { completedAt: _completedAt, ...runWithoutCompletion } = run;
  const waitingRun: Run = {
    ...runWithoutCompletion,
    status: "waiting_approval",
    allowedActions: ["approve", "reject", "cancel"],
  };
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "install", ordinal: 1, kind: "tool_call", status: "in_progress",
        toolCall: {
          id: "tool-install", itemId: "install", modelStepIndex: 1, batchOrder: 0,
          providerCallId: "provider-install", toolName: "skill_install",
          status: "running", startedAt: 1_000,
          argumentsJson: JSON.stringify({ url: "https://github.com/example/skills/tree/main/grilling" }),
        },
      })]}
      runs={[waitingRun]}
      approvals={[{
        id: "approval-network", sessionId: "session-1", runId: run.id,
        itemId: "install", toolCallId: "tool-install", kind: "network_access",
        summary: "Download a public GitHub skill", toolName: "skill_install",
        target: "example/skills@main:grilling", hosts: ["codeload.github.com:443"],
      }]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /网络访问/);
  assert.match(html, /target: example\/skills@main:grilling/);
  assert.match(html, /approved hosts: codeload.github.com:443/);
  assert.match(html, /批准联网/);
});

test("distinguishes expanded and unsandboxed command approvals", () => {
  const { completedAt: _completedAt, ...runWithoutCompletion } = run;
  const waitingRun: Run = {
    ...runWithoutCompletion,
    status: "waiting_approval",
    allowedActions: ["approve", "reject", "cancel"],
  };
  const commandItem = item({
    id: "shell-approval",
    ordinal: 1,
    kind: "command_execution",
    status: "in_progress",
    toolCall: {
      id: "tool-shell",
      itemId: "shell-approval",
      modelStepIndex: 1,
      batchOrder: 0,
      providerCallId: "provider-shell",
      toolName: "run_shell",
      status: "running",
      startedAt: 1_000,
      argumentsJson: JSON.stringify({ command: "make test" }),
    },
  });
  const renderApproval = (
    approval: Parameters<typeof ExecutionFeed>[0]["approvals"][number],
  ) => renderToStaticMarkup(
    <ExecutionFeed
      items={[commandItem]}
      runs={[waitingRun]}
      approvals={[approval]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  const expanded = renderApproval({
    id: "expanded",
    sessionId: "session-1",
    runId: run.id,
    itemId: commandItem.id,
    toolCallId: "tool-shell",
    kind: "command_execution",
    summary: "Run with approved paths",
    command: "make test",
    cwd: "/workspace",
    networkEnabled: true,
    timeoutSeconds: 120,
    executionMode: "expanded_sandbox",
    sandboxPermissions: "with_additional_permissions",
    additionalReadAccess: ["/sdk"],
    additionalWriteAccess: ["/output"],
    additionalExecutableAccess: ["/toolchain"],
    reason: "Build the project",
  });
  const unsandboxed = renderApproval({
    id: "unsandboxed",
    sessionId: "session-1",
    runId: run.id,
    itemId: commandItem.id,
    toolCallId: "tool-shell",
    kind: "command_execution",
    summary: "Run outside Seatbelt",
    command: "make test",
    cwd: "/workspace",
    networkEnabled: false,
    timeoutSeconds: 120,
    executionMode: "unsandboxed",
    sandboxPermissions: "require_escalated",
    escalationReason: "Seatbelt denied executable mapping",
    attemptOrdinal: 1,
  });

  assert.match(expanded, /Execution mode: Expanded sandbox/);
  assert.match(expanded, /additional read: \/sdk/);
  assert.match(expanded, /additional write: \/output/);
  assert.match(expanded, /additional execute: \/toolchain/);
  assert.match(expanded, /network: enabled/);
  assert.match(unsandboxed, /approval-card--unsandboxed/);
  assert.match(unsandboxed, /Execution mode: Unsandboxed/);
  assert.match(unsandboxed, /WARNING: This command runs with the current macOS user/);
  assert.match(unsandboxed, /escalation reason: Seatbelt denied executable mapping/);
});

test("renders minimalist SVG icons for file operations, skills, and shell calls", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[
        item({
          id: "tool-1", ordinal: 1, kind: "tool_call",
          toolCall: {
            id: "tc-1", itemId: "tool-1", modelStepIndex: 1, batchOrder: 0,
            providerCallId: "p-1", toolName: "list_files", status: "completed",
            startedAt: 1000, completedAt: 1100, argumentsJson: "{}", resultJson: "{}",
          },
        }),
        item({
          id: "tool-2", ordinal: 2, kind: "tool_call",
          toolCall: {
            id: "tc-2", itemId: "tool-2", modelStepIndex: 1, batchOrder: 1,
            providerCallId: "p-2", toolName: "skill_read", status: "completed",
            startedAt: 1100, completedAt: 1200, argumentsJson: "{}", resultJson: "{}",
          },
        }),
      ]}
      runs={[run]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /<span class="tool-icon" aria-hidden="true"><svg/);
  assert.match(html, /已列出文件/);
  assert.match(html, /已运行 skill_read/);
});

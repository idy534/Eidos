import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import type { Item, Run } from "../contracts.js";
import {
  ExecutionFeed,
  formatItemTime,
  isFeedAtBottom,
  shellOutputSegments,
} from "./ExecutionFeed.js";


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

test("does not render request snapshot tail in execution feed", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({ id: "user", ordinal: 1, kind: "user_message", content: "检查规则" })]}
      runs={[run]}
      stepResolutions={[{
        id: `step_${"4".repeat(64)}`,
        stepId: "step-1",
        runId: run.id,
        stepOrdinal: 1,
        snapshotHash: "4".repeat(64),
        requestHash: "5".repeat(64),
        ruleSnapshotId: `rule_${"6".repeat(64)}`,
        ruleSnapshotHash: "6".repeat(64),
        rules: [{
          absolutePath: "/workspace/EIDOS.md",
          relativePath: "EIDOS.md",
          filename: "EIDOS.md",
          contentHash: "7".repeat(64),
          byteCount: 12,
          includedByteCount: 12,
          directoryLevel: 0,
          selectionReason: "eidos_native",
          truncated: false,
        }],
        shadowed: [{
          absolutePath: "/workspace/AGENTS.md",
          relativePath: "AGENTS.md",
          filename: "AGENTS.md",
          directoryLevel: 0,
          reason: "higher_precedence_candidate_selected",
        }],
        warnings: [],
      }]}
      approvals={[]}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.doesNotMatch(html, /请求快照/);
  assert.doesNotMatch(html, /Snapshot ID/);
  assert.doesNotMatch(html, /Request hash/);
});

test("groups multi-stage commentary with tools and reserves response actions for the final answer", () => {
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
    item({ id: "progress-2", ordinal: 4, kind: "assistant_message", content: "工作区已经确认，接下来检查配置。" }),
    item({
      id: "search",
      ordinal: 5,
      kind: "tool_call",
      toolCall: {
        id: "tool-2",
        itemId: "search",
        modelStepIndex: 2,
        batchOrder: 0,
        providerCallId: "provider-2",
        toolName: "search_text",
        status: "completed",
        startedAt: 1_000,
        completedAt: 66_000,
        argumentsJson: JSON.stringify({ query: "config" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Search completed",
          data: { matches: [] },
        }),
      },
    }),
    item({ id: "final", ordinal: 6, kind: "assistant_message", content: "# 检查完成" }),
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
  assert.match(html, /我先检查工作区/);
  assert.match(html, /工作区已经确认，接下来检查配置/);
  assert.match(html, /无输出/);
  assert.doesNotMatch(html, /已完成/);
  assert.ok(html.indexOf("</details>") < html.indexOf("<h1>检查完成</h1>"));
  assert.equal((html.match(/aria-label="点赞"/g) ?? []).length, 1);
  assert.equal((html.match(/aria-label="差评"/g) ?? []).length, 1);
  assert.equal((html.match(/title="本次回复模型：/g) ?? []).length, 1);
});

test("formatItemTime formats timestamp to Month日 Hour:Minute", () => {
  // Test July 20, 2024 at 13:46:00 UTC
  // Construct date locally to verify formatItemTime match
  const testDate = new Date(2024, 6, 20, 13, 46);
  assert.equal(formatItemTime(testDate.getTime()), "7月20日 13:46");
  assert.equal(formatItemTime(undefined), "");
});

test("streams a tool-free answer without a process group", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[
        item({ id: "user", ordinal: 1, kind: "user_message", content: "直接回答", createdAt: 1721454360000 }),
        item({ id: "final", ordinal: 2, kind: "assistant_message", content: "**答案**", createdAt: 1721454360000 }),
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
  assert.match(html, /feed-item-copy-btn/);
  assert.match(html, /title="复制内容"/);
  assert.match(html, /class="user-message-bubble"/);
  assert.match(html, /class="feed-item-timestamp"/);
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

test("shows the applied diff for an automatic workspace file change", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "automatic-write", ordinal: 1, kind: "file_change",
        toolCall: {
          id: "tool-write", itemId: "automatic-write", modelStepIndex: 1, batchOrder: 0,
          providerCallId: "provider-write", toolName: "write_file",
          status: "completed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ path: "summary.txt" }),
          changeDiff: "--- a/summary.txt\n+++ b/summary.txt\n@@ -1 +1 @@\n-old\n+new\n",
          resultJson: JSON.stringify({
            outcome: "success", code: "ok", summary: "File change committed", data: {},
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

  assert.match(html, /已应用的变更/);
  assert.match(html, /\+\+\+ b\/summary\.txt/);
  assert.doesNotMatch(html, /批准并写入/);
});

test("shows canonical shell failure code, summary, and stderr", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "failed-shell", ordinal: 1, kind: "command_execution", status: "failed",
        toolCall: {
          id: "tool-shell-failure", itemId: "failed-shell", modelStepIndex: 1, batchOrder: 0,
          providerCallId: "provider-shell-failure", toolName: "run_shell",
          status: "failed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ command: "ls -la" }),
          resultJson: JSON.stringify({
            outcome: "error",
            code: "WORKSPACE_INDEX_INCOMPLETE",
            summary: "Workspace index could not be completed before shell launch.",
            data: { exitCode: null, stdout: "", stderr: "index deadline exceeded" },
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

  assert.match(html, /失败 · WORKSPACE_INDEX_INCOMPLETE/);
  assert.match(html, /Workspace index could not be completed before shell launch\./);
  assert.match(html, /index deadline exceeded/);
});

test("distinguishes a reconciliation gate from an executed shell failure", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "gated-shell", ordinal: 1, kind: "command_execution", status: "failed",
        toolCall: {
          id: "tool-gated-shell", itemId: "gated-shell", modelStepIndex: 1, batchOrder: 0,
          providerCallId: "provider-gated-shell", toolName: "run_shell",
          status: "failed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ command: "python3 build.py" }),
          resultJson: JSON.stringify({
            outcome: "error",
            code: "TOOL_RECONCILIATION_REQUIRED",
            summary: "A previous side effect must be reconciled",
            data: {},
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

  assert.match(html, /未执行，等待只读核验/);
  assert.doesNotMatch(html, /失败 · TOOL_RECONCILIATION_REQUIRED/);
  assert.match(html, /A previous side effect must be reconciled/);
});

test("shows shell termination and bounded-output diagnostics", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "truncated-shell", ordinal: 1, kind: "command_execution",
        toolCall: {
          id: "tool-truncated-shell", itemId: "truncated-shell", modelStepIndex: 1,
          batchOrder: 0, providerCallId: "provider-truncated-shell", toolName: "run_shell",
          status: "completed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ command: "python3 build.py" }),
          resultJson: JSON.stringify({
            outcome: "error", code: "nonzero_exit", summary: "Command failed",
            data: {
              exitCode: 2, stdout: "partial", stderr: "failed",
              termination: "exit", truncated: true, truncationReason: "output_limit",
            },
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

  assert.match(html, /退出码 · 2/);
  assert.match(html, /结束方式 · exit/);
  assert.match(html, /输出已截断 · output_limit/);
});

test("uses the accumulated stream content once and preserves its stdout/stderr order", () => {
  const streamed = "stdout 1\nstderr 1\nstdout 2\n";
  const segments = shellOutputSegments(
    streamed,
    "stdout 1\nstdout 2\n",
    "stderr 1\n",
  );
  assert.deepEqual(segments, [{ source: "stream", content: streamed }]);

  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "streamed-shell", ordinal: 1, kind: "command_execution", content: streamed,
        toolCall: {
          id: "tool-streamed-shell", itemId: "streamed-shell", modelStepIndex: 1,
          batchOrder: 0, providerCallId: "provider-streamed-shell", toolName: "run_shell",
          status: "completed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ command: "pnpm test:fast" }),
          resultJson: JSON.stringify({
            outcome: "success", code: "ok", summary: "Command completed",
            data: {
              stdout: "stdout 1\nstdout 2\n", stderr: "stderr 1\n", exitCode: 0,
              attemptCount: 1, sandboxed: true, escalated: false,
            },
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

  assert.equal((html.match(/stdout 1/g) ?? []).length, 1);
  assert.equal((html.match(/stderr 1/g) ?? []).length, 1);
  assert.ok(html.indexOf("stdout 1") < html.indexOf("stderr 1"));
  assert.ok(html.indexOf("stderr 1") < html.indexOf("stdout 2"));
  assert.match(html, /执行次数 · 1/);
  assert.match(html, /沙箱 · 是/);
  assert.match(html, /扩权 · 否/);
});

test("shows the recorded facts for an escalated retry", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "retried-shell", ordinal: 1, kind: "command_execution", content: "retry output\n",
        toolCall: {
          id: "tool-retried-shell", itemId: "retried-shell", modelStepIndex: 1,
          batchOrder: 0, providerCallId: "provider-retried-shell", toolName: "run_shell",
          status: "completed", startedAt: 1_000, completedAt: 3_000,
          argumentsJson: JSON.stringify({ command: "pnpm test:fast" }),
          resultJson: JSON.stringify({
            outcome: "success", code: "ok", summary: "Command completed after retry",
            data: {
              stdout: "retry output\n", stderr: "", exitCode: 0,
              attemptCount: 2, sandboxed: false, escalated: true,
            },
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

  assert.match(html, /执行次数 · 2/);
  assert.match(html, /沙箱 · 否/);
  assert.match(html, /扩权 · 是/);
  assert.match(html, /✓ 成功/);
});

test("falls back to legacy final streams only when accumulated content is absent", () => {
  const stdout = "legacy stdout\n";
  const stderr = "legacy stderr\n";
  assert.deepEqual(shellOutputSegments(undefined, stdout, stderr), [
    { source: "stdout", content: stdout },
    { source: "stderr", content: stderr },
  ]);
  assert.deepEqual(shellOutputSegments("", stdout, stderr), [
    { source: "stdout", content: stdout },
    { source: "stderr", content: stderr },
  ]);

  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "legacy-shell", ordinal: 1, kind: "command_execution", content: "",
        toolCall: {
          id: "tool-legacy-shell", itemId: "legacy-shell", modelStepIndex: 1,
          batchOrder: 0, providerCallId: "provider-legacy-shell", toolName: "run_shell",
          status: "completed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ command: "legacy" }),
          resultJson: JSON.stringify({
            outcome: "success", code: "ok", summary: "Command completed",
            data: { stdout, stderr, exitCode: 0 },
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
  assert.match(html, /legacy stdout/);
  assert.match(html, /legacy stderr/);
  assert.equal((html.match(/legacy stdout/g) ?? []).length, 1);
  assert.equal((html.match(/legacy stderr/g) ?? []).length, 1);
});

test("strips ANSI and OSC hyperlink controls from the accumulated output", () => {
  const accumulated = "\u001b[31" + "mred\u001b[0m \u001b]8;;https://example.com\u0007link\u001b]8;;\u0007";
  assert.deepEqual(shellOutputSegments(accumulated, "", ""), [
    { source: "stream", content: "red link" },
  ]);
  assert.deepEqual(shellOutputSegments(undefined, "\u001b[32mlegacy\u001b[0m", ""), [
    { source: "stdout", content: "legacy" },
  ]);
});

test("distinguishes in-progress no output from completed no output", () => {
  const { completedAt: _completedAt, ...runWithoutCompletion } = run;
  const activeRun = { ...runWithoutCompletion, status: "running" as const };
  const activeHtml = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "empty-active-shell", ordinal: 1, kind: "command_execution", status: "in_progress",
        toolCall: {
          id: "tool-empty-active-shell", itemId: "empty-active-shell", modelStepIndex: 1,
          batchOrder: 0, providerCallId: "provider-empty-active-shell", toolName: "run_shell",
          status: "running", startedAt: 1_000,
          argumentsJson: JSON.stringify({ command: "slow-command" }),
        },
      })]}
      runs={[activeRun]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );
  assert.match(activeHtml, /尚未输出/);
  assert.doesNotMatch(activeHtml, /无输出/);
  assert.match(activeHtml, /shell-status shell-status--neutral/);
  assert.doesNotMatch(activeHtml, /shell-status shell-status--error/);

  const completedHtml = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "empty-completed-shell", ordinal: 1, kind: "command_execution",
        toolCall: {
          id: "tool-empty-completed-shell", itemId: "empty-completed-shell", modelStepIndex: 1,
          batchOrder: 0, providerCallId: "provider-empty-completed-shell", toolName: "run_shell",
          status: "completed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ command: "true" }),
          resultJson: JSON.stringify({
            outcome: "success", code: "ok", summary: "Command completed",
            data: { stdout: "", stderr: "", exitCode: 0 },
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
  assert.match(completedHtml, /无输出/);
  assert.doesNotMatch(completedHtml, /尚未输出/);
});

test("does not show success for an error or a result that still needs reconciliation", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[
        item({
          id: "error-shell", ordinal: 1, kind: "command_execution",
          toolCall: {
            id: "tool-error-shell", itemId: "error-shell", modelStepIndex: 1,
            batchOrder: 0, providerCallId: "provider-error-shell", toolName: "run_shell",
            status: "completed", startedAt: 1_000, completedAt: 2_000,
            argumentsJson: JSON.stringify({ command: "error-but-zero" }),
            resultJson: JSON.stringify({
              outcome: "error", code: "sandbox_denied", summary: "Command failed",
              data: { stdout: "", stderr: "denied", exitCode: 0 },
            }),
          },
        }),
        item({
          id: "unconfirmed-shell", ordinal: 2, kind: "command_execution",
          toolCall: {
            id: "tool-unconfirmed-shell", itemId: "unconfirmed-shell", modelStepIndex: 1,
            batchOrder: 1, providerCallId: "provider-unconfirmed-shell", toolName: "run_shell",
            status: "completed", startedAt: 2_000, completedAt: 3_000,
            argumentsJson: JSON.stringify({ command: "unknown-result" }),
            resultJson: JSON.stringify({
              outcome: "success", code: "ok", summary: "Command completed",
              reconciliationRequired: true,
              data: { stdout: "", stderr: "", exitCode: 0 },
            }),
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

  assert.doesNotMatch(html, /✓ 成功/);
  assert.match(html, /失败 · sandbox_denied/);
  assert.match(html, /结果需要只读核验/);
  assert.doesNotMatch(html, /失败 · ok/);
  assert.match(html, /shell-status shell-status--warning/);
});

test("distinguishes a reconciliation gate for a non-shell tool", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({
        id: "gated-write", ordinal: 1, kind: "file_change", status: "failed",
        toolCall: {
          id: "tool-gated-write", itemId: "gated-write", modelStepIndex: 1, batchOrder: 0,
          providerCallId: "provider-gated-write", toolName: "write_file",
          status: "failed", startedAt: 1_000, completedAt: 2_000,
          argumentsJson: JSON.stringify({ path: "output.txt" }),
          resultJson: JSON.stringify({
            outcome: "error", code: "TOOL_RECONCILIATION_REQUIRED",
            summary: "A previous side effect must be reconciled", data: {},
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

  assert.match(html, /未执行，等待只读核验/);
  assert.doesNotMatch(html, /失败 output\.txt/);
});

test("does not show a succeeded run with a reconciliation barrier as complete", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[item({ id: "user", ordinal: 1, kind: "user_message", content: "检查" })]}
      runs={[{ ...run, reconciliationRequired: true }]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /完成状态待核验，尚未确认/);
  assert.doesNotMatch(html, /已完成/);
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

test("renders more actions dropdown on assistant messages", () => {
  const html = renderToStaticMarkup(
    <ExecutionFeed
      items={[
        item({ id: "user", ordinal: 1, kind: "user_message", content: "提问" }),
        item({ id: "assistant", ordinal: 2, kind: "assistant_message", content: "回答" }),
      ]}
      runs={[run]}
      workspaceRoot="/Users/test/workspace"
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );

  assert.match(html, /class="dropdown-wrapper response-more-dropdown"/);
  assert.match(html, /aria-label="更多操作"/);
  assert.match(html, /title="更多操作"/);
});

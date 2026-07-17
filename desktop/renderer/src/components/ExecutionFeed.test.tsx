import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import type { Item, Run } from "../contracts.js";
import { ExecutionFeed } from "./ExecutionFeed.js";


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
        toolName: "run_shell",
        status: "completed",
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
    <ExecutionFeed items={items} runs={[run]} approvals={[]} disabled={false} onApproval={() => {}} />,
  );

  assert.match(html, /<details class="process-group">/);
  assert.match(html, /已处理 1m 5s/);
  assert.match(html, /已运行 git diff --check/);
  assert.match(html, /无输出/);
  assert.doesNotMatch(html, /Run 已完成/);
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
      disabled={false}
      onApproval={() => {}}
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
      disabled={false}
      onApproval={() => {}}
    />,
  );

  assert.match(html, /class="thinking-indicator"/);
  assert.match(html, /正在思考/);
});

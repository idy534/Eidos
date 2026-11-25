import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Item, Run, SessionCheckpoint } from "../contracts.js";
import { ExecutionFeed } from "./ExecutionFeed.js";
import { parseStructuredReviewFindings } from "./review-findings.js";

describe("parseStructuredReviewFindings", () => {
  it("parses severity, title, path, line, body, and suggestion from markdown", () => {
    const markdown = [
      "### [P1] 空指针解引用风险 — `src/runtime/worker.ts:42`",
      "当会话被清理后 `session` 可能为 undefined，直接读取属性会抛出异常。",
      "",
      "**建议修改**：",
      "```ts",
      "if (!session) return;",
      "```",
      "",
      "- [P2] 缺少重复提交守卫 — desktop/renderer/src/App.tsx:108",
      "连续双击可能触发两次提交。",
    ].join("\n");

    const parsed = parseStructuredReviewFindings(markdown);
    expect(parsed).toHaveLength(2);
    expect(parsed[0]).toMatchObject({
      severity: "P1",
      title: "空指针解引用风险",
      path: "src/runtime/worker.ts",
      line: 42,
    });
    expect(parsed[0]?.body).toContain("session");
    expect(parsed[0]?.suggestion).toContain("if (!session) return;");
    expect(parsed[1]).toMatchObject({
      severity: "P2",
      title: "缺少重复提交守卫",
      path: "desktop/renderer/src/App.tsx",
      line: 108,
    });
  });
});

describe("ExecutionFeed checkpoint rewind and review adoption", () => {
  it("renders rewind action button and adoptable review finding buttons", () => {
    const run: Run = {
      id: "run-1",
      sessionId: "session-1",
      status: "succeeded",
      modelId: "deepseek/deepseek-chat",
      modelStepCount: 1,
      createdAt: 1,
      updatedAt: 2,
      completedAt: 2,
    };
    const items: Item[] = [
      {
        id: "user-1",
        sessionId: "session-1",
        runId: "run-1",
        ordinal: 1,
        kind: "user_message",
        status: "completed",
        createdAt: 1,
        content: "/review",
      },
      {
        id: "assistant-1",
        sessionId: "session-1",
        runId: "run-1",
        ordinal: 2,
        kind: "assistant_message",
        status: "completed",
        createdAt: 2,
        completedAt: 2,
        content: "### [P1] 缺少输入校验 — `src/index.ts:15`\n需要判空。",
      },
    ];
    const checkpoint: SessionCheckpoint = {
      id: "chk-1",
      runId: "run-1",
      itemOrdinal: 0,
      creationSeq: 1,
      workspaceIdentityHash: "h".repeat(64),
      gitHead: "a".repeat(40),
      gitSnapshotId: "snap-1",
      reconciliationRequired: false,
      createdAt: 1,
    };

    const onRewindCheckpoint = vi.fn();
    const onAdoptReviewFinding = vi.fn();
    const onAddReviewFindingComment = vi.fn();

    render(
      <ExecutionFeed
        items={items}
        runs={[run]}
        approvals={[]}
        checkpointsByRunId={new Map([[run.id, checkpoint]])}
        onRewindCheckpoint={onRewindCheckpoint}
        onAdoptReviewFinding={onAdoptReviewFinding}
        onAddReviewFindingComment={onAddReviewFindingComment}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "回退工作区到此轮之前" }));
    expect(onRewindCheckpoint).toHaveBeenCalledWith(checkpoint);

    fireEvent.click(screen.getByRole("button", { name: "一键采纳修复" }));
    expect(onAdoptReviewFinding).toHaveBeenCalledWith(
      expect.objectContaining({ severity: "P1", path: "src/index.ts", line: 15 }),
    );

    fireEvent.click(screen.getByRole("button", { name: "加入评论" }));
    expect(onAddReviewFindingComment).toHaveBeenCalledWith(
      expect.objectContaining({ severity: "P1", path: "src/index.ts", line: 15 }),
    );
  });
});

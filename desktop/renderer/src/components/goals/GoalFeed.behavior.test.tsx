import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ExecutionFeed } from "../ExecutionFeed.js";
import type { Item, Run } from "../../../contracts.js";

it("labels an automatic goal Run without inventing a user message", () => {
  const run: Run = { id: "run", sessionId: "session", modelId: "model", status: "succeeded", modelStepCount: 1, createdAt: 1, updatedAt: 2,
    goalOrigin: { goalId: "goal", trigger: "continuation", sequence: 2 } };
  const item: Item = { id: "answer", runId: run.id, sessionId: run.sessionId, ordinal: 1, kind: "assistant_message", status: "completed", content: "检查已完成。", createdAt: 2 };
  const { container } = render(<ExecutionFeed runs={[run]} items={[item]} approvals={[]}
    goalActivities={[{ eventId: 1, runId: run.id, status: "complete", kind: "status", occurredAt: 2 }]} />);
  expect(screen.getByText("目标续跑 · 第 2 轮")).toBeInTheDocument();
  expect(screen.getByText("目标已完成 · 模型声明")).toBeInTheDocument();
  expect(container.querySelector(".user-message-bubble")).toBeNull();
  expect(screen.queryByRole("button", { name: "重新回答" })).toBeNull();
});

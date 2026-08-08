import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import type { Item, ModelOption, ResponseActionState, Run } from "../contracts.js";
import { ExecutionFeed } from "./ExecutionFeed.js";


const run: Run = {
  id: "run-1",
  sessionId: "session-1",
  userInput: "原始问题",
  status: "succeeded",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 1000,
  startedAt: 1000,
  updatedAt: 2000,
  completedAt: 2000,
};

const items: Item[] = [
  {
    id: "user-1",
    sessionId: "session-1",
    runId: "run-1",
    ordinal: 1,
    kind: "user_message",
    status: "completed",
    createdAt: 1000,
    completedAt: 1000,
    content: "原始问题",
  },
  {
    id: "assistant-1",
    sessionId: "session-1",
    runId: "run-1",
    ordinal: 2,
    modelStepIndex: 1,
    kind: "assistant_message",
    status: "completed",
    createdAt: 1500,
    completedAt: 2000,
    content: "原始回答",
  },
];

const models: ModelOption[] = [
  {
    id: "deepseek-v4-flash",
    name: "DeepSeek-V4 Flash",
    vendor: "DeepSeek",
    provider: "deepseek",
    url: "https://example.invalid/chat/completions",
    supportsToolCall: true,
    supportsImages: false,
    supportsReasoning: false,
  },
];

function render(
  responseActionState: ResponseActionState = {
    feedback: [{ itemId: "assistant-1", value: "up" }],
    revisions: [],
  },
): string {
  return renderToStaticMarkup(
    <ExecutionFeed
      items={items}
      runs={[run]}
      models={models}
      responseActionState={responseActionState}
      approvals={[]}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );
}

test("latest completed exchange exposes edit, feedback, regenerate and model", () => {
  const markup = render();
  assert.match(markup, /aria-label="编辑并重新发送"/);
  assert.match(markup, /aria-label="取消点赞"/);
  assert.match(markup, /aria-label="差评"/);
  assert.match(markup, /aria-label="重新回答"/);
  assert.match(markup, /DeepSeek-V4 Flash/);
});

test("superseded run is removed from canonical execution feed", () => {
  const markup = render({
    feedback: [],
    revisions: [{
      runId: "run-2",
      sourceRunId: "run-1",
      kind: "edit",
    }],
  });
  assert.doesNotMatch(markup, /原始问题/);
  assert.doesNotMatch(markup, /原始回答/);
});
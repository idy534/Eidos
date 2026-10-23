import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Composer } from "../src/components/Composer.js";
import { QueuedMessages } from "../src/components/QueuedMessages.js";
import { ExecutionFeed } from "../src/components/ExecutionFeed.js";
import type { Item, Run, RuntimeNotification } from "../src/contracts.js";
import type { QueuedMessage } from "../../shared/queued-runs.js";
import "../src/styles.css";

let messages: QueuedMessage[] = [{ sessionId: "s", runId: "first", userInput: "下一步，完善消息队列", references: [] }];
const listeners = new Set<(notice: RuntimeNotification) => void>();
const notify = (notice: RuntimeNotification) => listeners.forEach((listener) => listener(notice));
const noop = () => {};
const active: Run = { id: "active", sessionId: "s", status: "running", modelId: "m", modelStepCount: 1, createdAt: Date.now() - 20_000, startedAt: Date.now() - 20_000, updatedAt: Date.now(), allowedActions: ["cancel"] };
let steering: Item | undefined;
window.eidosRuntime = {
  listQueuedRuns: async () => ({ items: messages }),
  onNotification: (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
  updateQueuedRun: async (request) => {
    messages = messages.map((message) => message.runId === request.runId ? { ...message, userInput: request.userInput } : message);
    return { change: { kind: "updated", sessionId: "s", runId: request.runId, userInput: request.userInput, updatedAt: 2 } };
  },
  deleteQueuedRun: async (request) => {
    messages = messages.filter((message) => message.runId !== request.runId);
    return { change: { kind: "deleted", sessionId: "s", runId: request.runId } };
  },
  steerQueuedRun: async (request) => {
    const source = messages.find((message) => message.runId === request.runId)!;
    steering = { id: "steering-item", sessionId: "s", runId: "active", ordinal: 3, kind: "user_message", status: "in_progress", content: source.userInput, createdAt: Date.now() };
    messages = messages.filter((message) => message.runId !== request.runId);
    return { change: { kind: "steered", sessionId: "s", runId: request.runId, targetRunId: request.targetRunId, itemId: steering.id } };
  },
} as typeof window.eidosRuntime;
Object.assign(window, { setQueuedMessages: (next: QueuedMessage[]) => {
  messages = next;
  notify({ method: "run/queueChanged", params: { change: { kind: "deleted", sessionId: "s", runId: "fixture-change" } } });
} });

function Fixture() {
  const [input, setInput] = useState("主输入框中的草稿");
  const [items, setItems] = useState<Item[]>([
    { id: "user", sessionId: "s", runId: "active", ordinal: 1, kind: "user_message", status: "completed", content: "当前任务", createdAt: 1 },
    { id: "progress", sessionId: "s", runId: "active", ordinal: 2, kind: "assistant_message", status: "completed", content: "我正在整理资料", createdAt: 2 },
  ]);
  Object.assign(window, { consumeSteering: () => setItems((previous) => previous.map((item) => item.id === "steering-item" ? { ...item, status: "completed" } : item)) });
  return <div className="workspace"><div className="workspace-content"><div className="workspace-main"><div className="input-drop-zone">
    <ExecutionFeed items={items} runs={[active]} approvals={[]} />
    <QueuedMessages sessionId="s" ready activeRunId="active" onChanged={(change) => {
      if (change.kind === "steered" && steering) setItems((previous) => [...previous, steering!]);
    }} onRefresh={noop} />
    <Composer composerMode="running" activeRun={active}
      input={input} modelList={undefined} selectedModelId="m" contextUsage={undefined}
      modelConfigured modelLoading={false} isSubmitting={false} submitKind={undefined} cancelingRunId={undefined}
      onInputChange={setInput} onSubmit={noop} onCancel={noop} onModelChange={noop} onOpenModelSettings={noop} />
  </div></div></div></div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);

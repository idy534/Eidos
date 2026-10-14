import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Composer } from "../src/components/Composer.js";
import { QueuedMessages } from "../src/components/QueuedMessages.js";
import type { RuntimeNotification } from "../src/contracts.js";
import type { QueuedMessage } from "../../shared/queued-runs.js";
import "../src/styles.css";

let messages: QueuedMessage[] = [{ sessionId: "s", runId: "first", userInput: "下一步，完善消息队列", references: [] }];
const listeners = new Set<(notice: RuntimeNotification) => void>();
const notify = (notice: RuntimeNotification) => listeners.forEach((listener) => listener(notice));
const noop = () => {};
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
} as typeof window.eidosRuntime;
Object.assign(window, { setQueuedMessages: (next: QueuedMessage[]) => {
  messages = next;
  notify({ method: "run/queueChanged", params: { change: { kind: "deleted", sessionId: "s", runId: "fixture-change" } } });
} });

function Fixture() {
  const [input, setInput] = useState("主输入框中的草稿");
  return <div className="workspace"><div className="workspace-content"><div className="workspace-main"><div className="input-drop-zone">
    <div className="feed"><p>当前任务正在进行</p></div>
    <QueuedMessages sessionId="s" ready onChanged={noop} onRefresh={noop} />
    <Composer composerMode="running" activeRun={{ id: "active", sessionId: "s", status: "running", modelId: "m", modelStepCount: 1, createdAt: 1, updatedAt: 1, allowedActions: ["cancel"] }}
      input={input} modelList={undefined} selectedModelId="m" contextUsage={undefined}
      modelConfigured modelLoading={false} isSubmitting={false} submitKind={undefined} cancelingRunId={undefined}
      onInputChange={setInput} onSubmit={noop} onCancel={noop} onModelChange={noop} onOpenModelSettings={noop} />
  </div></div></div></div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);

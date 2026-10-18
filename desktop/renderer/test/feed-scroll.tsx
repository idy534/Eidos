import { useState } from "react";
import { createRoot } from "react-dom/client";
import type { Item, Run } from "../src/contracts.js";
import { ArtifactProvider } from "../src/components/ArtifactContext.js";
import { ExecutionFeed } from "../src/components/ExecutionFeed.js";
import "../src/styles.css";

const noop = () => {};
const run: Run = {
  id: "scroll-run", sessionId: "scroll-session", status: "running", modelId: "test",
  modelStepCount: 1, createdAt: Date.now(), startedAt: Date.now(), updatedAt: Date.now(),
};
const common = { sessionId: run.sessionId, runId: run.id, status: "completed" as const, createdAt: 1, completedAt: 2 };
const initial: Item[] = [{ ...common, id: "user", ordinal: 0, kind: "user_message", content: "检查长会话的滚动行为。" }];
for (let index = 0; index < 35; index++) initial.push({
  ...common, id: `tool-${index}`, ordinal: index + 1, kind: "command_execution", content: "done\n".repeat(12),
  toolCall: {
    id: `call-${index}`, itemId: `tool-${index}`, modelStepIndex: 1, batchOrder: index,
    providerCallId: `provider-${index}`, toolName: "run_shell", status: "completed", startedAt: 1, completedAt: 2,
    argumentsJson: JSON.stringify({ command: `echo process-${index}` }),
    resultJson: JSON.stringify({ outcome: "success", code: "ok", summary: "Done", data: { stdout: "done", stderr: "", exitCode: 0 } }),
  },
});
initial.push({ ...common, id: "answer", ordinal: 40, kind: "assistant_message", status: "in_progress", content: "正在生成。\n\n".repeat(15) });

let finishPreview: ((url: string) => void) | undefined;
Object.assign(window, { eidosRuntime: {
  prepareWorkspacePreview: () => new Promise<string>((resolve) => { finishPreview = resolve; }),
  releaseWorkspacePreview: async () => {},
} });

function FeedScrollFixture() {
  const [items, setItems] = useState<Item[]>([]);
  const [runs, setRuns] = useState([run]);
  const append = (text = "\n\n新增内容。") => setItems((old) => old.map((item) => item.id === "answer" ? { ...item, content: item.content + text } : item));
  Object.assign(window, {
    startFeed: () => { setItems(initial); setRuns([run]); },
    appendFeed: () => append(),
    finishFeed: () => {
      setRuns([{ ...run, status: "succeeded", completedAt: Date.now() }]);
      setItems((old) => old.map((item) => ({ ...item, status: "completed" })));
    },
    addLateImage: () => append("\n\n![延迟图片](/workspace/late.png)"),
    finishLateImage: () => finishPreview?.("data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><rect width="400" height="600" fill="#246845"/></svg>')),
  });
  return <ArtifactProvider value={{ sessionId: run.sessionId, executionRoot: "/workspace", openFile: noop, openBrowser: noop }}>
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <ExecutionFeed items={items} runs={runs} approvals={[]} onApprove={noop} onReject={noop} />
    </div>
  </ArtifactProvider>;
}

createRoot(document.getElementById("root")!).render(<FeedScrollFixture />);

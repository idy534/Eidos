import { useState } from "react";
import { createRoot } from "react-dom/client";
import type { Item, Run } from "../src/contracts.js";
import { Composer } from "../src/components/Composer.js";
import { SessionCanvas } from "../src/components/SessionCanvas.js";
import { ExecutionFeed } from "../src/components/ExecutionFeed.js";
import { ArtifactProvider } from "../src/components/ArtifactContext.js";
import "../src/styles.css";

const runs: Run[] = [{
  id: "layout-run", sessionId: "layout-session", status: "succeeded", modelId: "layout-model",
  modelStepCount: 1, createdAt: 1, updatedAt: 2, completedAt: 2,
}];
const items: Item[] = [{
  id: "layout-reply", sessionId: "layout-session", runId: "layout-run", ordinal: 1,
  kind: "assistant_message", status: "completed", createdAt: 1, completedAt: 2,
  content: "```javascript\nconst stable = true;\n```\n\n"
    + Array.from({ length: 70 }, (_, index) => `Record ${index}: conversation history.`).join("\n\n")
    + "\n\n![Layout preview](/workspace/layout.png)",
}];
const noop = () => {};
let previewRequests = 0;
const previewUrl = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="180"><rect width="300" height="180" fill="#246845"/></svg>');
Object.assign(window, {
  eidosRuntime: {
    prepareWorkspacePreview: () => {
      previewRequests++;
      return new Promise((resolve) => setTimeout(() => resolve(previewUrl), 100));
    },
    releaseWorkspacePreview: async () => {},
  },
  getPreviewRequests: () => previewRequests,
});

function ComposerLayout() {
  const [input, setInput] = useState("");
  Object.assign(window, { setComposerDraft: setInput });
  return (
    <ArtifactProvider value={{ sessionId: "layout-session", executionRoot: "/workspace", openFile: noop, openBrowser: noop }}>
    <div className="workspace">
      <div className="workspace-body">
        <div className="workspace-main-column">
          <header className="session-header">Composer Layout Test</header>
          <div className="workspace-content">
            <div className="workspace-main">
              <div className="input-drop-zone">
                <SessionCanvas sessionId="layout-session" welcome={false}
                  feed={<ExecutionFeed items={items} runs={runs} approvals={[]} />}>
                <Composer
                  composerMode="idle" activeRun={undefined} input={input}
                  modelList={undefined} selectedModelId={undefined} contextUsage={undefined}
                  modelConfigured={false} modelLoading={false} isSubmitting={false}
                  submitKind={undefined} cancelingRunId={undefined}
                  onInputChange={setInput} onSubmit={noop} onCancel={noop}
                  onModelChange={noop} onOpenModelSettings={noop}
                />
                </SessionCanvas>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </ArtifactProvider>
  );
}

createRoot(document.getElementById("root")!).render(<ComposerLayout />);

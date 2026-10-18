import { createRoot } from "react-dom/client";
import { ContextIndicator } from "../src/components/ContextIndicator.js";
import "../src/styles.css";

let requests = 0;
Object.assign(window, {
  contextRequestCount: () => requests,
  eidosRuntime: {
    readContextDetails: async () => ({
      runId: "fixture-run", measurement: "provider", activeTokens: 12000,
      projectedTokens: 11800, contextWindowTokens: 128000,
      sections: [{ name: "instructions", tokens: 2000 }, { name: "tool_results", tokens: 6000 }, { name: "conversation", tokens: 3000 }, { name: "overhead", tokens: 800 }],
      clearedToolResults: 3, calibrationSamples: 4, lastEstimationErrorPercent: -1.7,
      compaction: { requestId: "previous", stage: "finished", trigger: "soft_target", status: "succeeded", strategy: "model", durationMs: 1500, beforeTokens: 100000, afterTokens: 16000, inputTokens: 20000, outputTokens: 1200 },
    }),
    requestContextCompaction: async () => { requests++; return { requestId: "manual", stage: "requested", trigger: "manual", status: "queued" }; },
  },
});
createRoot(document.getElementById("root")!).render(
  <div className="context-fixture"><div className="composer-actions"><ContextIndicator runId="fixture-run" usage={{ activeTokens: 12000, windowTokens: 128000, percentUsed: 9.4, source: "provider" }} /></div></div>,
);

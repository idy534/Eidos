import { createRoot } from "react-dom/client";
import type { ContextDetails } from "../../shared/context-management.js";
import type { RuntimeNotification } from "../src/contracts.js";
import { useContextUsageController } from "../src/app/useContextUsageController.js";
import { ContextIndicator } from "../src/components/ContextIndicator.js";
import "../src/styles.css";

let requests = 0;
let notify: ((notice: RuntimeNotification) => void) | undefined;
let details: ContextDetails = {
  runId: "fixture-run", measurement: "provider", activeTokens: 12000,
  projectedTokens: 11800, contextWindowTokens: 128000,
  sections: [{ name: "instructions", tokens: 2000 }, { name: "tool_results", tokens: 6000 }, { name: "conversation", tokens: 3000 }, { name: "overhead", tokens: 1000 }],
  clearedToolResults: 3, calibrationSamples: 4, lastEstimationErrorPercent: -1.7,
  compaction: { requestId: "previous", stage: "finished", trigger: "soft_target", status: "succeeded", strategy: "model", durationMs: 1500, beforeTokens: 100000, afterTokens: 16000, inputTokens: 20000, outputTokens: 1200 },
};
Object.assign(window, {
  contextRequestCount: () => requests,
  contextSetScenario: (scenario: "overflow" | "compacted") => {
    const values = scenario === "overflow" ? [6000, 13000, 85000, 65000, 7000, 5376] : [1000, 2000, 6000, 6000, 2000, 1000];
    details = { ...details, activeTokens: scenario === "overflow" ? 181376 : 18000,
      contextWindowTokens: 100000, measurement: scenario === "overflow" ? "provider" : "provider_calibrated",
      planId: "plan-" + scenario, sections: (["instructions", "tools", "conversation", "tool_results", "summary", "overhead"] as const).map((name, index) => ({ name, tokens: values[index]! })),
    };
    notify?.({ method: "context/compacted", params: { sessionId: "fixture-session", runId: "fixture-run", summaryId: "summary-current", sourceItemCount: 10, phase: "mid_turn" } });
  },
  eidosRuntime: {
    readContextDetails: async () => details,
    onNotification: (callback: typeof notify) => { notify = callback; return () => { notify = undefined; }; },
    requestContextCompaction: async () => { requests++; return { requestId: "manual", stage: "requested", trigger: "manual", status: "queued" }; },
  },
});
function Fixture() {
  const [state, actions] = useContextUsageController({ ready: true, sessionId: "fixture-session", modelId: "fixture-model", runId: "fixture-run" });
  // The production AppShell owns this subscription.
  notify = actions.handleNotification;
  return <div className="context-fixture"><div className="composer-actions"><ContextIndicator runId="fixture-run" usage={state.usage} details={state.details} /></div></div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);

import { createRoot } from "react-dom/client";
import { AppShell } from "../src/app/AppShell.js";
import { deriveRuntimePresentation } from "../src/session-state.js";
import { createWelcomeRuntime } from "./welcome-runtime.js";
import "../src/styles.css";

window.eidosRuntime = createWelcomeRuntime();
window.localStorage.setItem("eidos.sidebarOpen", String(window.innerWidth > 760));
const status = { state: "ready", protocolVersion: 1, runtimeVersion: "test", runShell: true,
  modelConfigured: true, storageHealth: { state: "ready" } } as const;
createRoot(document.getElementById("root")!).render(<AppShell runtime={{ status,
  presentation: deriveRuntimePresentation(status), isStorageReady: true,
  restarting: false, restartRuntime: async () => {} }} />);

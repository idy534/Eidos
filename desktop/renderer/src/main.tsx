import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "react-diff-view/style/index.css";
import "katex/dist/katex.min.css";

import { App } from "./App";
import { applyPlatformDataset } from "./platform";
import { applyThemePreference, readThemePreference } from "./theme";
import "./styles.css";

applyPlatformDataset();
applyThemePreference(readThemePreference());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "react-diff-view/style/index.css";

import { App } from "./App";
import "./styles.css";
import "./components/response-actions.css";


createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

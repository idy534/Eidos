import { createRoot } from "react-dom/client";
import { ManagedResourceSettings } from "../src/components/settings/ManagedResourceSettings.js";
import type { ResourceStatus } from "../../shared/managed-resources.js";
import "../src/styles.css";
import "../src/components/settings/SkillSettings.css";
let status: ResourceStatus = { available: true, directory: "/Users/test/Eidos/Resource", packs: [
  { id: "document-python", title: "基础文档处理", version: "1", phase: "ready" },
  { id: "office", title: "Office 转换与重算", version: "1", phase: "downloading", downloadedBytes: 50, totalBytes: 100 },
  { id: "ocr", title: "OCR 引擎", version: "1", phase: "failed", errorCode: "resource_disk_full" },
] };
window.eidosRuntime = {
  getResourceStatus: async () => status,
  retryResource: async ({ packId }) => { status = { ...status, packs: status.packs?.map(pack => pack.id === packId ? { ...pack, phase: "downloading", downloadedBytes: 0, totalBytes: 100, errorCode: null } : pack) ?? [] }; return { accepted: true }; },
} as typeof window.eidosRuntime;
createRoot(document.getElementById("root")!).render(<main style={{ padding: "1rem", maxWidth: 800, margin: "auto" }}><h2>系统 Skill</h2><ManagedResourceSettings /></main>);

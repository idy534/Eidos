import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");

const errors = [];

function readFile(relPath) {
  const fullPath = path.join(rootDir, relPath);
  if (!fs.existsSync(fullPath)) {
    errors.push(`File not found: ${relPath}`);
    return "";
  }
  return fs.readFileSync(fullPath, "utf8");
}

// 1. Verify runtime-client.ts does NOT declare duplicate interfaces or types
const runtimeClientContent = readFile("desktop/main/runtime-client.ts");
const forbiddenDeclarations = [
  "RuntimeHealth",
  "Session",
  "SessionSnapshot",
  "Run",
  "ModelStatus",
  "ModelOption",
  "ModelListResult",
  "ApprovalRequest",
  "ApprovalDecision",
  "PluginRecord",
  "SkillMetadata",
  "McpServerRecord",
  "RuntimeNotification",
  "ToolCall",
  "Item",
];

for (const name of forbiddenDeclarations) {
  const interfaceRegex = new RegExp(`export\\s+(interface|type)\\s+${name}\\b`);
  if (interfaceRegex.test(runtimeClientContent)) {
    errors.push(`runtime-client.ts must not declare interface/type ${name}. Import it from shared.`);
  }
}

// Verify runtime-client.ts imports from shared
if (!/import\s+type\s+\{[\s\S]*\}\s+from\s+["']\.\.\/shared\/(?:index|domain-contracts)\.js["']/.test(runtimeClientContent)) {
  errors.push(`runtime-client.ts must import domain contracts from ../shared/index.js or ../shared/domain-contracts.js`);
}

// 2. Verify Preload file
const preloadPath = path.join(rootDir, "desktop/main/preload.ts");
const preloadContent = fs.existsSync(preloadPath) ? fs.readFileSync(preloadPath, "utf8") : readFile("desktop/main/preload.cts");
if (!preloadContent.includes('import { IPC } from "../shared/index.js"') && !preloadContent.includes('import { IPC } from "../shared/ipc-channels.js"')) {
  errors.push("preload.ts must import IPC from shared");
}
if (/const\s+IPC\s*=/.test(preloadContent)) {
  errors.push("preload.ts must not declare a local IPC object.");
}
if (!preloadContent.includes("EidosRuntimeAPI")) {
  errors.push("preload.ts must expose typed EidosRuntimeAPI.");
}

// 3. Verify main.ts imports shared IPC and contracts
const mainContent = readFile("desktop/main/main.ts");
if (!/import\s+[\s\S]*IPC[\s\S]*from\s+["']\.\.\/shared\/(?:index|ipc-channels)\.js["']/.test(mainContent)) {
  errors.push("main.ts must import IPC channels from shared");
}

// 4. Check for raw string literals of known channels outside shared and test files
const KNOWN_CHANNELS = [
  "runtime:get-status",
  "runtime:health",
  "run:start",
  "run:continue",
  "approval:respond",
  "app:new-task",
  "app:open-workspace",
];

const rendererSrcDir = path.join(rootDir, "desktop/renderer/src");
function scanForRawChannels(dir) {
  if (!fs.existsSync(dir)) return;
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules" && entry.name !== ".git") {
        scanForRawChannels(full);
      }
    } else if (entry.isFile() && (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx"))) {
      if (entry.name.includes(".test.") || entry.name.includes("/test/")) continue;
      const content = fs.readFileSync(full, "utf8");
      for (const channel of KNOWN_CHANNELS) {
        if (content.includes(`"${channel}"`) || content.includes(`'${channel}'`)) {
          errors.push(`Raw channel string literal "${channel}" found in production file ${path.relative(rootDir, full)}`);
        }
      }
    }
  }
}
scanForRawChannels(rendererSrcDir);

if (errors.length > 0) {
  console.error("❌ Contract Check Failures:");
  for (const err of errors) {
    console.error(`  - ${err}`);
  }
  process.exit(1);
} else {
  console.log("✓ All Desktop domain and IPC contract checks passed.");
}

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
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
if (!/import\s+type\s+\{[\s\S]*\}\s+from\s+["']\.\.\/shared\/index\.js["']/.test(runtimeClientContent)) {
  errors.push(`runtime-client.ts must import domain contracts from ../shared/index.js`);
}

// 2. Verify Preload file
const preloadContent = readFile("desktop/main/preload.cts");
if (!preloadContent.includes('import { IPC } from "../shared/index.js"')) {
  errors.push("preload.cts must import IPC from ../shared/index.js");
}
if (/const\s+IPC\s*=/.test(preloadContent)) {
  errors.push("preload.cts must not declare a local IPC object.");
}
if (!preloadContent.includes("EidosRuntimeAPI")) {
  errors.push("preload.cts must expose typed EidosRuntimeAPI.");
}

// 3. Verify main.ts imports shared IPC and contracts
const mainContent = readFile("desktop/main/main.ts");
if (!/import\s+[\s\S]*IPC[\s\S]*from\s+["']\.\.\/shared\/index\.js["']/.test(mainContent)) {
  errors.push("main.ts must import IPC channels from ../shared/index.js");
}

// 4. Verify no raw IPC channel string literals in renderer production code
const rendererSrcDir = path.join(rootDir, "desktop/renderer/src");
function scanForRawChannels(dir) {
  if (!fs.existsSync(dir)) return;
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      scanForRawChannels(full);
    } else if (entry.isFile() && (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx"))) {
      if (entry.name.endsWith(".test.ts") || entry.name.endsWith(".test.tsx")) continue;
      const content = fs.readFileSync(full, "utf8");
      if (/["']eidos:[a-z_-]+:[a-z_-]+["']/.test(content)) {
        errors.push(`Raw IPC channel string literal found in renderer production file: ${path.relative(rootDir, full)}`);
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

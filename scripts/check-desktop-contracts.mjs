import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");

const KNOWN_CHANNELS = [
  "runtime:get-status",
  "runtime:health",
  "run:start",
  "run:continue",
  "approval:respond",
  "app:new-task",
  "app:open-workspace",
];

function checkFilesRecursively(dir, results = []) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules" && entry.name !== ".git") {
        checkFilesRecursively(fullPath, results);
      }
    } else if (entry.isFile() && (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx") || entry.name.endsWith(".cts") || entry.name.endsWith(".cjs"))) {
      results.push(fullPath);
    }
  }
  return results;
}

console.log("🔍 Checking IPC contract single source of truth...");

const desktopDir = path.join(rootDir, "desktop");
const files = checkFilesRecursively(desktopDir);

let errors = [];

// 1. Check for duplicate raw IPC channel objects
for (const file of files) {
  const relPath = path.relative(rootDir, file);
  if (relPath.includes("desktop/shared/ipc-channels.ts")) continue;

  const content = fs.readFileSync(file, "utf8");

  if (/const\s+IPC\s*=\s*\{/.test(content)) {
    errors.push(`Duplicate IPC channel object literal found in ${relPath}`);
  }
}

// 2. Check for raw string literals of known channels outside shared/ipc-channels.ts and test files
for (const file of files) {
  const relPath = path.relative(rootDir, file);
  if (relPath.includes("desktop/shared/ipc-channels.ts")) continue;
  if (relPath.includes(".test.")) continue;
  if (relPath.includes("/test/")) continue;

  const content = fs.readFileSync(file, "utf8");

  for (const channel of KNOWN_CHANNELS) {
    if (content.includes(`"${channel}"`) || content.includes(`'${channel}'`)) {
      errors.push(`Raw channel string literal "${channel}" found in production file ${relPath}`);
    }
  }
}

// 3. Verify preload imports IPC from ipc-channels
const preloadPath = path.join(desktopDir, "main/preload.ts");
if (fs.existsSync(preloadPath)) {
  const preloadContent = fs.readFileSync(preloadPath, "utf8");
  if (!preloadContent.includes('import { IPC } from "../shared/ipc-channels.js"') && !preloadContent.includes("import { IPC }")) {
    errors.push(`preload.ts does not import IPC from shared/ipc-channels.js`);
  }
} else {
  errors.push(`preload.ts is missing at ${preloadPath}`);
}

if (errors.length > 0) {
  console.error("❌ IPC Contract Drift Check Failed:");
  for (const err of errors) {
    console.error(`  - ${err}`);
  }
  process.exit(1);
} else {
  console.log("✅ IPC Contract Single Source of Truth verified cleanly!");
}

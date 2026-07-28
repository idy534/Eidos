import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

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

function parseAST(filePath, text) {
  const kind = filePath.endsWith(".tsx")
    ? ts.ScriptKind.TSX
    : filePath.endsWith(".cts")
      ? ts.ScriptKind.CTS
      : ts.ScriptKind.TS;
  return ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true, kind);
}

// 1. Verify runtime-client.ts AST declarations
const runtimeClientPath = "desktop/main/runtime-client.ts";
const runtimeClientText = readFile(runtimeClientPath);
if (runtimeClientText) {
  const sf = parseAST(runtimeClientPath, runtimeClientText);
  const forbiddenDeclarations = new Set([
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
  ]);

  let importsSharedContracts = false;

  for (const node of sf.statements) {
    if (ts.isImportDeclaration(node)) {
      const moduleSpec = node.moduleSpecifier.text;
      if (
        moduleSpec === "../shared/index.js"
        || moduleSpec === "../shared/domain-contracts.js"
      ) {
        importsSharedContracts = true;
      }
    }

    if (
      (ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node))
      && node.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)
    ) {
      if (forbiddenDeclarations.has(node.name.text)) {
        errors.push(
          `runtime-client.ts AST error: must not declare exported interface/type ${node.name.text}. Import it from shared.`,
        );
      }
    }
  }

  if (!importsSharedContracts) {
    errors.push(
      "runtime-client.ts must import domain contracts from ../shared/index.js or ../shared/domain-contracts.js",
    );
  }
}

// 2. Verify Preload file AST
const preloadRelPath = fs.existsSync(path.join(rootDir, "desktop/main/preload.ts"))
  ? "desktop/main/preload.ts"
  : "desktop/main/preload.cts";
const preloadText = readFile(preloadRelPath);
if (preloadText) {
  const sf = parseAST(preloadRelPath, preloadText);
  let importsIPC = false;
  let definesLocalIPC = false;
  let referencesTypedAPI = false;

  for (const node of sf.statements) {
    if (ts.isImportDeclaration(node)) {
      const moduleSpec = node.moduleSpecifier.text;
      if (
        moduleSpec === "../shared/index.js"
        || moduleSpec === "../shared/ipc-channels.js"
      ) {
        if (node.importClause?.namedBindings && ts.isNamedImports(node.importClause.namedBindings)) {
          for (const spec of node.importClause.namedBindings.elements) {
            if (spec.name.text === "IPC") {
              importsIPC = true;
            }
          }
        }
      }
    }

    if (ts.isVariableStatement(node)) {
      for (const decl of node.declarationList.declarations) {
        if (ts.isIdentifier(decl.name) && decl.name.text === "IPC") {
          definesLocalIPC = true;
        }
      }
    }

    if (preloadText.includes("EidosRuntimeAPI")) {
      referencesTypedAPI = true;
    }
  }

  if (!importsIPC) {
    errors.push(`${preloadRelPath} AST error: must import IPC from shared`);
  }
  if (definesLocalIPC) {
    errors.push(`${preloadRelPath} AST error: must not declare a local IPC object`);
  }
  if (!referencesTypedAPI) {
    errors.push(`${preloadRelPath} AST error: must expose typed EidosRuntimeAPI`);
  }
}

// 3. Verify main.ts AST
const mainPath = "desktop/main/main.ts";
const mainText = readFile(mainPath);
if (mainText) {
  const sf = parseAST(mainPath, mainText);
  let importsIPC = false;
  let importsShutdown = false;
  let definesPerformShutdown = false;

  for (const node of sf.statements) {
    if (ts.isImportDeclaration(node)) {
      const moduleSpec = node.moduleSpecifier.text;
      if (
        moduleSpec === "../shared/index.js"
        || moduleSpec === "../shared/ipc-channels.js"
      ) {
        importsIPC = true;
      }
      if (moduleSpec === "./runtime-shutdown.js") {
        if (node.importClause?.namedBindings && ts.isNamedImports(node.importClause.namedBindings)) {
          for (const spec of node.importClause.namedBindings.elements) {
            if (spec.name.text === "shutdownRuntime") {
              importsShutdown = true;
            }
          }
        }
      }
    }

    if (ts.isFunctionDeclaration(node) && node.name?.text === "performShutdown") {
      definesPerformShutdown = true;
    }
  }

  if (!importsIPC) {
    errors.push("main.ts AST error: must import IPC channels from shared");
  }
  if (!importsShutdown) {
    errors.push("main.ts AST error: must import shutdownRuntime from ./runtime-shutdown.js");
  }
  if (definesPerformShutdown) {
    errors.push("main.ts AST error: must not declare inline performShutdown; use shutdownRuntime from ./runtime-shutdown.js");
  }
}

// 4. Verify obsolete shortcut-dispatch file is completely removed
const shortcutDispatchPath = path.join(rootDir, "desktop/main/shortcut-dispatch.ts");
if (fs.existsSync(shortcutDispatchPath)) {
  errors.push("Obsolete shortcut-dispatch.ts must be deleted.");
}

// 5. Verify AppShell exports and Composer component extraction AST
const appShellPath = "desktop/renderer/src/app/AppShell.tsx";
const appShellText = readFile(appShellPath);
if (appShellText) {
  const sf = parseAST(appShellPath, appShellText);
  let importsComposer = false;
  let exportsComposer = false;

  for (const node of sf.statements) {
    if (ts.isImportDeclaration(node)) {
      const moduleSpec = node.moduleSpecifier.text;
      if (moduleSpec === "../components/Composer.js") {
        importsComposer = true;
      }
    }
    if (
      ts.isFunctionDeclaration(node)
      && node.name?.text === "Composer"
      && node.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)
    ) {
      exportsComposer = true;
    }
  }

  if (exportsComposer) {
    errors.push("AppShell.tsx AST error: must not export Composer component; extract it to components/Composer.tsx.");
  }
  if (!importsComposer) {
    errors.push("AppShell.tsx AST error: must import Composer from ../components/Composer.js");
  }
}

// 6. AST verification of EidosRuntimeAPI contract methods
const contractsPath = "desktop/shared/ipc-api.ts";
const contractsText = readFile(contractsPath);
if (contractsText) {
  const sf = parseAST(contractsPath, contractsText);
  const requiredMethods = new Set([
    "getStatus",
    "getHealth",
    "selectWorkspace",
    "listSessions",
    "readSession",
    "listEvents",
    "createSession",
    "renameSession",
    "deleteSession",
    "startRun",
    "continueRun",
    "cancelRun",
    "getModelStatus",
    "listModels",
    "configureModel",
    "listPendingApprovals",
    "respondApproval",
    "listPlugins",
    "importPlugin",
    "setPluginEnabled",
    "removePlugin",
    "listSkills",
    "listMcpServers",
    "setMcpEnabled",
    "readExtensions",
    "readExtensionEvents",
    "onStatus",
    "onNotification",
    "onApprovalRequest",
    "onShortcut",
  ]);

  let foundAPI = false;

  for (const node of sf.statements) {
    if (ts.isInterfaceDeclaration(node) && node.name.text === "EidosRuntimeAPI") {
      foundAPI = true;
      const declaredMembers = new Set();
      for (const member of node.members) {
        if (member.name && ts.isIdentifier(member.name)) {
          declaredMembers.add(member.name.text);
        }
      }

      for (const reqMethod of requiredMethods) {
        if (!declaredMembers.has(reqMethod)) {
          errors.push(`EidosRuntimeAPI AST error: missing contract method "${reqMethod}"`);
        }
      }
    }
  }

  if (!foundAPI) {
    errors.push("contracts.ts AST error: EidosRuntimeAPI interface definition not found");
  }
}

// 7. Check for raw string literals of known channels outside shared and test files via AST
const KNOWN_CHANNELS = new Set([
  "runtime:get-status",
  "runtime:health",
  "run:start",
  "run:continue",
  "approval:respond",
  "app:new-task",
  "app:open-workspace",
]);

const rendererSrcDir = path.join(rootDir, "desktop/renderer/src");

function scanFileASTForRawChannels(filePath) {
  const text = fs.readFileSync(filePath, "utf8");
  const sf = parseAST(filePath, text);

  function visit(node) {
    if (ts.isStringLiteral(node)) {
      if (KNOWN_CHANNELS.has(node.text)) {
        errors.push(
          `Raw channel string literal "${node.text}" found in production file ${path.relative(rootDir, filePath)}`,
        );
      }
    }
    ts.forEachChild(node, visit);
  }

  ts.forEachChild(sf, visit);
}

function scanDirForRawChannels(dir) {
  if (!fs.existsSync(dir)) return;
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules" && entry.name !== ".git") {
        scanDirForRawChannels(full);
      }
    } else if (entry.isFile() && (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx"))) {
      if (entry.name.includes(".test.") || entry.name.includes("/test/")) continue;
      scanFileASTForRawChannels(full);
    }
  }
}
scanDirForRawChannels(rendererSrcDir);

if (errors.length > 0) {
  console.error("❌ Contract Check Failures:");
  for (const err of errors) {
    console.error(`  - ${err}`);
  }
  process.exit(1);
} else {
  console.log("✓ All Desktop domain and IPC contract checks passed (AST verified).");
}

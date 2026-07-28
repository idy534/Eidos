import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = process.argv[2] === "--root" && process.argv[3]
  ? path.resolve(process.argv[3])
  : path.resolve(__dirname, "..");

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

function descendants(node, predicate) {
  const found = [];
  function visit(current) {
    if (predicate(current)) found.push(current);
    ts.forEachChild(current, visit);
  }
  visit(node);
  return found;
}

function namedFunction(sourceFile, name) {
  return descendants(
    sourceFile,
    (node) => ts.isFunctionDeclaration(node) && node.name?.text === name,
  )[0];
}

function objectPropertyNames(object) {
  return new Set(object.properties.flatMap((property) => (
    ts.isPropertyAssignment(property) && property.name
      ? [property.name.getText(object.getSourceFile()).replaceAll(/['"]/g, "")]
      : ts.isShorthandPropertyAssignment(property)
        ? [property.name.text]
        : []
  )));
}

function isWithin(node, ancestor) {
  for (let current = node; current; current = current.parent) {
    if (current === ancestor) return true;
  }
  return false;
}

function notificationMethods(condition, sourceFile) {
  if (
    ts.isBinaryExpression(condition)
    && condition.operatorToken.kind === ts.SyntaxKind.BarBarToken
  ) {
    const left = notificationMethods(condition.left, sourceFile);
    const right = notificationMethods(condition.right, sourceFile);
    return left && right ? new Set([...left, ...right]) : undefined;
  }
  if (
    ts.isBinaryExpression(condition)
    && condition.operatorToken.kind === ts.SyntaxKind.EqualsEqualsEqualsToken
    && condition.left.getText(sourceFile) === "notification.method"
    && ts.isStringLiteral(condition.right)
  ) {
    return new Set([condition.right.text]);
  }
  return undefined;
}

function isGuardedByNotificationMethods(call, expected, sourceFile) {
  for (let current = call.parent; current; current = current.parent) {
    if (!ts.isIfStatement(current) || !isWithin(call, current.thenStatement)) continue;
    const methods = notificationMethods(current.expression, sourceFile);
    if (
      methods
      && methods.size === expected.size
      && [...methods].every((method) => expected.has(method))
    ) {
      return true;
    }
  }
  return false;
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

  const approvalRequest = namedFunction(sf, "approvalRequestFrom");
  const provenanceValidator = namedFunction(sf, "isToolProvenance");
  const provenanceProjector = namedFunction(sf, "projectApprovalToolProvenance");
  const toolCallValidator = namedFunction(sf, "isToolCall");
  for (const [name, node] of [
    ["approvalRequestFrom", approvalRequest],
    ["isToolProvenance", provenanceValidator],
    ["projectApprovalToolProvenance", provenanceProjector],
    ["isToolCall", toolCallValidator],
  ]) {
    if (!node) errors.push(`runtime-client.ts AST error: missing ${name}`);
  }

  if (approvalRequest) {
    const expected = {
      file_change: ["id", "sessionId", "runId", "itemId", "toolCallId", "kind", "summary", "diff"],
      external_tool: ["id", "sessionId", "runId", "itemId", "toolCallId", "kind", "summary", "toolName", "arguments", "provenance", "permissionProfile", "timeoutSeconds", "envNames"],
      network_access: ["id", "sessionId", "runId", "itemId", "toolCallId", "kind", "summary", "toolName", "hosts", "target"],
      command_execution: ["id", "sessionId", "runId", "itemId", "toolCallId", "kind", "summary", "command", "cwd", "networkEnabled", "timeoutSeconds"],
    };
    const returns = descendants(
      approvalRequest,
      (node) => ts.isReturnStatement(node) && ts.isObjectLiteralExpression(node.expression),
    ).map((node) => node.expression);
    const seenKinds = new Map();
    for (const object of returns) {
      if (object.properties.some(ts.isSpreadAssignment)) {
        errors.push("approvalRequestFrom AST error: Approval return objects must not contain spreads");
      }
      const explicitProperties = object.properties.filter((property) => (
        ts.isPropertyAssignment(property) || ts.isShorthandPropertyAssignment(property)
      ));
      const properties = objectPropertyNames(object);
      const id = object.properties.find((property) => (
        ts.isPropertyAssignment(property) && property.name.getText(sf) === "id"
      ));
      if (!id || !ts.isPropertyAssignment(id) || id.initializer.getText(sf) !== "message.id") {
        errors.push("approvalRequestFrom AST error: every Approval id must be message.id");
      }
      const kind = object.properties.find((property) => (
        ts.isPropertyAssignment(property) && property.name.getText(sf) === "kind"
      ));
      const kindName = kind && ts.isPropertyAssignment(kind) && ts.isStringLiteral(kind.initializer)
        ? kind.initializer.text
        : undefined;
      const expectedFields = kindName ? expected[kindName] : undefined;
      if (expectedFields) {
        seenKinds.set(kindName, (seenKinds.get(kindName) ?? 0) + 1);
      }
      const hasStaticNames = explicitProperties.every((property) => (
        ts.isIdentifier(property.name) || ts.isStringLiteral(property.name)
      ));
      if (
        !expectedFields
        || explicitProperties.length !== object.properties.length
        || !hasStaticNames
        || properties.size !== expectedFields.length
        || explicitProperties.length !== expectedFields.length
        || expectedFields.some((field) => !properties.has(field))
      ) {
        errors.push(
          `approvalRequestFrom AST error: ${kindName ?? "unknown kind"} must use exact fields`,
        );
      }
    }
    for (const kindName of Object.keys(expected)) {
      if (seenKinds.get(kindName) !== 1) {
        errors.push(
          `approvalRequestFrom AST error: ${kindName} must have exactly one return branch`,
        );
      }
    }
  }
  if (provenanceValidator && !descendants(
    provenanceValidator,
    (node) => ts.isCallExpression(node) && node.expression.getText(sf) === "hasOnlyKeys",
  ).length) {
    errors.push("isToolProvenance AST error: must use exact-key validation");
  }
  if (provenanceProjector) {
    const returns = descendants(provenanceProjector, ts.isReturnStatement);
    if (!returns.some((node) => ts.isObjectLiteralExpression(node.expression))) {
      errors.push("projectApprovalToolProvenance AST error: must return a new object literal");
    }
    if (returns.some((node) => node.expression?.getText(sf) === "value")) {
      errors.push("projectApprovalToolProvenance AST error: must not return raw input");
    }
    if (descendants(
      provenanceProjector,
      (node) => ts.isSpreadAssignment(node) && node.expression.getText(sf) === "value",
    ).length) {
      errors.push("projectApprovalToolProvenance AST error: must not spread raw input");
    }
  }
  if (toolCallValidator && !descendants(
    toolCallValidator,
    (node) => ts.isCallExpression(node) && node.expression.getText(sf) === "isToolProvenance",
  ).length) {
    errors.push("isToolCall AST error: must use strict isToolProvenance");
  }
  if (approvalRequest && !descendants(
    approvalRequest,
    (node) => ts.isCallExpression(node) && node.expression.getText(sf) === "projectApprovalToolProvenance",
  ).length) {
    errors.push("approvalRequestFrom AST error: External Tool Approval must use provenance projector");
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
  if (descendants(
    sf,
    (node) => ts.isCallExpression(node) && node.expression.getText(sf) === "Promise.race",
  ).length) {
    errors.push("main.ts AST error: must not duplicate shutdown Promise.race");
  }
  const security = new Map();
  for (const property of descendants(sf, ts.isPropertyAssignment)) {
    const name = property.name.getText(sf);
    if (["contextIsolation", "nodeIntegration", "sandbox"].includes(name)) {
      security.set(name, property.initializer.kind === ts.SyntaxKind.TrueKeyword);
    }
  }
  if (security.get("contextIsolation") !== true
    || security.get("nodeIntegration") !== false
    || security.get("sandbox") !== true) {
    errors.push("main.ts AST error: Renderer security settings must remain isolated, sandboxed, and without Node integration");
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
  const clearCalls = descendants(
    sf,
    (node) => ts.isCallExpression(node) && node.expression.getText(sf).endsWith(".clearApprovalsForRun"),
  );
  if (clearCalls.length !== 1 || clearCalls[0].arguments[0]?.getText(sf) !== "run.id") {
    errors.push("AppShell.tsx AST error: only run/completed may clear Approvals by run.id");
  } else if (!isGuardedByNotificationMethods(
    clearCalls[0],
    new Set(["run/completed"]),
    sf,
  )) {
    errors.push("AppShell.tsx AST error: clearApprovalsForRun must be guarded by run/completed");
  }
  const removeCalls = descendants(
    sf,
    (node) => ts.isCallExpression(node) && node.expression.getText(sf).endsWith(".removeApproval"),
  );
  if (removeCalls.length !== 1 || removeCalls[0].arguments[0]?.getText(sf) !== "notification.params.approvalId") {
    errors.push("AppShell.tsx AST error: resolved/canceled Approval removal must use approvalId");
  } else if (!isGuardedByNotificationMethods(
    removeCalls[0],
    new Set(["approval/resolved", "approval/canceled"]),
    sf,
  )) {
    errors.push("AppShell.tsx AST error: removeApproval must be guarded by approval/resolved or approval/canceled");
  }
  const topError = descendants(
    sf,
    (node) => ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === "topError",
  )[0];
  if (!topError || topError.initializer?.getText(sf) !== "sessionState.error ?? runState.error") {
    errors.push("AppShell.tsx AST error: topError must only combine Session and Run errors");
  }
  const jsxAttributes = descendants(sf, ts.isJsxAttribute);
  const hasAttribute = (name, expression) => jsxAttributes.some((attribute) => (
    attribute.name.getText(sf) === name
    && ts.isJsxExpression(attribute.initializer)
    && attribute.initializer.expression?.getText(sf) === expression
  ));
  if (!hasAttribute("modelError", "modelState.error")) {
    errors.push("AppShell.tsx AST error: Model error must stay local to Settings");
  }
  if (!hasAttribute("extensionError", "extensionState.error")) {
    errors.push("AppShell.tsx AST error: Extension error must stay local to Settings");
  }
  if (jsxAttributes.filter((attribute) => (
    attribute.name.getText(sf) === "getFallbackFocus"
    && ts.isJsxExpression(attribute.initializer)
    && attribute.initializer.expression?.getText(sf) === "getDialogFallbackFocus"
  )).length < 2) {
    errors.push("AppShell.tsx AST error: both Dialogs must receive explicit fallback ownership");
  }
}

// 6. Dialog focus ownership and transition checks
for (const dialogPath of [
  "desktop/renderer/src/components/ApprovalFeedbackDialog.tsx",
  "desktop/renderer/src/components/settings/ConfirmDialog.tsx",
]) {
  const text = readFile(dialogPath);
  if (!text) continue;
  const sf = parseAST(dialogPath, text);
  if (descendants(
    sf,
    (node) => ts.isCallExpression(node) && node.expression.getText(sf) === "document.querySelector",
  ).length || descendants(
    sf,
    (node) => ts.isPropertyAccessExpression(node) && node.getText(sf) === "document.body",
  ).length) {
    errors.push(`${dialogPath} AST error: Dialog must not query or own a global fallback`);
  }
  if (!descendants(
    sf,
    (node) => ts.isPropertySignature(node) && node.name.getText(sf) === "getFallbackFocus",
  ).length) {
    errors.push(`${dialogPath} AST error: Dialog must receive fallback focus through props`);
  }
  if (!descendants(
    sf,
    (node) => ts.isCallExpression(node) && node.expression.getText(sf) === "useDialogFocusLifecycle",
  ).length) {
    errors.push(`${dialogPath} AST error: Dialog must use transition-aware focus lifecycle`);
  }
}
const focusLifecyclePath = "desktop/renderer/src/components/useDialogFocusLifecycle.ts";
const focusLifecycleText = readFile(focusLifecyclePath);
if (focusLifecycleText) {
  const sf = parseAST(focusLifecyclePath, focusLifecycleText);
  const hasPreviousOpen = descendants(
    sf,
    (node) => ts.isVariableDeclaration(node) && node.name.getText(sf) === "wasOpenRef",
  ).length > 0;
  const hasCloseTransition = descendants(
    sf,
    (node) => ts.isBinaryExpression(node)
      && node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken
      && node.left.getText(sf) === "wasOpen"
      && node.right.getText(sf) === "!open",
  ).length > 0;
  if (!hasPreviousOpen || !hasCloseTransition) {
    errors.push("useDialogFocusLifecycle.ts AST error: focus restoration must require a real open-to-close transition");
  }
}

// 7. Renderer pure-test discovery configuration
const rendererTestConfig = JSON.parse(readFile("tsconfig.renderer-test.json"));
for (const pattern of [
  "desktop/renderer/src/**/*.test.ts",
  "desktop/renderer/src/**/*.test.tsx",
]) {
  if (!rendererTestConfig.include?.includes(pattern)) {
    errors.push(`tsconfig.renderer-test.json error: missing include ${pattern}`);
  }
}
for (const pattern of [
  "desktop/renderer/src/**/*.behavior.test.ts",
  "desktop/renderer/src/**/*.behavior.test.tsx",
]) {
  if (!rendererTestConfig.exclude?.includes(pattern)) {
    errors.push(`tsconfig.renderer-test.json error: missing exclude ${pattern}`);
  }
}
if (rendererTestConfig.include?.some((pattern) => (
  /\.test\.tsx?$/.test(pattern) && !pattern.includes("**")
))) {
  errors.push("tsconfig.renderer-test.json error: pure tests must be glob-discovered, not individually listed");
}

// 8. AST verification of EidosRuntimeAPI contract methods
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

// 9. Check for raw string literals of known channels outside shared and test files via AST
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
  console.log("All Desktop structural, domain and IPC contract checks passed.");
}

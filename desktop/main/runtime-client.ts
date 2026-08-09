import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import readline from "node:readline";


const MAX_MESSAGE_BYTES = 1024 * 1024;
const RUNTIME_BUSINESS_CODES = new Set([
  "RUNTIME_NOT_INITIALIZED",
  "RUNTIME_DRAINING",
  "RUNTIME_RECONFIGURING",
  "RUNTIME_SHUTDOWN_TIMEOUT",
  "PROTOCOL_VERSION_UNSUPPORTED",
  "RUN_ALREADY_ACTIVE",
  "RESOURCE_NOT_FOUND",
  "INVALID_STATE",
  "APPROVAL_NO_LONGER_PENDING",
  "WORKSPACE_BOUNDARY_VIOLATION",
  "SANDBOX_UNAVAILABLE",
  "STORAGE_HEALTH_ONLY",
  "OPERATION_ID_REUSED",
  "OPERATION_IN_PROGRESS",
  "INTERNAL_ERROR",
  "SENSITIVE_CONTENT_REJECTED",
  "SENSITIVE_SCAN_FAILED",
  "INVALID_SESSION_TITLE",
  "SESSION_HAS_ACTIVE_RUN",
  "MODEL_NOT_AVAILABLE",
  "RUN_CANCEL_TIMEOUT",
  "RUN_RECONCILIATION_REQUIRED",
  "EXTENSIONS_UNAVAILABLE",
  "PLUGIN_IMPORT_REJECTED",
  "PLUGIN_IMPORT_FAILED",
  "PLUGIN_VERSION_CONFLICT",
  "PLUGIN_ID_CONFLICT",
  "SKILL_CATALOG_UNAVAILABLE",
  "SKILL_UNAVAILABLE",
  "MCP_SERVER_DISABLED",
]);

import type {
  ApprovalDecision,
  ApprovalRequest,
  FileApprovalRequest,
  CommandApprovalRequest,
  ExternalToolApprovalRequest,
  NetworkApprovalRequest,
  EventListResult,
  Item,
  McpListResult,
  McpServerRecord,
  ModelId,
  ModelListResult,
  ModelOption,
  ModelPresetsResult,
  ModelCreateInput,
  ModelUpdateInput,
  PluginListResult,
  PluginRecord,
  Run,
  ContextUsage,
  RuntimeEvent,
  RuntimeHealth,
  RuntimeNotification,
  Session,
  SessionListResult,
  SessionSnapshot,
  SkillListResult,
  SkillMetadata,
  ToolCall,
  ToolProvenance,
  ExtensionSnapshot as ExtensionSnapshotResult,
} from "../shared/index.js";

export interface InitializeResult {
  protocolVersion: number;
  runtimeVersion: string;
  capabilities: {
    runShell: boolean;
    modelConfigured: boolean;
  };
}

export type {
  ApprovalDecision,
  ApprovalRequest,
  FileApprovalRequest,
  CommandApprovalRequest,
  ExternalToolApprovalRequest,
  NetworkApprovalRequest,
  EventListResult,
  Item,
  McpListResult,
  McpServerRecord,
  ModelId,
  ModelListResult,
  ModelOption,
  ModelPresetsResult,
  ModelCreateInput,
  ModelUpdateInput,
  PluginListResult,
  PluginRecord,
  Run,
  ContextUsage,
  RuntimeEvent,
  RuntimeHealth,
  RuntimeNotification,
  Session,
  SessionListResult,
  SessionSnapshot,
  SkillListResult,
  SkillMetadata,
  ToolCall,
  ToolProvenance,
  ExtensionSnapshotResult,
};

interface RuntimeClientOptions {
  pythonExecutable: string;
  runtimeRoot: string;
  dataDirectory?: string;
  environment?: Record<string, string>;
  onNotification?: (notification: RuntimeNotification) => void;
  onApprovalRequest?: (request: ApprovalRequest) => Promise<ApprovalDecision>;
  onStderr?: (line: string) => void;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timeout: ReturnType<typeof setTimeout>;
}

interface RpcError {
  code: number;
  message: string;
  data?: {
    code?: string;
    retryable?: boolean;
  };
}

export class RuntimeRequestError extends Error {
  readonly rpcCode: number;
  readonly businessCode: string | undefined;

  constructor(error: RpcError) {
    const businessCode = RUNTIME_BUSINESS_CODES.has(error.data?.code ?? "")
      ? error.data?.code
      : "INTERNAL_ERROR";
    super(`EIDOS_RUNTIME_ERROR:${businessCode}`);
    this.name = "RuntimeRequestError";
    this.rpcCode = error.code;
    this.businessCode = businessCode;
  }
}

export class RuntimeClient {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly pending = new Map<string, PendingRequest>();
  private readonly exitPromise: Promise<number>;
  private readonly onNotification: ((notification: RuntimeNotification) => void) | undefined;
  private readonly onApprovalRequest: ((request: ApprovalRequest) => Promise<ApprovalDecision>) | undefined;
  private stdoutBuffer = Buffer.alloc(0);
  private nextRequestId = 1;
  private closed = false;

  constructor(options: RuntimeClientOptions) {
    const pythonPath = [options.runtimeRoot, process.env.PYTHONPATH]
      .filter((entry): entry is string => Boolean(entry))
      .join(path.delimiter);
    const environment: NodeJS.ProcessEnv = {
      ...process.env,
      ...options.environment,
      PYTHONPATH: pythonPath,
    };
    if (options.dataDirectory) {
      environment.EIDOS_DATA_DIR = options.dataDirectory;
    }

    this.child = spawn(options.pythonExecutable, ["-u", "-m", "eidos_runtime"], {
      cwd: options.runtimeRoot,
      env: environment,
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.child.stdout.on("data", (chunk: Buffer) => this.handleStdoutChunk(chunk));

    const stderr = readline.createInterface({ input: this.child.stderr });
    stderr.on("line", (line) => options.onStderr?.(line));
    this.onNotification = options.onNotification;
    this.onApprovalRequest = options.onApprovalRequest;

    this.exitPromise = new Promise((resolve) => {
      this.child.once("error", (error) => this.failAll(error));
      this.child.once("close", (code) => {
        this.closed = true;
        this.failAll(new Error("Runtime process exited"));
        resolve(code ?? 1);
      });
    });
  }

  initialize(): Promise<InitializeResult> {
    return this.validatedRequest(
      "initialize",
      {
        client: { name: "eidos-desktop", version: "0.3.0" },
        protocolVersion: 1,
      },
      isInitializeResult,
    );
  }

  async createSession(workspaceRoot: string, operationId = randomUUID()): Promise<Session> {
    return this.validatedRequest(
      "session/create", { workspaceRoot, operationId }, isSession,
    );
  }

  listSessions(
    options: { limit?: number; cursor?: string } = {},
  ): Promise<SessionListResult> {
    return this.validatedRequest("session/list", options, isSessionListResult);
  }

  readSession(
    sessionId: string,
    options: { itemLimit?: number; beforeItemId?: string } = {},
  ): Promise<SessionSnapshot> {
    return this.validatedRequest(
      "session/read",
      { sessionId, ...options },
      isSessionSnapshot,
    );
  }

  renameSession(
    sessionId: string, title: string, operationId = randomUUID(),
  ): Promise<Session> {
    return this.validatedRequest(
      "session/rename", { sessionId, title, operationId }, isSession,
    );
  }

  deleteSession(
    sessionId: string, operationId = randomUUID(),
  ): Promise<{ deletedSessionId: string }> {
    return this.validatedRequest(
      "session/delete", { sessionId, operationId }, isDeletedSessionResult,
    );
  }

  listEvents(sessionId: string, afterEventId: number, limit = 200): Promise<EventListResult> {
    return this.validatedRequest(
      "event/list", { sessionId, afterEventId, limit }, isEventListResult,
    );
  }

  health(): Promise<RuntimeHealth> {
    return this.validatedRequest("runtime/health", {}, isRuntimeHealth);
  }

  startRun(
    sessionId: string,
    userInput: string,
    modelId: ModelId,
    operationId = randomUUID(),
  ): Promise<Run> {
    return this.validatedRequest(
      "run/start", { sessionId, userInput, modelId, operationId }, isRun,
    );
  }

  cancelRun(runId: string, operationId = randomUUID()): Promise<Run> {
    return this.validatedRequest("run/cancel", { runId, operationId }, isRun);
  }

  async readContextUsage(runId: string): Promise<ContextUsage | null> {
    const result = await this.validatedRequest(
      "context/usage",
      { runId },
      isContextUsageResult,
    );
    const usage = result.contextUsage;
    return usage
      ? {
          activeTokens: usage.activeTokens,
          windowTokens: usage.contextWindowTokens,
          percentUsed: usage.percentUsed,
          source: usage.source,
          ...(usage.updatedAt !== undefined ? { updatedAt: usage.updatedAt } : {}),
        }
      : null;
  }

  listModelPresets(): Promise<ModelPresetsResult> {
    return this.validatedRequest("model/presets", {}, isModelPresetsResult);
  }

  listModels(): Promise<ModelListResult> {
    return this.validatedRequest("model/list", {}, isModelListResult);
  }

  createModel(input: ModelCreateInput): Promise<ModelOption> {
    return this.validatedRequest("model/create", { ...input }, isModelOption);
  }

  updateModel(input: ModelUpdateInput): Promise<ModelOption> {
    return this.validatedRequest("model/update", { ...input }, isModelOption);
  }

  deleteModel(id: ModelId): Promise<void> {
    return this.validatedRequest(
      "model/delete",
      { id },
      (value): value is { deletedModelId: string } => (
        isRecord(value)
        && hasOnlyKeys(value, ["deletedModelId"])
        && value.deletedModelId === id
      ),
    ).then(() => undefined);
  }

  listPlugins(): Promise<{ plugins: PluginRecord[] }> {
    return this.validatedRequest("plugin/list", {}, isPluginListResult);
  }

  importPlugin(sourcePath: string, operationId = randomUUID()): Promise<PluginRecord> {
    return this.validatedRequest(
      "plugin/import", { sourcePath, operationId }, isPluginRecord,
    );
  }

  setPluginEnabled(pluginId: string, enabled: boolean, operationId = randomUUID()): Promise<PluginRecord> {
    return this.validatedRequest(
      "plugin/setEnabled", { pluginId, enabled, operationId }, isPluginRecord,
    );
  }

  removePlugin(pluginId: string, operationId = randomUUID()): Promise<PluginRecord> {
    return this.validatedRequest(
      "plugin/remove", { pluginId, operationId }, isPluginRecord,
    );
  }

  listSkills(): Promise<{ skills: SkillMetadata[] }> {
    return this.validatedRequest("skill/list", {}, isSkillListResult);
  }

  listMcpServers(): Promise<{ servers: McpServerRecord[] }> {
    return this.validatedRequest("mcp/list", {}, isMcpServerListResult);
  }

  setMcpEnabled(
    pluginId: string, serverId: string, enabled: boolean,
    operationId = randomUUID(),
  ): Promise<McpServerRecord> {
    return this.validatedRequest(
      "mcp/setEnabled", { pluginId, serverId, enabled, consent: true, operationId },
      isMcpServerRecord,
    );
  }

  readExtensions(): Promise<ExtensionSnapshotResult> {
    return this.validatedRequest("extension/read", {}, isExtensionSnapshotResult);
  }

  readExtensionEvents(afterEventId: number, limit = 200): Promise<EventListResult> {
    return this.validatedRequest(
      "extension/readEvents", { afterEventId, limit }, isEventListResult,
    );
  }

  async shutdown(): Promise<void> {
    if (this.closed) {
      return;
    }
    await this.request("runtime/shutdown", {});
  }

  waitForExit(): Promise<number> {
    return this.exitPromise;
  }

  terminate(): void {
    if (!this.closed) {
      this.child.kill();
    }
  }

  private request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    if (this.closed || this.child.stdin.destroyed) {
      return Promise.reject(new Error("Runtime process is not available"));
    }

    const id = `client-${this.nextRequestId++}`;
    const serialized = JSON.stringify({ jsonrpc: "2.0", id, method, params });
    if (Buffer.byteLength(serialized, "utf8") > MAX_MESSAGE_BYTES) {
      return Promise.reject(new Error("Runtime request exceeds 1 MiB"));
    }

    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Runtime request timed out: ${method}`));
      }, 30_000);
      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
        timeout,
      });
      this.child.stdin.write(`${serialized}\n`, "utf8", (error) => {
        if (!error) {
          return;
        }
        const pending = this.pending.get(id);
        if (pending) {
          clearTimeout(pending.timeout);
          this.pending.delete(id);
        }
        reject(error);
      });
    });
  }

  private async validatedRequest<T>(
    method: string,
    params: Record<string, unknown>,
    validate: (value: unknown) => value is T,
  ): Promise<T> {
    const result = await this.request<unknown>(method, params);
    if (!validate(result)) {
      this.failProtocol(`Runtime returned an invalid result for ${method}`);
      throw new Error(`Runtime returned an invalid result for ${method}`);
    }
    return result;
  }

  private handleStdoutChunk(chunk: Buffer): void {
    if (this.closed) {
      return;
    }
    this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk]);
    let newline = this.stdoutBuffer.indexOf(0x0a);
    while (newline >= 0) {
      if (newline > MAX_MESSAGE_BYTES) {
        this.failProtocol("Runtime response exceeds 1 MiB");
        return;
      }
      let line = this.stdoutBuffer.subarray(0, newline);
      this.stdoutBuffer = this.stdoutBuffer.subarray(newline + 1);
      if (line.length > 0 && line[line.length - 1] === 0x0d) {
        line = line.subarray(0, line.length - 1);
      }
      this.handleLine(line.toString("utf8"));
      if (this.closed) {
        return;
      }
      newline = this.stdoutBuffer.indexOf(0x0a);
    }
    if (this.stdoutBuffer.length > MAX_MESSAGE_BYTES) {
      this.failProtocol("Runtime response exceeds 1 MiB");
    }
  }

  private handleLine(line: string): void {
    if (Buffer.byteLength(line, "utf8") > MAX_MESSAGE_BYTES) {
      this.failProtocol("Runtime response exceeds 1 MiB");
      return;
    }

    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      this.failProtocol("Runtime wrote invalid JSON to stdout");
      return;
    }

    if (isNotification(message)) {
      this.onNotification?.(message);
      return;
    }
    if (isServerRequest(message)) {
      const request = approvalRequestFrom(message);
      if (!request || !this.onApprovalRequest) {
        this.writeProtocolMessage({
          jsonrpc: "2.0",
          id: message.id,
          error: { code: -32601, message: "Method not found" },
        });
        return;
      }
      void this.onApprovalRequest(request).then((decision) => {
        this.writeProtocolMessage({ jsonrpc: "2.0", id: message.id, result: decision });
      }).catch(() => {
        this.writeProtocolMessage({
          jsonrpc: "2.0",
          id: message.id,
          error: { code: -32000, message: "Approval failed" },
        });
      });
      return;
    }
    if (!isResponse(message)) {
      this.failProtocol("Runtime wrote an invalid JSON-RPC message");
      return;
    }

    const pending = this.pending.get(message.id);
    if (!pending) {
      this.failProtocol("Runtime returned an unknown response id");
      return;
    }
    this.pending.delete(message.id);
    clearTimeout(pending.timeout);

    if ("error" in message) {
      pending.reject(new RuntimeRequestError(message.error));
      return;
    }
    pending.resolve(message.result);
  }

  private writeProtocolMessage(message: Record<string, unknown>): void {
    if (this.closed || this.child.stdin.destroyed) {
      return;
    }
    const serialized = JSON.stringify(message);
    if (Buffer.byteLength(serialized, "utf8") > MAX_MESSAGE_BYTES) {
      this.failProtocol("Runtime protocol response exceeds 1 MiB");
      return;
    }
    this.child.stdin.write(`${serialized}\n`, "utf8");
  }

  private failProtocol(message: string): void {
    this.closed = true;
    this.failAll(new Error(message));
    this.child.kill();
  }

  private failAll(error: Error): void {
    for (const request of this.pending.values()) {
      clearTimeout(request.timeout);
      request.reject(error);
    }
    this.pending.clear();
  }
}

function isResponse(
  value: unknown,
): value is
  | { jsonrpc: "2.0"; id: string; result: unknown }
  | { jsonrpc: "2.0"; id: string; error: RpcError } {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  if (candidate.jsonrpc !== "2.0" || typeof candidate.id !== "string") {
    return false;
  }
  if (("result" in candidate) === ("error" in candidate)) {
    return false;
  }
  if ("result" in candidate) {
    return true;
  }
  const error = candidate.error;
  return (
    Boolean(error)
    && typeof error === "object"
    && typeof (error as Record<string, unknown>).code === "number"
    && typeof (error as Record<string, unknown>).message === "string"
  );
}

function isServerRequest(
  value: unknown,
): value is { jsonrpc: "2.0"; id: string; method: string; params: unknown } {
  if (!isRecord(value)) {
    return false;
  }
  return (
    value.jsonrpc === "2.0"
    && typeof value.id === "string"
    && value.id.startsWith("server-")
    && typeof value.method === "string"
    && "params" in value
    && hasOnlyKeys(value, ["jsonrpc", "id", "method", "params"])
  );
}

function isNotification(value: unknown): value is RuntimeNotification {
  if (
    !isRecord(value)
    || value.jsonrpc !== "2.0"
    || "id" in value
    || typeof value.method !== "string"
    || !isRecord(value.params)
    || !hasOnlyKeys(value, ["jsonrpc", "method", "params"])
  ) {
    return false;
  }
  const params = value.params;
  if (value.method === "session/titleUpdated") {
    return (
      hasOnlyKeys(params, ["sessionId", "title"])
      && typeof params.sessionId === "string"
      && typeof params.title === "string"
      && params.title.length > 0
    );
  }
  if (
    value.method === "run/started"
    || value.method === "run/updated"
    || value.method === "run/completed"
  ) {
    const run = params.run;
    const valid = (
      hasOnlyKeys(params, ["sessionId", "run"])
      && typeof params.sessionId === "string"
      && isRun(run)
      && params.sessionId === run.sessionId
    );
    if (!valid || !isRun(run)) {
      return false;
    }
    if (value.method === "run/started") {
      return run.status === "running";
    }
    if (value.method === "run/updated") {
      return ["queued", "running", "waiting_approval", "finalizing"].includes(run.status);
    }
    return ![
      "queued", "running", "waiting_approval", "finalizing",
    ].includes(run.status);
  }
  if (value.method === "item/started" || value.method === "item/completed") {
    const item = params.item;
    const valid = (
      hasOnlyKeys(params, ["sessionId", "runId", "item"])
      && typeof params.sessionId === "string"
      && typeof params.runId === "string"
      && isItem(item)
      && params.sessionId === item.sessionId
      && params.runId === item.runId
    );
    if (!valid || !isItem(item)) {
      return false;
    }
    return value.method === "item/started"
      ? item.status === "in_progress" && item.completedAt === undefined
      : item.status !== "in_progress" && item.completedAt !== undefined;
  }
  if (value.method === "item/delta") {
    return (
      hasOnlyKeys(params, ["sessionId", "runId", "itemId", "sequence", "delta"])
      && typeof params.sessionId === "string"
      && typeof params.runId === "string"
      && typeof params.itemId === "string"
      && isPositiveInteger(params.sequence)
      && typeof params.delta === "string"
    );
  }
  if (
    value.method === "approval/requested"
    || value.method === "approval/resolved"
    || value.method === "approval/canceled"
  ) {
    const status = String(params.status);
    return (
      hasOnlyKeys(params, ["sessionId", "runId", "approvalId", "status"])
      && typeof params.sessionId === "string"
      && typeof params.runId === "string"
      && typeof params.approvalId === "string"
      && (
        (value.method === "approval/requested" && status === "pending")
        || (
          value.method === "approval/resolved"
          && ["approved", "rejected"].includes(status)
        )
        || (
          value.method === "approval/canceled"
          && ["canceled", "invalidated"].includes(status)
        )
      )
    );
  }
  return false;
}

function isInitializeResult(value: unknown): value is InitializeResult {
  if (!isRecord(value) || !hasOnlyKeys(value, ["protocolVersion", "runtimeVersion", "capabilities"])) {
    return false;
  }
  return (
    isNonNegativeInteger(value.protocolVersion)
    && typeof value.runtimeVersion === "string"
    && isRecord(value.capabilities)
    && hasOnlyKeys(value.capabilities, ["runShell", "modelConfigured"])
    && typeof value.capabilities.runShell === "boolean"
    && typeof value.capabilities.modelConfigured === "boolean"
  );
}

function isModelOption(value: unknown): value is ModelOption {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "id", "name", "vendor", "provider", "url", "supportsToolCall",
      "supportsImages", "supportsReasoning", "reasoning",
    ])
    && isModelId(value.id)
    && typeof value.name === "string"
    && typeof value.vendor === "string"
    && typeof value.provider === "string"
    && typeof value.url === "string"
    && typeof value.supportsToolCall === "boolean"
    && typeof value.supportsImages === "boolean"
    && typeof value.supportsReasoning === "boolean"
    && (value.reasoning === null || isModelReasoning(value.reasoning))
  );
}

function isModelReasoning(value: unknown): boolean {
  return isRecord(value)
    && hasOnlyKeys(value, ["defaultEffort", "supportedEfforts"])
    && ["high", "max"].includes(String(value.defaultEffort))
    && Array.isArray(value.supportedEfforts)
    && value.supportedEfforts.every((effort) => ["high", "max"].includes(String(effort)));
}

function isModelId(value: unknown): value is ModelId {
  return typeof value === "string" && value.length > 0 && value.length <= 256;
}

function isModelListResult(value: unknown): value is ModelListResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["models", "defaultModelId"])
    && (
      value.defaultModelId === undefined
      || value.defaultModelId === null
      || isModelId(value.defaultModelId)
    )
    && Array.isArray(value.models)
    && value.models.every(isModelOption)
  );
}

function isModelPresetsResult(value: unknown): value is ModelPresetsResult {
  return isRecord(value)
    && hasOnlyKeys(value, ["providers"])
    && Array.isArray(value.providers)
    && value.providers.every((provider) => (
      isRecord(provider)
      && hasOnlyKeys(provider, ["id", "name", "models"])
      && ["deepseek", "minimax", "kimi"].includes(String(provider.id))
      && typeof provider.name === "string"
      && Array.isArray(provider.models)
      && provider.models.every((model) => (
        isRecord(model)
        && isModelOption({ ...model, vendor: provider.name, provider: provider.id })
      ))
    ));
}

function isRuntimeHealth(value: unknown): value is RuntimeHealth {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["state", "code"])
    && ["ready", "health_only"].includes(String(value.state))
    && (value.code === undefined || typeof value.code === "string")
  );
}

function isSession(value: unknown): value is Session {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["id", "workspaceRoot", "title", "taskStatus", "createdAt", "updatedAt"])
    && typeof value.id === "string"
    && typeof value.workspaceRoot === "string"
    && (value.title === undefined || typeof value.title === "string")
    && ["new", "in_progress", "completed", "failed", "canceled"].includes(String(value.taskStatus))
    && isNonNegativeInteger(value.createdAt)
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isSessionListResult(value: unknown): value is SessionListResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["items", "nextCursor"])
    && Array.isArray(value.items)
    && value.items.every(isSession)
    && (value.nextCursor === undefined || typeof value.nextCursor === "string")
  );
}

function isSessionSnapshot(value: unknown): value is SessionSnapshot {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "session", "runs", "items", "stepResolutions",
      "previousItemId", "throughEventId",
    ])
    && isSession(value.session)
    && Array.isArray(value.runs)
    && value.runs.every(isRun)
    && Array.isArray(value.items)
    && value.items.every(isItem)
    && Array.isArray(value.stepResolutions)
    && value.stepResolutions.every(isStepResolutionReview)
    && (value.previousItemId === undefined || typeof value.previousItemId === "string")
    && (value.throughEventId === undefined || isNonNegativeInteger(value.throughEventId))
  );
}

function isStepResolutionReview(value: unknown): boolean {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "id", "stepId", "runId", "stepOrdinal", "snapshotHash", "requestHash",
      "ruleSnapshotId", "ruleSnapshotHash", "rules", "shadowed", "warnings",
    ])
    && typeof value.id === "string"
    && typeof value.stepId === "string"
    && typeof value.runId === "string"
    && isPositiveInteger(value.stepOrdinal)
    && isSha256(value.snapshotHash)
    && isSha256(value.requestHash)
    && typeof value.ruleSnapshotId === "string"
    && isSha256(value.ruleSnapshotHash)
    && Array.isArray(value.rules)
    && value.rules.every((rule) => (
      isRecord(rule)
      && hasOnlyKeys(rule, [
        "absolutePath", "relativePath", "filename", "contentHash", "byteCount",
        "includedByteCount", "directoryLevel", "selectionReason", "truncated",
      ])
      && typeof rule.absolutePath === "string"
      && typeof rule.relativePath === "string"
      && typeof rule.filename === "string"
      && isSha256(rule.contentHash)
      && isNonNegativeInteger(rule.byteCount)
      && isNonNegativeInteger(rule.includedByteCount)
      && isNonNegativeInteger(rule.directoryLevel)
      && [
        "eidos_override", "eidos_native", "compatibility_fallback",
      ].includes(String(rule.selectionReason))
      && typeof rule.truncated === "boolean"
    ))
    && Array.isArray(value.shadowed)
    && value.shadowed.every((candidate) => (
      isRecord(candidate)
      && hasOnlyKeys(candidate, [
        "absolutePath", "relativePath", "filename", "directoryLevel", "reason",
      ])
      && typeof candidate.absolutePath === "string"
      && typeof candidate.relativePath === "string"
      && typeof candidate.filename === "string"
      && isNonNegativeInteger(candidate.directoryLevel)
      && candidate.reason === "higher_precedence_candidate_selected"
    ))
    && Array.isArray(value.warnings)
    && value.warnings.every((warning) => (
      isRecord(warning)
      && hasOnlyKeys(warning, ["code", "path", "message"])
      && [
        "RULE_BUDGET_TRUNCATED", "RULE_READ_ERROR",
        "RULE_PATH_OUTSIDE_WORKSPACE",
      ].includes(String(warning.code))
      && typeof warning.path === "string"
      && typeof warning.message === "string"
    ))
  );
}

function isEventListResult(value: unknown): value is EventListResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["items", "hasMore", "throughEventId"])
    && Array.isArray(value.items)
    && value.items.every((event) => (
      isRecord(event)
      && hasOnlyKeys(event, [
        "eventContractVersion", "eventId", "eventType", "occurredAt",
        "sessionId", "runId", "payload",
      ])
      && event.eventContractVersion === 1
      && isPositiveInteger(event.eventId)
      && typeof event.eventType === "string"
      && isNonNegativeInteger(event.occurredAt)
      && (event.sessionId === undefined || typeof event.sessionId === "string")
      && (event.runId === undefined || typeof event.runId === "string")
      && isRecord(event.payload)
    ))
    && typeof value.hasMore === "boolean"
    && isNonNegativeInteger(value.throughEventId)
  );
}

function isRun(value: unknown): value is Run {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "id",
      "sessionId",
      "userInput",
      "status",
      "runtimeState",
      "modelId",
      "modelStepCount",
      "allowedActions",
      "createdAt",
      "startedAt",
      "updatedAt",
      "completedAt",
      "errorCode",
      "cancelRequestedAt",
      "cancelCompletedAt",
      "cancelFailureCode",
      "stopReason",
      "sideEffectsMayExist",
      "extensionSnapshot",
      "activatedTools",
    ])
  ) {
    return false;
  }
  return (
    typeof value.id === "string"
    && typeof value.sessionId === "string"
    && (value.userInput === undefined || typeof value.userInput === "string")
    && [
      "queued", "running", "waiting_approval",
      "finalizing", "stopped", "succeeded", "failed", "canceled", "interrupted",
    ].includes(String(value.status))
    && (
      value.runtimeState === undefined
      || [
        "queued", "thinking", "tool_executing",
        "waiting_approval", "finalizing", "terminal",
      ].includes(String(value.runtimeState))
    )
    && isModelId(value.modelId)
    && isNonNegativeInteger(value.modelStepCount)
    && (value.allowedActions === undefined || (
      Array.isArray(value.allowedActions)
      && value.allowedActions.every((action) => [
        "cancel", "approve", "reject",
      ].includes(String(action)))
    ))
    && isNonNegativeInteger(value.createdAt)
    && (value.startedAt === undefined || isNonNegativeInteger(value.startedAt))
    && isNonNegativeInteger(value.updatedAt)
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
    && (value.errorCode === undefined || typeof value.errorCode === "string")
    && (
      value.cancelRequestedAt === undefined
      || isNonNegativeInteger(value.cancelRequestedAt)
    )
    && (
      value.cancelCompletedAt === undefined
      || isNonNegativeInteger(value.cancelCompletedAt)
    )
    && (
      value.cancelFailureCode === undefined
      || typeof value.cancelFailureCode === "string"
    )
    && (value.stopReason === undefined || typeof value.stopReason === "string")
    && (value.sideEffectsMayExist === undefined || typeof value.sideEffectsMayExist === "boolean")
    && (value.extensionSnapshot === undefined || isExtensionSnapshot(value.extensionSnapshot))
    && (value.activatedTools === undefined || (
      Array.isArray(value.activatedTools)
      && value.activatedTools.every((name) => typeof name === "string")
    ))
  );
}

type RuntimeContextUsage = Omit<ContextUsage, "windowTokens"> & {
  contextWindowTokens: number;
};

function isContextUsage(value: unknown): value is RuntimeContextUsage {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "activeTokens", "contextWindowTokens", "percentUsed", "source", "updatedAt",
    ])
    && isNonNegativeInteger(value.activeTokens)
    && isPositiveInteger(value.contextWindowTokens)
    && typeof value.percentUsed === "number"
    && Number.isFinite(value.percentUsed)
    && value.percentUsed >= 0
    && value.percentUsed <= 100
    && ["provider", "estimated"].includes(String(value.source))
    && (value.updatedAt === undefined || isNonNegativeInteger(value.updatedAt))
  );
}

function isContextUsageResult(
  value: unknown,
): value is { contextUsage?: RuntimeContextUsage } {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["contextUsage"])
    && (value.contextUsage === undefined || isContextUsage(value.contextUsage))
  );
}

function isDeletedSessionResult(value: unknown): value is { deletedSessionId: string } {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["deletedSessionId"])
    && typeof value.deletedSessionId === "string"
  );
}

function isItem(value: unknown): value is Item {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "id",
      "sessionId",
      "runId",
      "ordinal",
      "modelStepIndex",
      "kind",
      "status",
      "content",
      "incomplete",
      "createdAt",
      "completedAt",
      "toolCall",
    ])
  ) {
    return false;
  }
  const valid = (
    typeof value.id === "string"
    && typeof value.sessionId === "string"
    && typeof value.runId === "string"
    && isNonNegativeInteger(value.ordinal)
    && ["user_message", "assistant_message", "file_change", "command_execution", "tool_call"].includes(String(value.kind))
    && ["in_progress", "completed", "failed", "declined", "canceled"].includes(String(value.status))
    && isNonNegativeInteger(value.createdAt)
    && (value.modelStepIndex === undefined || isNonNegativeInteger(value.modelStepIndex))
    && (value.content === undefined || typeof value.content === "string")
    && (value.incomplete === undefined || typeof value.incomplete === "boolean")
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
  );
  if (!valid) {
    return false;
  }
  return ["tool_call", "file_change", "command_execution"].includes(String(value.kind))
    ? isToolCall(value.toolCall)
    : value.toolCall === undefined;
}

function isToolCall(value: unknown): value is ToolCall {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "id",
      "itemId",
      "modelStepIndex",
      "batchOrder",
      "providerCallId",
      "toolName",
      "status",
      "argumentsJson",
      "resultJson",
      "startedAt",
      "completedAt",
      "approvalStatus",
      "approvalDecision",
      "approvalFeedback",
      "approvalDiff",
      "baseSha256",
      "provenance",
      "toolSetHash",
    ])
  ) {
    return false;
  }
  return (
    typeof value.id === "string"
    && typeof value.itemId === "string"
    && isNonNegativeInteger(value.modelStepIndex)
    && isNonNegativeInteger(value.batchOrder)
    && typeof value.providerCallId === "string"
    && typeof value.toolName === "string"
    && ["running", "completed", "failed", "canceled"].includes(String(value.status))
    && (value.argumentsJson === undefined || typeof value.argumentsJson === "string")
    && (value.resultJson === undefined || typeof value.resultJson === "string")
    && isNonNegativeInteger(value.startedAt)
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
    && (value.approvalStatus === undefined || ["pending", "resolved", "canceled"].includes(String(value.approvalStatus)))
    && (value.approvalDecision === undefined || ["approve", "reject"].includes(String(value.approvalDecision)))
    && (value.approvalFeedback === undefined || typeof value.approvalFeedback === "string")
    && (value.approvalDiff === undefined || typeof value.approvalDiff === "string")
    && (value.baseSha256 === undefined || typeof value.baseSha256 === "string")
    && (value.provenance === undefined || isToolProvenance(value.provenance))
    && (value.toolSetHash === undefined || typeof value.toolSetHash === "string")
  );
}

function isToolProvenance(value: unknown): value is ToolProvenance {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "kind", "sourceId", "sourceVersion", "contentHash",
      "pluginId", "serverId", "skillId",
    ])
    && ["builtin", "skill", "mcp"].includes(String(value.kind))
    && typeof value.sourceId === "string"
    && typeof value.sourceVersion === "string"
    && typeof value.contentHash === "string"
    && (value.pluginId === undefined || typeof value.pluginId === "string")
    && (value.serverId === undefined || typeof value.serverId === "string")
    && (value.skillId === undefined || typeof value.skillId === "string")
  );
}

function projectApprovalToolProvenance(
  value: unknown,
): ToolProvenance | undefined {
  if (
    !isRecord(value)
    || !["builtin", "skill", "mcp"].includes(String(value.kind))
    || typeof value.sourceId !== "string"
    || typeof value.sourceVersion !== "string"
    || typeof value.contentHash !== "string"
    || (value.pluginId !== undefined && typeof value.pluginId !== "string")
    || (value.serverId !== undefined && typeof value.serverId !== "string")
    || (value.skillId !== undefined && typeof value.skillId !== "string")
  ) {
    return undefined;
  }
  return {
    kind: value.kind as "builtin" | "skill" | "mcp",
    sourceId: value.sourceId,
    sourceVersion: value.sourceVersion,
    contentHash: value.contentHash,
    ...(value.pluginId !== undefined ? { pluginId: value.pluginId } : {}),
    ...(value.serverId !== undefined ? { serverId: value.serverId } : {}),
    ...(value.skillId !== undefined ? { skillId: value.skillId } : {}),
  };
}

function isExtensionSnapshot(value: unknown): value is Record<string, unknown> {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "schemaVersion", "extensionContractVersion", "plugins",
      "skillCatalogHash", "mcpConfigHash",
    ])
    && value.schemaVersion === 1
    && value.extensionContractVersion === 1
    && typeof value.skillCatalogHash === "string"
    && typeof value.mcpConfigHash === "string"
    && Array.isArray(value.plugins)
    && value.plugins.every((plugin) => (
      isRecord(plugin)
      && hasOnlyKeys(plugin, ["id", "version", "contentHash"])
      && typeof plugin.id === "string"
      && typeof plugin.version === "string"
      && typeof plugin.contentHash === "string"
    ))
  );
}

function isPluginRecord(value: unknown): value is PluginRecord {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "schemaVersion", "id", "name", "version", "description", "contentHash",
      "enabled", "status", "installedAt", "updatedAt",
    ])
    && value.schemaVersion === 1
    && ["id", "name", "version", "description", "contentHash"].every(
      (key) => typeof value[key] === "string",
    )
    && typeof value.enabled === "boolean"
    && ["installed", "removed"].includes(String(value.status))
    && isNonNegativeInteger(value.installedAt)
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isPluginListResult(value: unknown): value is { plugins: PluginRecord[] } {
  return isRecord(value) && hasOnlyKeys(value, ["plugins"])
    && Array.isArray(value.plugins) && value.plugins.every(isPluginRecord);
}

function isSkillMetadata(value: unknown): value is SkillMetadata {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "schemaVersion", "qualifiedId", "name", "description", "pluginId",
      "pluginVersion", "pluginHash", "contentHash",
    ])
    && value.schemaVersion === 1
    && [
      "qualifiedId", "name", "description", "pluginId",
      "pluginVersion", "pluginHash", "contentHash",
    ].every((key) => typeof value[key] === "string")
  );
}

function isSkillListResult(value: unknown): value is { skills: SkillMetadata[] } {
  return isRecord(value) && hasOnlyKeys(value, ["skills"])
    && Array.isArray(value.skills) && value.skills.every(isSkillMetadata);
}

function isMcpServerRecord(value: unknown): value is McpServerRecord {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "schemaVersion", "pluginId", "pluginVersion", "pluginHash", "serverId",
      "executable", "argv", "envNames", "permissionProfile",
      "startupTimeoutSeconds", "toolTimeoutSeconds", "declaredEnabled",
      "consented", "available", "errorCode", "updatedAt",
    ])
    && value.schemaVersion === 1
    && ["pluginId", "pluginVersion", "pluginHash", "serverId", "executable"].every(
      (key) => typeof value[key] === "string",
    )
    && Array.isArray(value.argv) && value.argv.every((item) => typeof item === "string")
    && Array.isArray(value.envNames) && value.envNames.every((item) => typeof item === "string")
    && ["connector", "workspace_read"].includes(String(value.permissionProfile))
    && isPositiveInteger(value.startupTimeoutSeconds)
    && isPositiveInteger(value.toolTimeoutSeconds)
    && typeof value.declaredEnabled === "boolean"
    && typeof value.consented === "boolean"
    && typeof value.available === "boolean"
    && (value.errorCode === undefined || typeof value.errorCode === "string")
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isMcpServerListResult(value: unknown): value is { servers: McpServerRecord[] } {
  return isRecord(value) && hasOnlyKeys(value, ["servers"])
    && Array.isArray(value.servers) && value.servers.every(isMcpServerRecord);
}

function isExtensionSnapshotResult(value: unknown): value is ExtensionSnapshotResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["plugins", "skills", "servers", "throughEventId"])
    && Array.isArray(value.plugins) && value.plugins.every(isPluginRecord)
    && Array.isArray(value.skills) && value.skills.every(isSkillMetadata)
    && Array.isArray(value.servers) && value.servers.every(isMcpServerRecord)
    && isNonNegativeInteger(value.throughEventId)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  const allowedKeys = new Set(allowed);
  return Object.keys(value).every((key) => allowedKeys.has(key));
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

function approvalRequestFrom(
  message: { jsonrpc: "2.0"; id: string; method: string; params: unknown },
): ApprovalRequest | undefined {
  if (message.method !== "item/requestApproval" || !isRecord(message.params)) {
    return undefined;
  }
  const params = message.params;
  const common = (
    typeof params.sessionId === "string"
    && typeof params.runId === "string"
    && typeof params.itemId === "string"
    && typeof params.toolCallId === "string"
    && typeof params.summary === "string"
  );
  if (!common) {
    return undefined;
  }
  if (params.kind === "file_change") {
    if (typeof params.diff !== "string") {
      return undefined;
    }
    return {
      id: message.id,
      sessionId: params.sessionId as string,
      runId: params.runId as string,
      itemId: params.itemId as string,
      toolCallId: params.toolCallId as string,
      kind: "file_change",
      summary: params.summary as string,
      diff: params.diff as string,
    };
  }
  if (params.kind === "external_tool") {
    const provenance = projectApprovalToolProvenance(params.provenance);
    if (
      typeof params.toolName !== "string"
      || !isRecord(params.arguments)
      || provenance === undefined
      || !["connector", "workspace_read"].includes(String(params.permissionProfile))
      || !isPositiveInteger(params.timeoutSeconds)
      || !Array.isArray(params.envNames)
      || !params.envNames.every((name) => typeof name === "string")
    ) {
      return undefined;
    }
    return {
      id: message.id,
      sessionId: params.sessionId as string,
      runId: params.runId as string,
      itemId: params.itemId as string,
      toolCallId: params.toolCallId as string,
      kind: "external_tool",
      summary: params.summary as string,
      toolName: params.toolName as string,
      arguments: params.arguments as Record<string, unknown>,
      provenance,
      permissionProfile: params.permissionProfile as "connector" | "workspace_read",
      timeoutSeconds: params.timeoutSeconds as number,
      envNames: [...(params.envNames as string[])],
    };
  }
  if (params.kind === "network_access") {
    if (
      typeof params.toolName !== "string"
      || !Array.isArray(params.hosts)
      || params.hosts.length === 0
      || !params.hosts.every((host) => typeof host === "string")
      || typeof params.target !== "string"
    ) {
      return undefined;
    }
    return {
      id: message.id,
      sessionId: params.sessionId as string,
      runId: params.runId as string,
      itemId: params.itemId as string,
      toolCallId: params.toolCallId as string,
      kind: "network_access",
      summary: params.summary as string,
      toolName: params.toolName as string,
      hosts: [...(params.hosts as string[])],
      target: params.target as string,
    };
  }
  if (
    params.kind === "command_execution"
    && typeof params.command === "string"
    && typeof params.cwd === "string"
    && typeof params.networkEnabled === "boolean"
    && isPositiveInteger(params.timeoutSeconds)
    && (
      params.executionMode === undefined
      || ["default_sandbox", "expanded_sandbox", "unsandboxed"].includes(String(params.executionMode))
    )
    && (
      params.sandboxPermissions === undefined
      || ["use_default", "with_additional_permissions", "require_escalated"].includes(String(params.sandboxPermissions))
    )
    && [params.additionalReadAccess, params.additionalWriteAccess, params.additionalExecutableAccess]
      .every((paths) => paths === undefined || (Array.isArray(paths) && paths.every((path) => typeof path === "string")))
    && (params.reason === undefined || typeof params.reason === "string")
    && (params.escalationReason === undefined || typeof params.escalationReason === "string")
    && (params.attemptOrdinal === undefined || params.attemptOrdinal === 0 || params.attemptOrdinal === 1)
  ) {
    return {
      id: message.id,
      sessionId: params.sessionId as string,
      runId: params.runId as string,
      itemId: params.itemId as string,
      toolCallId: params.toolCallId as string,
      kind: "command_execution",
      summary: params.summary as string,
      command: params.command as string,
      cwd: params.cwd as string,
      networkEnabled: params.networkEnabled as boolean,
      timeoutSeconds: params.timeoutSeconds as number,
      executionMode: (params.executionMode ?? "default_sandbox") as NonNullable<CommandApprovalRequest["executionMode"]>,
      sandboxPermissions: (params.sandboxPermissions ?? "use_default") as NonNullable<CommandApprovalRequest["sandboxPermissions"]>,
      additionalReadAccess: [...(params.additionalReadAccess as string[] | undefined ?? [])],
      additionalWriteAccess: [...(params.additionalWriteAccess as string[] | undefined ?? [])],
      additionalExecutableAccess: [...(params.additionalExecutableAccess as string[] | undefined ?? [])],
      reason: (params.reason as string | undefined) ?? "",
      escalationReason: (params.escalationReason as string | undefined) ?? "",
      attemptOrdinal: (params.attemptOrdinal ?? 0) as 0 | 1,
    };
  }
  return undefined;
}

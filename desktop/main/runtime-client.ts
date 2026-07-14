import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import readline from "node:readline";


const MAX_MESSAGE_BYTES = 1024 * 1024;

export interface InitializeResult {
  protocolVersion: number;
  runtimeVersion: string;
  capabilities: {
    runShell: boolean;
    modelConfigured: boolean;
  };
}

export interface ModelStatus {
  provider: "deepseek";
  model: "deepseek-v4-flash";
  configured: boolean;
}

export interface Session {
  id: string;
  workspaceRoot: string;
  createdAt: number;
  updatedAt: number;
}

export interface SessionListResult {
  items: Session[];
  nextCursor?: string;
}

export interface SessionSnapshot {
  session: Session;
  runs: Run[];
  items: Item[];
  previousItemId?: string;
}

export type RunStatus =
  | "running"
  | "waiting_approval"
  | "succeeded"
  | "failed"
  | "canceled"
  | "interrupted";

export interface Run {
  id: string;
  sessionId: string;
  userInput: string;
  status: RunStatus;
  modelStepCount: number;
  createdAt: number;
  startedAt: number;
  updatedAt: number;
  completedAt?: number;
  errorCode?: string;
}

export interface ToolCall {
  id: string;
  itemId: string;
  modelStepIndex: number;
  batchOrder: number;
  providerCallId: string;
  toolName: string;
  status: "running" | "completed" | "failed" | "canceled";
  argumentsJson: string;
  resultJson?: string;
  startedAt: number;
  completedAt?: number;
}

export interface Item {
  id: string;
  sessionId: string;
  runId: string;
  ordinal: number;
  kind:
    | "user_message"
    | "assistant_message"
    | "file_change"
    | "command_execution"
    | "tool_call";
  status: "in_progress" | "completed" | "failed" | "declined" | "canceled";
  createdAt: number;
  modelStepIndex?: number;
  content?: string;
  completedAt?: number;
  toolCall?: ToolCall;
}

export type RuntimeNotification =
  | { method: "run/started"; params: { sessionId: string; run: Run } }
  | {
      method: "item/started" | "item/completed";
      params: { sessionId: string; runId: string; item: Item };
    }
  | {
      method: "item/delta";
      params: {
        sessionId: string;
        runId: string;
        itemId: string;
        sequence: number;
        delta: string;
      };
    }
  | { method: "run/completed"; params: { sessionId: string; run: Run } };

interface RuntimeClientOptions {
  pythonExecutable: string;
  runtimeRoot: string;
  dataDirectory?: string;
  environment?: Record<string, string>;
  onNotification?: (notification: RuntimeNotification) => void;
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
    super(error.message);
    this.name = "RuntimeRequestError";
    this.rpcCode = error.code;
    this.businessCode = error.data?.code;
  }
}

export class RuntimeClient {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly pending = new Map<string, PendingRequest>();
  private readonly exitPromise: Promise<number>;
  private readonly onNotification: ((notification: RuntimeNotification) => void) | undefined;
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
        client: { name: "eidos-desktop", version: "0.1.0" },
        protocolVersion: 1,
      },
      isInitializeResult,
    );
  }

  async createSession(workspaceRoot: string): Promise<Session> {
    return this.validatedRequest("session/create", { workspaceRoot }, isSession);
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

  startRun(sessionId: string, userInput: string): Promise<Run> {
    return this.validatedRequest("run/start", { sessionId, userInput }, isRun);
  }

  cancelRun(runId: string): Promise<Run> {
    return this.validatedRequest("run/cancel", { runId }, isRun);
  }

  modelStatus(): Promise<ModelStatus> {
    return this.validatedRequest("model/status", {}, isModelStatus);
  }

  configureModel(apiKey: string): Promise<ModelStatus> {
    return this.validatedRequest("model/configure", { apiKey }, isModelStatus);
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
      this.writeProtocolMessage({
        jsonrpc: "2.0",
        id: message.id,
        error: { code: -32601, message: "Method not found" },
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
  if (value.method === "run/started" || value.method === "run/completed") {
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
    return value.method === "run/started"
      ? run.status === "running"
      : !["running", "waiting_approval"].includes(run.status);
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

function isModelStatus(value: unknown): value is ModelStatus {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["provider", "model", "configured"])
    && value.provider === "deepseek"
    && value.model === "deepseek-v4-flash"
    && typeof value.configured === "boolean"
  );
}

function isSession(value: unknown): value is Session {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["id", "workspaceRoot", "createdAt", "updatedAt"])
    && typeof value.id === "string"
    && typeof value.workspaceRoot === "string"
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
    && hasOnlyKeys(value, ["session", "runs", "items", "previousItemId"])
    && isSession(value.session)
    && Array.isArray(value.runs)
    && value.runs.every(isRun)
    && Array.isArray(value.items)
    && value.items.every(isItem)
    && (value.previousItemId === undefined || typeof value.previousItemId === "string")
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
      "modelStepCount",
      "createdAt",
      "startedAt",
      "updatedAt",
      "completedAt",
      "errorCode",
    ])
  ) {
    return false;
  }
  return (
    typeof value.id === "string"
    && typeof value.sessionId === "string"
    && typeof value.userInput === "string"
    && ["running", "waiting_approval", "succeeded", "failed", "canceled", "interrupted"].includes(String(value.status))
    && isNonNegativeInteger(value.modelStepCount)
    && isNonNegativeInteger(value.createdAt)
    && isNonNegativeInteger(value.startedAt)
    && isNonNegativeInteger(value.updatedAt)
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
    && (value.errorCode === undefined || typeof value.errorCode === "string")
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
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
  );
  if (!valid) {
    return false;
  }
  return value.kind === "tool_call" ? isToolCall(value.toolCall) : value.toolCall === undefined;
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
    && typeof value.argumentsJson === "string"
    && (value.resultJson === undefined || typeof value.resultJson === "string")
    && isNonNegativeInteger(value.startedAt)
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  const allowedKeys = new Set(allowed);
  return Object.keys(value).every((key) => allowedKeys.has(key));
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

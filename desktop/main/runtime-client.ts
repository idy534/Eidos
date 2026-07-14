import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import readline from "node:readline";


const MAX_MESSAGE_BYTES = 1024 * 1024;

export interface InitializeResult {
  protocolVersion: number;
  runtimeVersion: string;
  capabilities: {
    runShell: boolean;
  };
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
  runs: unknown[];
  items: unknown[];
}

interface RuntimeClientOptions {
  pythonExecutable: string;
  runtimeRoot: string;
  dataDirectory?: string;
  onStderr?: (line: string) => void;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
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
  private nextRequestId = 1;
  private closed = false;

  constructor(options: RuntimeClientOptions) {
    const pythonPath = [options.runtimeRoot, process.env.PYTHONPATH]
      .filter((entry): entry is string => Boolean(entry))
      .join(path.delimiter);
    const environment: NodeJS.ProcessEnv = {
      ...process.env,
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

    const stdout = readline.createInterface({ input: this.child.stdout });
    stdout.on("line", (line) => this.handleLine(line));

    const stderr = readline.createInterface({ input: this.child.stderr });
    stderr.on("line", (line) => options.onStderr?.(line));

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
    return this.request("initialize", {
      client: { name: "eidos-desktop", version: "0.1.0" },
      protocolVersion: 1,
    });
  }

  createSession(workspaceRoot: string): Promise<Session> {
    return this.request("session/create", { workspaceRoot });
  }

  listSessions(
    options: { limit?: number; cursor?: string } = {},
  ): Promise<SessionListResult> {
    return this.request("session/list", options);
  }

  readSession(sessionId: string): Promise<SessionSnapshot> {
    return this.request("session/read", { sessionId });
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
      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
      });
      this.child.stdin.write(`${serialized}\n`, "utf8", (error) => {
        if (!error) {
          return;
        }
        this.pending.delete(id);
        reject(error);
      });
    });
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

    if (!isResponse(message)) {
      this.failProtocol("Runtime wrote an invalid JSON-RPC response");
      return;
    }

    const pending = this.pending.get(message.id);
    if (!pending) {
      this.failProtocol("Runtime returned an unknown response id");
      return;
    }
    this.pending.delete(message.id);

    if ("error" in message) {
      pending.reject(new RuntimeRequestError(message.error));
      return;
    }
    pending.resolve(message.result);
  }

  private failProtocol(message: string): void {
    this.closed = true;
    this.failAll(new Error(message));
    this.child.kill();
  }

  private failAll(error: Error): void {
    for (const request of this.pending.values()) {
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

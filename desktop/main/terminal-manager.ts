import { randomUUID } from "node:crypto";
import { realpath, stat } from "node:fs/promises";
import path from "node:path";

import { IPC } from "../shared/index.js";
import type {
  SessionSnapshot,
  TerminalDataEvent,
  TerminalExitEvent,
  TerminalSessionInfo,
} from "../shared/index.js";

const DEFAULT_COLUMNS = 80;
const DEFAULT_ROWS = 24;
const MIN_TERMINAL_DIMENSION = 2;
const MAX_TERMINAL_DIMENSION = 500;
const MAX_TERMINAL_INPUT_BYTES = 64 * 1024;
const MAX_TERMINAL_EVENT_CHARACTERS = 16 * 1024;
export const MAX_TERMINALS_PER_OWNER = 12;

export interface TerminalOwner {
  id: number;
  isDestroyed(): boolean;
  send(channel: string, payload: unknown): void;
}

export interface TerminalProcess {
  onData(listener: (data: string) => void): { dispose(): void };
  onExit(listener: (event: { exitCode: number; signal?: number }) => void): { dispose(): void };
  write(data: string): void;
  resize(columns: number, rows: number): void;
  kill(): void;
}

export interface TerminalSpawnOptions {
  cwd: string;
  cols: number;
  rows: number;
  env: Record<string, string>;
  name: string;
}

export interface TerminalManagerDependencies {
  readSession(sessionId: string): Promise<SessionSnapshot>;
  spawn(file: string, args: string[], options: TerminalSpawnOptions): TerminalProcess;
  resolveDirectory?(root: string): Promise<string>;
  environment?: NodeJS.ProcessEnv;
  createId?(): string;
}

interface TerminalEntry {
  terminalId: string;
  sessionId: string;
  owner: TerminalOwner;
  process: TerminalProcess;
  dataSubscription: { dispose(): void };
  exitSubscription: { dispose(): void };
}

async function resolveDirectory(root: string): Promise<string> {
  if (!path.isAbsolute(root)) throw new Error("Workspace 当前不可用。");
  const canonicalRoot = await realpath(root);
  const metadata = await stat(canonicalRoot);
  if (!metadata.isDirectory()) throw new Error("Workspace 当前不可用。");
  return canonicalRoot;
}

function buildTerminalEnvironment(source: NodeJS.ProcessEnv): Record<string, string> {
  const result: Record<string, string> = {};
  const allowed = [
    "HOME", "USER", "LOGNAME", "PATH", "SHELL", "TMPDIR", "LANG", "LC_ALL", "SSH_AUTH_SOCK",
  ];
  for (const key of allowed) {
    const value = source[key];
    if (typeof value === "string" && value.length <= 32 * 1024) result[key] = value;
  }
  for (const [key, value] of Object.entries(source)) {
    if (/^LC_[A-Z_]+$/.test(key) && typeof value === "string" && value.length <= 32 * 1024) {
      result[key] = value;
    }
  }
  result.TERM = "xterm-256color";
  result.COLORTERM = "truecolor";
  result.TERM_PROGRAM = "Eidos";
  return result;
}

function validateIdentifier(value: string, label: string): void {
  if (!value || value.length > 256) throw new Error(`${label}参数无效。`);
}

export class TerminalManager {
  private readonly entries = new Map<string, TerminalEntry>();
  private readonly ownerCounts = new Map<number, number>();
  private readonly resolveDirectory: (root: string) => Promise<string>;
  private readonly environment: NodeJS.ProcessEnv;
  private readonly createId: () => string;

  constructor(private readonly deps: TerminalManagerDependencies) {
    this.resolveDirectory = deps.resolveDirectory ?? resolveDirectory;
    this.environment = deps.environment ?? process.env;
    this.createId = deps.createId ?? randomUUID;
  }

  async create(owner: TerminalOwner, sessionId: string): Promise<TerminalSessionInfo> {
    validateIdentifier(sessionId, "Session ");
    if (owner.isDestroyed()) throw new Error("终端窗口已经关闭。");
    this.reserveOwner(owner.id);

    try {
      const snapshot = await this.deps.readSession(sessionId);
      const root = this.executionRoot(snapshot);
      let cwd: string;
      try {
        cwd = await this.resolveDirectory(root);
      } catch {
        throw new Error("Workspace 当前不可用。");
      }
      if (owner.isDestroyed()) throw new Error("终端窗口已经关闭。");

      const terminalId = this.createId();
      validateIdentifier(terminalId, "Terminal ");
      if (this.entries.has(terminalId)) throw new Error("终端标识冲突。");

      const process = this.deps.spawn("/bin/zsh", ["-l"], {
        cwd,
        cols: DEFAULT_COLUMNS,
        rows: DEFAULT_ROWS,
        env: buildTerminalEnvironment(this.environment),
        name: "xterm-256color",
      });

      const entry = {} as TerminalEntry;
      entry.terminalId = terminalId;
      entry.sessionId = sessionId;
      entry.owner = owner;
      entry.process = process;
      entry.dataSubscription = process.onData((data) => this.handleData(entry, data));
      entry.exitSubscription = process.onExit((event) => this.handleExit(entry, event));
      this.entries.set(terminalId, entry);
      return { terminalId, sessionId };
    } catch (error) {
      this.releaseOwner(owner.id);
      throw error;
    }
  }

  write(owner: TerminalOwner, terminalId: string, data: string): void {
    const entry = this.ownedEntry(owner, terminalId);
    if (Buffer.byteLength(data, "utf8") > MAX_TERMINAL_INPUT_BYTES) {
      throw new Error("终端输入过大。");
    }
    entry.process.write(data);
  }

  resize(owner: TerminalOwner, terminalId: string, columns: number, rows: number): void {
    const entry = this.ownedEntry(owner, terminalId);
    if (
      !Number.isInteger(columns)
      || !Number.isInteger(rows)
      || columns < MIN_TERMINAL_DIMENSION
      || rows < MIN_TERMINAL_DIMENSION
      || columns > MAX_TERMINAL_DIMENSION
      || rows > MAX_TERMINAL_DIMENSION
    ) {
      throw new Error("终端尺寸无效。");
    }
    entry.process.resize(columns, rows);
  }

  close(owner: TerminalOwner, terminalId: string): void {
    const entry = this.ownedEntry(owner, terminalId);
    this.remove(entry);
    this.kill(entry);
  }

  closeOwner(ownerId: number): void {
    this.closeMatching((entry) => entry.owner.id === ownerId);
  }

  closeSession(sessionId: string): void {
    this.closeMatching((entry) => entry.sessionId === sessionId);
  }

  closeAll(): void {
    this.closeMatching(() => true);
  }

  private executionRoot(snapshot: SessionSnapshot): string {
    const { session } = snapshot;
    if (session.projectless) throw new Error("Projectless 会话不提供终端。");
    if (session.executionMode !== "worktree") {
      if (!session.workspaceRoot) throw new Error("Workspace 当前不可用。");
      return session.workspaceRoot;
    }
    const worktree = session.worktree;
    if (
      !worktree
      || worktree.state !== "active"
      || !worktree.worktreeRoot
      || (session.associatedWorktreeId !== undefined
        && session.associatedWorktreeId !== worktree.worktreeId)
    ) {
      throw new Error("Worktree 当前不可用。");
    }
    return worktree.worktreeRoot;
  }

  private reserveOwner(ownerId: number): void {
    const current = this.ownerCounts.get(ownerId) ?? 0;
    if (current >= MAX_TERMINALS_PER_OWNER) throw new Error("终端数量已达到上限。");
    this.ownerCounts.set(ownerId, current + 1);
  }

  private releaseOwner(ownerId: number): void {
    const next = (this.ownerCounts.get(ownerId) ?? 1) - 1;
    if (next <= 0) this.ownerCounts.delete(ownerId);
    else this.ownerCounts.set(ownerId, next);
  }

  private ownedEntry(owner: TerminalOwner, terminalId: string): TerminalEntry {
    validateIdentifier(terminalId, "Terminal ");
    const entry = this.entries.get(terminalId);
    if (!entry || entry.owner.id !== owner.id) throw new Error("终端不存在。");
    return entry;
  }

  private handleData(entry: TerminalEntry, data: string): void {
    if (!this.entries.has(entry.terminalId) || entry.owner.isDestroyed() || !data) return;
    for (let offset = 0; offset < data.length; offset += MAX_TERMINAL_EVENT_CHARACTERS) {
      const payload: TerminalDataEvent = {
        terminalId: entry.terminalId,
        data: data.slice(offset, offset + MAX_TERMINAL_EVENT_CHARACTERS),
      };
      entry.owner.send(IPC.TERMINAL_DATA_EVENT, payload);
    }
  }

  private handleExit(entry: TerminalEntry, event: { exitCode: number; signal?: number }): void {
    if (!this.entries.has(entry.terminalId)) return;
    this.remove(entry);
    if (entry.owner.isDestroyed()) return;
    const payload: TerminalExitEvent = {
      terminalId: entry.terminalId,
      exitCode: event.exitCode,
      ...(event.signal === undefined ? {} : { signal: event.signal }),
    };
    entry.owner.send(IPC.TERMINAL_EXIT_EVENT, payload);
  }

  private closeMatching(matches: (entry: TerminalEntry) => boolean): void {
    for (const entry of [...this.entries.values()]) {
      if (!matches(entry)) continue;
      this.remove(entry);
      this.kill(entry);
    }
  }

  private kill(entry: TerminalEntry): void {
    try {
      entry.process.kill();
    } catch {
      // Cleanup is best-effort. One exited PTY must not block the remaining cleanup.
    }
  }

  private remove(entry: TerminalEntry): void {
    if (!this.entries.delete(entry.terminalId)) return;
    entry.dataSubscription.dispose();
    entry.exitSubscription.dispose();
    this.releaseOwner(entry.owner.id);
  }
}

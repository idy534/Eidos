import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
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

export interface ResolvedTerminalShell {
  file: string;
  args: string[];
}

export interface ResolveTerminalShellOptions {
  platform?: NodeJS.Platform;
  environment: NodeJS.ProcessEnv;
  preferredShell?: string | undefined;
  fileExists?: (filePath: string) => boolean;
}

function shellArgsFor(filePath: string, windows: boolean): string[] {
  const base = (windows ? path.win32.basename(filePath) : path.posix.basename(filePath)).toLowerCase();
  if (base === "pwsh.exe" || base === "pwsh" || base === "powershell.exe" || base === "powershell") {
    return ["-NoLogo"];
  }
  if (base === "cmd.exe" || base === "cmd" || base === "wsl.exe" || base === "wsl" || base === "sh") {
    return [];
  }
  if (base === "zsh" || base === "bash" || base === "bash.exe" || base === "fish") {
    return ["-l"];
  }
  return windows ? [] : ["-l"];
}

function findExecutableOnPath(
  executable: string,
  pathEnv: string | undefined,
  delimiter: string,
  pathImpl: typeof path.win32 | typeof path.posix,
  fileExists: (filePath: string) => boolean,
): string | undefined {
  if (!pathEnv) return undefined;
  for (const segment of pathEnv.split(delimiter)) {
    const dir = segment.trim().replace(/^"|"$/g, "");
    if (!dir) continue;
    const candidate = pathImpl.join(dir, executable);
    if (fileExists(candidate)) return candidate;
  }
  return undefined;
}

export function resolveTerminalShell(options: ResolveTerminalShellOptions): ResolvedTerminalShell {
  const platform = options.platform ?? globalThis.process.platform;
  const windows = platform === "win32";
  const env = options.environment;
  const fileExists = options.fileExists ?? existsSync;
  const preferred = options.preferredShell?.trim();
  if (preferred && /[\0\r\n]/.test(preferred)) {
    throw new Error("Shell 路径无效。");
  }

  if (windows) {
    const systemRoot = env.SystemRoot;
    const validSystemRoot = Boolean(systemRoot && path.win32.isAbsolute(systemRoot));
    const programFiles = env.ProgramFiles;
    const programFilesX86 = env["ProgramFiles(x86)"];
    const pwshCandidates = [
      ...(programFiles ? [path.win32.join(programFiles, "PowerShell", "7", "pwsh.exe")] : []),
      ...(findExecutableOnPath("pwsh.exe", env.PATH ?? env.Path, ";", path.win32, fileExists)
        ? [findExecutableOnPath("pwsh.exe", env.PATH ?? env.Path, ";", path.win32, fileExists)!]
        : []),
    ];
    const powershellCandidates = validSystemRoot
      ? [path.win32.join(systemRoot!, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")]
      : [];
    const gitBashCandidates = [
      ...(programFiles ? [path.win32.join(programFiles, "Git", "bin", "bash.exe")] : []),
      ...(programFilesX86 ? [path.win32.join(programFilesX86, "Git", "bin", "bash.exe")] : []),
    ];
    const wslCandidates = validSystemRoot
      ? [path.win32.join(systemRoot!, "System32", "wsl.exe")]
      : [];
    const cmdCandidates = [
      ...(env.COMSPEC && path.win32.isAbsolute(env.COMSPEC) ? [env.COMSPEC] : []),
      ...(validSystemRoot ? [path.win32.join(systemRoot!, "System32", "cmd.exe")] : []),
    ];

    if (preferred) {
      const aliasMap: Record<string, string[]> = {
        pwsh: pwshCandidates,
        powershell: powershellCandidates,
        "git-bash": gitBashCandidates,
        wsl: wslCandidates,
        cmd: cmdCandidates,
      };
      const mapped = aliasMap[preferred.toLowerCase()];
      if (mapped) {
        const hit = mapped.find((candidate) => fileExists(candidate));
        if (hit) return { file: hit, args: shellArgsFor(hit, true) };
      } else if (path.win32.isAbsolute(preferred) && fileExists(preferred)) {
        return { file: preferred, args: shellArgsFor(preferred, true) };
      }
    }

    const candidates = [
      ...pwshCandidates,
      ...powershellCandidates,
      ...gitBashCandidates,
      ...cmdCandidates,
    ];
    const resolved = candidates.find((candidate) => fileExists(candidate));
    if (resolved) return { file: resolved, args: shellArgsFor(resolved, true) };
    if (validSystemRoot && powershellCandidates[0]) {
      return { file: powershellCandidates[0], args: shellArgsFor(powershellCandidates[0], true) };
    }
    throw new Error("Windows 系统目录不可用。");
  }

  const posixFallbacks = ["/bin/zsh", "/bin/bash", "/bin/sh"];
  if (preferred) {
    const aliasMap: Record<string, string[]> = {
      zsh: ["/bin/zsh", "/usr/bin/zsh", "/opt/homebrew/bin/zsh"],
      bash: ["/bin/bash", "/usr/bin/bash", "/opt/homebrew/bin/bash"],
      fish: ["/opt/homebrew/bin/fish", "/usr/local/bin/fish", "/usr/bin/fish", "/bin/fish"],
      sh: ["/bin/sh"],
    };
    const mapped = aliasMap[preferred.toLowerCase()];
    if (mapped) {
      const hit = mapped.find((candidate) => fileExists(candidate));
      if (hit) return { file: hit, args: shellArgsFor(hit, false) };
    } else if (path.posix.isAbsolute(preferred) && fileExists(preferred)) {
      return { file: preferred, args: shellArgsFor(preferred, false) };
    }
  }

  const envShell = env.SHELL?.trim();
  if (envShell && path.posix.isAbsolute(envShell) && fileExists(envShell)) {
    return { file: envShell, args: shellArgsFor(envShell, false) };
  }
  const fallback = posixFallbacks.find((candidate) => fileExists(candidate)) ?? "/bin/zsh";
  return { file: fallback, args: shellArgsFor(fallback, false) };
}

export interface TerminalManagerDependencies {
  readSession(sessionId: string): Promise<SessionSnapshot>;
  resolveProjectRoot?(projectId: string): Promise<string>;
  spawn(file: string, args: string[], options: TerminalSpawnOptions): TerminalProcess;
  resolveDirectory?(root: string): Promise<string>;
  environment?: NodeJS.ProcessEnv;
  createId?(): string;
  platform?: NodeJS.Platform;
  fileExists?: (filePath: string) => boolean;
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
    ...(process.platform === "win32"
      ? ["SystemRoot", "SystemDrive", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        "TEMP", "TMP", "ProgramData", "ProgramFiles", "ProgramFiles(x86)", "PSModulePath"]
      : []),
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
  private readonly platform: NodeJS.Platform;
  private readonly fileExists: (filePath: string) => boolean;

  constructor(private readonly deps: TerminalManagerDependencies) {
    this.resolveDirectory = deps.resolveDirectory ?? resolveDirectory;
    this.environment = deps.environment ?? process.env;
    this.createId = deps.createId ?? randomUUID;
    this.platform = deps.platform ?? globalThis.process.platform;
    this.fileExists = deps.fileExists ?? existsSync;
  }

  async create(
    owner: TerminalOwner,
    sessionId: string,
    workspaceRoot?: string,
    projectId?: string,
    preferredShell?: string,
  ): Promise<TerminalSessionInfo> {
    validateIdentifier(sessionId, "Session ");
    if (owner.isDestroyed()) throw new Error("终端窗口已经关闭。");
    this.reserveOwner(owner.id);

    try {
      const root = sessionId.startsWith("draft-")
        ? await this.draftExecutionRoot(workspaceRoot, projectId)
        : this.executionRoot(await this.deps.readSession(sessionId));
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

      const { file, args } = resolveTerminalShell({
        platform: this.platform,
        environment: this.environment,
        preferredShell,
        fileExists: this.fileExists,
      });
      const process = this.deps.spawn(file, args, {
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
    if (session.projectless || session.executionMode !== "worktree") {
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
    return session.roots?.[0]?.path ?? worktree.worktreeRoot;
  }

  private async draftExecutionRoot(
    workspaceRoot: string | undefined,
    projectId: string | undefined,
  ): Promise<string> {
    const root = projectId && this.deps.resolveProjectRoot
      ? await this.deps.resolveProjectRoot(projectId)
      : workspaceRoot;
    if (!root) throw new Error("Workspace 当前不可用。");
    return root;
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

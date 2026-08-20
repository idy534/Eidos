import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_TERMINALS_PER_OWNER,
  TerminalManager,
  type TerminalOwner,
  type TerminalProcess,
} from "./terminal-manager.js";
import type { Session, SessionSnapshot } from "../shared/index.js";

class FakeTerminalProcess implements TerminalProcess {
  writes: string[] = [];
  resizes: Array<{ columns: number; rows: number }> = [];
  killCalls = 0;
  throwOnKill = false;
  private dataListener: ((data: string) => void) | undefined;
  private exitListener: ((event: { exitCode: number; signal?: number }) => void) | undefined;

  onData(listener: (data: string) => void): { dispose(): void } {
    this.dataListener = listener;
    return { dispose: () => { this.dataListener = undefined; } };
  }

  onExit(listener: (event: { exitCode: number; signal?: number }) => void): { dispose(): void } {
    this.exitListener = listener;
    return { dispose: () => { this.exitListener = undefined; } };
  }

  write(data: string): void {
    this.writes.push(data);
  }

  resize(columns: number, rows: number): void {
    this.resizes.push({ columns, rows });
  }

  kill(): void {
    this.killCalls += 1;
    if (this.throwOnKill) throw new Error("kill failed");
  }

  emitData(data: string): void {
    this.dataListener?.(data);
  }

  emitExit(event: { exitCode: number; signal?: number }): void {
    this.exitListener?.(event);
  }
}

function session(overrides: Partial<Session> = {}): SessionSnapshot {
  return {
    session: {
      id: "session-1",
      workspaceRoot: "/workspace",
      executionMode: "local",
      taskStatus: "new",
      createdAt: 1,
      updatedAt: 1,
      ...overrides,
    },
    runs: [],
    items: [],
    stepResolutions: [],
  };
}

function owner(id = 1): TerminalOwner & { messages: Array<{ channel: string; payload: unknown }> } {
  const messages: Array<{ channel: string; payload: unknown }> = [];
  return {
    id,
    messages,
    isDestroyed: () => false,
    send: (channel, payload) => { messages.push({ channel, payload }); },
  };
}

function harness(options: {
  snapshot?: SessionSnapshot;
  resolveDirectory?: (root: string) => Promise<string>;
  environment?: NodeJS.ProcessEnv;
} = {}) {
  const processes: FakeTerminalProcess[] = [];
  const spawns: Array<{
    file: string;
    args: string[];
    options: { cwd: string; cols: number; rows: number; env: Record<string, string>; name: string };
  }> = [];
  let nextId = 1;
  const manager = new TerminalManager({
    readSession: async () => options.snapshot ?? session(),
    resolveDirectory: options.resolveDirectory ?? (async (root) => `/real${root}`),
    environment: options.environment ?? {
      HOME: "/Users/test",
      USER: "test",
      LOGNAME: "test",
      PATH: "/usr/bin:/bin",
      SHELL: "/bin/fish",
      LANG: "zh_CN.UTF-8",
      LC_ALL: "zh_CN.UTF-8",
      SSH_AUTH_SOCK: "/tmp/agent.sock",
      API_KEY: "must-not-leak",
      EIDOS_DATA_DIR: "/secret/eidos",
    },
    createId: () => `terminal-${nextId++}`,
    spawn: (file, args, spawnOptions) => {
      const process = new FakeTerminalProcess();
      processes.push(process);
      spawns.push({ file, args, options: spawnOptions });
      return process;
    },
  });
  return { manager, processes, spawns };
}

test("a local terminal uses the canonical session workspace and a minimal environment", async () => {
  const h = harness();
  const terminalOwner = owner();

  const opened = await h.manager.create(terminalOwner, "session-1");

  assert.deepEqual(opened, { terminalId: "terminal-1", sessionId: "session-1" });
  assert.equal(h.spawns[0]?.file, "/bin/zsh");
  assert.deepEqual(h.spawns[0]?.args, ["-l"]);
  assert.equal(h.spawns[0]?.options.cwd, "/real/workspace");
  assert.equal(h.spawns[0]?.options.env.API_KEY, undefined);
  assert.equal(h.spawns[0]?.options.env.EIDOS_DATA_DIR, undefined);
  assert.equal(h.spawns[0]?.options.env.SSH_AUTH_SOCK, "/tmp/agent.sock");
  assert.equal(h.spawns[0]?.options.env.TERM_PROGRAM, "Eidos");
});

test("an active managed Worktree terminal uses its Worktree root", async () => {
  const h = harness({
    snapshot: session({
      executionMode: "worktree",
      associatedWorktreeId: "worktree-1",
      worktree: {
        worktreeId: "worktree-1",
        projectId: "project-1",
        repositoryRoot: "/repository",
        worktreeRoot: "/managed/worktree",
        baseRef: "main",
        baseCommit: "abc",
        branch: null,
        state: "active",
      },
    }),
  });

  await h.manager.create(owner(), "session-1");

  assert.equal(h.spawns[0]?.options.cwd, "/real/managed/worktree");
});

test("projectless and unavailable Worktree sessions cannot create user terminals", async () => {
  const projectless = harness({ snapshot: session({ projectless: true }) });
  await assert.rejects(
    projectless.manager.create(owner(), "session-1"),
    /Projectless 会话不提供终端/,
  );

  for (const state of ["missing", "invalid", "deleted"] as const) {
    const unavailable = harness({
      snapshot: session({
        executionMode: "worktree",
        associatedWorktreeId: "worktree-1",
        worktree: {
          worktreeId: "worktree-1",
          projectId: "project-1",
          repositoryRoot: "/repository",
          worktreeRoot: "/managed/worktree",
          baseRef: "main",
          baseCommit: "abc",
          branch: null,
          state,
        },
      }),
    });
    await assert.rejects(
      unavailable.manager.create(owner(), "session-1"),
      /Worktree 当前不可用/,
    );
  }
});

test("a stale Worktree binding and a missing workspace are rejected before spawn", async () => {
  const stale = harness({
    snapshot: session({
      executionMode: "worktree",
      associatedWorktreeId: "expected",
      worktree: {
        worktreeId: "other",
        projectId: "project-1",
        repositoryRoot: "/repository",
        worktreeRoot: "/managed/worktree",
        baseRef: "main",
        baseCommit: "abc",
        branch: null,
        state: "active",
      },
    }),
  });
  await assert.rejects(stale.manager.create(owner(), "session-1"), /Worktree 当前不可用/);

  const missing = harness({
    resolveDirectory: async () => { throw new Error("ENOENT"); },
  });
  await assert.rejects(missing.manager.create(owner(), "session-1"), /Workspace 当前不可用/);
});

test("terminal input, resize and close require the creating owner", async () => {
  const h = harness();
  const firstOwner = owner(1);
  const otherOwner = owner(2);
  const { terminalId } = await h.manager.create(firstOwner, "session-1");

  assert.throws(() => h.manager.write(otherOwner, terminalId, "pwd\r"), /终端不存在/);
  assert.throws(() => h.manager.resize(otherOwner, terminalId, 80, 24), /终端不存在/);
  assert.throws(() => h.manager.close(otherOwner, terminalId), /终端不存在/);

  h.manager.write(firstOwner, terminalId, "pwd\r");
  h.manager.resize(firstOwner, terminalId, 120, 40);
  h.manager.close(firstOwner, terminalId);

  assert.deepEqual(h.processes[0]?.writes, ["pwd\r"]);
  assert.deepEqual(h.processes[0]?.resizes, [{ columns: 120, rows: 40 }]);
  assert.equal(h.processes[0]?.killCalls, 1);
});

test("terminal input and dimensions are bounded", async () => {
  const h = harness();
  const terminalOwner = owner();
  const { terminalId } = await h.manager.create(terminalOwner, "session-1");

  assert.throws(
    () => h.manager.write(terminalOwner, terminalId, "x".repeat(64 * 1024 + 1)),
    /终端输入过大/,
  );
  assert.throws(() => h.manager.resize(terminalOwner, terminalId, 1, 24), /终端尺寸无效/);
  assert.throws(() => h.manager.resize(terminalOwner, terminalId, 80, 501), /终端尺寸无效/);
});

test("terminal data and exit events are sent only to the owner", async () => {
  const h = harness();
  const terminalOwner = owner();
  const { terminalId } = await h.manager.create(terminalOwner, "session-1");

  h.processes[0]?.emitData("hello");
  h.processes[0]?.emitExit({ exitCode: 0 });

  assert.deepEqual(terminalOwner.messages, [
    { channel: "terminal:data", payload: { terminalId, data: "hello" } },
    { channel: "terminal:exit", payload: { terminalId, exitCode: 0 } },
  ]);
  assert.throws(() => h.manager.write(terminalOwner, terminalId, "pwd\r"), /终端不存在/);
});

test("owner, session and app cleanup kill the matching terminal processes", async () => {
  const h = harness();
  const firstOwner = owner(1);
  const secondOwner = owner(2);
  await h.manager.create(firstOwner, "session-1");
  await h.manager.create(firstOwner, "session-2");
  await h.manager.create(secondOwner, "session-1");

  h.manager.closeSession("session-2");
  assert.deepEqual(h.processes.map((process) => process.killCalls), [0, 1, 0]);

  h.manager.closeOwner(1);
  assert.deepEqual(h.processes.map((process) => process.killCalls), [1, 1, 0]);

  h.manager.closeAll();
  assert.deepEqual(h.processes.map((process) => process.killCalls), [1, 1, 1]);
});

test("each Renderer owner has a fixed terminal process limit", async () => {
  const h = harness();
  const terminalOwner = owner();
  for (let index = 0; index < MAX_TERMINALS_PER_OWNER; index += 1) {
    await h.manager.create(terminalOwner, "session-1");
  }

  await assert.rejects(
    h.manager.create(terminalOwner, "session-1"),
    /终端数量已达到上限/,
  );
});

test("app cleanup still kills other terminals when one process cleanup fails", async () => {
  const h = harness();
  await h.manager.create(owner(1), "session-1");
  await h.manager.create(owner(2), "session-2");
  h.processes[0]!.throwOnKill = true;

  assert.doesNotThrow(() => h.manager.closeAll());
  assert.deepEqual(h.processes.map((process) => process.killCalls), [1, 1]);
});

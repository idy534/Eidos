import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";

import {
  buildRuntimeEnvironment,
  RuntimeClient,
  RuntimeRequestError,
} from "./runtime-client.js";
import type { RuntimeNotification } from "./runtime-client.js";


const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const protocolV1Fixture = path.join(projectRoot, "protocol", "fixtures", "v1.json");
const toolResultsV1Fixture = path.join(projectRoot, "protocol", "fixtures", "tool-results-v1.json");
const extensionsV1Fixture = path.join(projectRoot, "protocol", "fixtures", "extensions-v1.json");
const pythonExecutable = process.env.EIDOS_PYTHON
  ?? path.join(projectRoot, ".venv", "bin", "python");
const execFileAsync = promisify(execFile);


async function createGitRepository(prefix: string): Promise<string> {
  const repository = await mkdtemp(path.join(os.tmpdir(), prefix));
  await execFileAsync("git", ["init", "-q", "-b", "main"], { cwd: repository });
  await execFileAsync("git", ["config", "user.email", "eidos-tests@example.com"], {
    cwd: repository,
  });
  await execFileAsync("git", ["config", "user.name", "Eidos Tests"], {
    cwd: repository,
  });
  await writeFile(path.join(repository, "README.md"), "# Fixture\n", "utf8");
  await execFileAsync("git", ["add", "README.md"], { cwd: repository });
  await execFileAsync("git", ["commit", "-qm", "initial"], { cwd: repository });
  return repository;
}


async function managedWorktreeRoot(repository: string): Promise<string> {
  const result = await execFileAsync("git", ["worktree", "list", "--porcelain"], {
    cwd: repository,
  });
  const repositoryRoot = await realpath(repository);
  const roots = result.stdout
    .split("\n")
    .filter((line) => line.startsWith("worktree "))
    .map((line) => line.slice("worktree ".length));
  const managed = await (async () => {
    for (const root of roots) {
      const canonicalRoot = await realpath(root);
      if (canonicalRoot !== repositoryRoot) {
        return canonicalRoot;
      }
    }
    return undefined;
  })();
  assert.ok(managed, "session create did not create a managed worktree");
  return managed;
}


test("shares canonical ToolResult vectors with the Python runtime", async () => {
  const fixture = JSON.parse(await readFile(toolResultsV1Fixture, "utf8")) as {
    toolContractVersion: number;
    vectors: Array<{ result: unknown; canonicalJson: string }>;
  };
  assert.equal(fixture.toolContractVersion, 1);
  for (const vector of fixture.vectors) {
    assert.equal(JSON.stringify(sortJson(vector.result)), vector.canonicalJson);
  }
});

test("shares closed extension vectors with the Python runtime", async () => {
  const fixture = JSON.parse(await readFile(extensionsV1Fixture, "utf8")) as Record<string, unknown>;
  assert.equal(fixture.extensionContractVersion, 1);
  assert.equal((fixture.plugin as { id: string }).id, "demo");
  assert.equal((fixture.mcpServer as { permissionProfile: string }).permissionProfile, "connector");
  assert.deepEqual(
    (fixture.stepToolSnapshot as { deferredNames: string[] }).deferredNames,
    ["mcp__fixture__echo"],
  );
  assert.equal(
    (fixture.stepResolutionReview as { requestHash: string }).requestHash,
    "5".repeat(64),
  );
});


test("preserves a closed business error code without exposing runtime details", () => {
  const error = new RuntimeRequestError({
    code: -32000,
    message: "Request failed",
    data: { code: "RUN_ALREADY_ACTIVE", retryable: false },
  });

  assert.equal(error.message, "EIDOS_RUNTIME_ERROR:RUN_ALREADY_ACTIVE");
  assert.equal(error.businessCode, "RUN_ALREADY_ACTIVE");
});

test("preserves every workspace and lifecycle business code in the closed contract", () => {
  const codes = [
    "INVALID_PARAMS",
    "INVALID_CURSOR",
    "INVALID_EVENT_CURSOR",
    "REPOSITORY_NOT_FOUND",
    "NOT_A_GIT_REPOSITORY",
    "WORKTREE_REQUIRES_GIT",
    "BASE_REF_NOT_FOUND",
    "GIT_COMMAND_TIMEOUT",
    "GIT_REMOTE_OUTCOME_UNCERTAIN",
    "WORKTREE_CREATE_FAILED",
    "WORKTREE_PERSISTENCE_FAILED",
    "WORKTREE_RECOVERY_REQUIRED",
    "WORKSPACE_IDENTITY_UNAVAILABLE",
    "WORKSPACE_BOUNDARY_VIOLATION",
    "WORKSPACE_SENSITIVE_PATH",
    "WORKSPACE_UNAVAILABLE",
    "WORKSPACE_IDENTITY_CHANGED",
    "WORKSPACE_READ_TIMEOUT",
    "WORKSPACE_FILE_TOO_LARGE",
    "WORKSPACE_SENSITIVE_CONTENT",
    "GIT_DISCARD_REQUIRES_UNSTAGED",
    "REVIEW_DIFF_CHANGED",
    "REVIEW_ANCHOR_INVALID",
    "REVIEW_COMMENT_ID_REUSED",
    "REVIEW_COMMENT_NOT_FOUND",
    "CHECKPOINT_GIT_STATE_UNAVAILABLE",
    "CHECKPOINT_FORK_WORKTREE_FAILED",
    "CHECKPOINT_REWIND_FAILED",
    "CHECKPOINT_WORKFLOW_BUSY",
    "DIRECT_CHECKPOINT_FORK_PATH_FORBIDDEN",
    "MANAGED_CHECKPOINT_FORK_PATH_FORBIDDEN",
    "ASYNC_OPERATION_CANCELED",
    "ASYNC_OPERATION_INTERRUPTED",
  ];
  for (const code of codes) {
    const error = new RuntimeRequestError({
      code: -32000,
      message: "Request failed",
      data: { code, retryable: false },
    });
    assert.equal(error.businessCode, code);
    assert.equal(error.message, `EIDOS_RUNTIME_ERROR:${code}`);
  }
  const unknown = new RuntimeRequestError({
    code: -32000,
    message: "Request failed",
    data: { code: "UNRECOGNIZED_RUNTIME_CODE", retryable: false },
  });
  assert.equal(unknown.businessCode, "INTERNAL_ERROR");
});


test("development Runtime environment preserves inherited Python settings", () => {
  const environment = buildRuntimeEnvironment({
    runtimeRoot: "/workspace/runtime",
    baseEnvironment: {
      PYTHONPATH: "/developer/python",
      PYTHONHOME: "/developer/python-home",
      PYTHONNOUSERSITE: "0",
      PYTHONDONTWRITEBYTECODE: "0",
      EIDOS_PYTHON: "/developer/python/bin/python",
    },
    overrides: { EIDOS_FAKE_MODEL: "1" },
    policy: "development",
  });

  assert.equal(environment.PYTHONPATH, [
    "/workspace/runtime",
    "/developer/python",
  ].join(path.delimiter));
  assert.equal(environment.PYTHONHOME, "/developer/python-home");
  assert.equal(environment.PYTHONNOUSERSITE, "0");
  assert.equal(environment.PYTHONDONTWRITEBYTECODE, "0");
  assert.equal(environment.EIDOS_PYTHON, "/developer/python/bin/python");
  assert.equal(environment.EIDOS_FAKE_MODEL, "1");
});


test("packaged Runtime environment is isolated to the bundled app root", () => {
  const environment = buildRuntimeEnvironment({
    runtimeRoot: "/Applications/Eidos.app/Contents/Resources/runtime/app",
    baseEnvironment: {
      PYTHONPATH: "/developer/python",
      PYTHONHOME: "/developer/python-home",
      PYTHONNOUSERSITE: "0",
      PYTHONDONTWRITEBYTECODE: "0",
      EIDOS_PYTHON: "/developer/python/bin/python",
    },
    overrides: { EIDOS_FAKE_MODEL: "1" },
    policy: "packaged",
  });

  assert.equal(
    environment.PYTHONPATH,
    "/Applications/Eidos.app/Contents/Resources/runtime/app",
  );
  assert.equal(environment.PYTHONHOME, undefined);
  assert.equal(environment.PYTHONNOUSERSITE, "1");
  assert.equal(environment.PYTHONDONTWRITEBYTECODE, "1");
  assert.equal(environment.EIDOS_PYTHON, undefined);
  assert.equal(environment.EIDOS_FAKE_MODEL, "1");
});


test("spawns the Python runtime and completes initialize then shutdown", async () => {
  const stderrLines: string[] = [];
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));

  try {
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      onStderr: (line) => stderrLines.push(line),
    });

    const initialized = await client.initialize();
    assert.equal(initialized.protocolVersion, 1);
    assert.equal(initialized.runtimeVersion, "0.3.0");
    assert.equal(typeof initialized.capabilities.runShell, "boolean");
    assert.equal(initialized.capabilities.modelConfigured, false);
    assert.deepEqual(await client.health(), { state: "ready" });

    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    assert.ok(stderrLines.some((line) => line.includes("Runtime initialized")));
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
  }
});

test("creates and reads a persisted session across runtime restarts", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await createGitRepository("eidos-workspace-");

  try {
    const firstClient = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
    });
    await firstClient.initialize();
    const created = await firstClient.createSession(workspaceRoot);
    await firstClient.shutdown();
    assert.equal(await firstClient.waitForExit(), 0);

    const secondClient = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
    });
    await secondClient.initialize();
    const listed = await secondClient.listSessions();
    const snapshot = await secondClient.readSession(created.id);
    await secondClient.shutdown();
    assert.equal(await secondClient.waitForExit(), 0);

    assert.deepEqual(listed, { items: [created] });
    assert.deepEqual(snapshot, {
      session: created, runs: [], items: [], stepResolutions: [], throughEventId: 1,
    });
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("creates first-class Direct Workspace sessions without Git review state", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-direct-"));
  const client = new RuntimeClient({
    pythonExecutable,
    runtimeRoot: path.join(projectRoot, "runtime"),
    dataDirectory,
  });

  try {
    await client.initialize();
    assert.deepEqual(await client.readProjectGitContext(workspaceRoot), {
      gitAvailable: false,
      currentBranch: null,
      head: null,
      branches: [],
      dirty: false,
      changedFileCount: 0,
    });
    const first = await client.createSession(workspaceRoot);
    const second = await client.createSession(workspaceRoot);
    await mkdir(path.join(workspaceRoot, "nested folder"));
    await writeFile(path.join(workspaceRoot, "nested folder", "hello.ts"), "export const hello = true;\n", "utf8");
    assert.deepEqual(await client.listWorkspaceDirectory(first.id, "."), {
      path: ".",
      entries: [{
        name: "nested folder",
        relativePath: "nested folder",
        kind: "directory",
      }],
      truncated: false,
    });
    assert.deepEqual(await client.readWorkspaceFilePreview(first.id, "nested folder/hello.ts"), {
      path: "nested folder/hello.ts",
      kind: "code",
      sizeBytes: 27,
      truncated: false,
      content: "export const hello = true;\n",
      language: "typescript",
    });
    assert.equal(first.project?.workspaceRoot, await realpath(workspaceRoot));
    assert.equal(first.project?.gitAvailable, false);
    assert.equal(first.project?.id, second.project?.id);
    assert.equal(first.worktree, undefined);
    assert.equal(second.worktree, undefined);
    assert.deepEqual((await client.listSessions()).items.map((session) => session.project?.id), [
      first.project?.id,
      first.project?.id,
    ]);
  } finally {
    await client.shutdown();
    await client.waitForExit();
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("projects managed Worktrees and keeps Git review isolated per session", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const repositoryRoot = await createGitRepository("eidos-git-review-");
  const client = new RuntimeClient({
    pythonExecutable,
    runtimeRoot: path.join(projectRoot, "runtime"),
    dataDirectory,
  });

  try {
    await client.initialize();
    const gitContext = await client.readProjectGitContext(repositoryRoot);
    assert.equal(gitContext.gitAvailable, true);
    assert.equal(gitContext.currentBranch, "main");
    assert.ok(gitContext.head);
    assert.ok(gitContext.branches.includes("main"));
    const local = await client.createSession(repositoryRoot, { executionMode: "local" });
    await writeFile(path.join(repositoryRoot, "WORKFLOW.txt"), "workflow\n", "utf8");
    const localStatus = await client.readSessionGitStatus(local.id);
    assert.deepEqual(localStatus.untrackedFiles, ["WORKFLOW.txt"]);
    assert.equal(localStatus.untrackedCount, 1);
    const fileDiff = await client.readSessionGitDiff(local.id, "head", "WORKFLOW.txt");
    assert.deepEqual(fileDiff.changedFiles, ["WORKFLOW.txt"]);
    assert.match(fileDiff.unifiedDiff, /workflow/);
    const reviewComment = await client.createReviewComment(local.id, {
      commentId: randomUUID(),
      path: "WORKFLOW.txt",
      scope: "head",
      side: "new",
      line: 1,
      body: "Add a focused test.",
      baseHead: fileDiff.head,
      diffHash: fileDiff.diffHash,
    }, randomUUID());
    assert.equal(reviewComment.status, "active");
    assert.deepEqual(
      await client.listReviewComments(local.id, "WORKFLOW.txt", "head"),
      [reviewComment],
    );
    const discarded = await client.discardSessionGit(
      local.id,
      "WORKFLOW.txt",
      "14141414-1414-4414-8414-141414141414",
    );
    assert.deepEqual(discarded.status.untrackedFiles, []);
    await assert.rejects(readFile(path.join(repositoryRoot, "WORKFLOW.txt"), "utf8"));
    assert.equal(
      (await client.listReviewComments(local.id, "WORKFLOW.txt", "head"))[0]?.status,
      "stale",
    );
    assert.equal(
      await client.deleteReviewComment(local.id, reviewComment.id, randomUUID()),
      reviewComment.id,
    );
    await writeFile(path.join(repositoryRoot, "WORKFLOW.txt"), "workflow\n", "utf8");
    const stageOperationId = "77777777-7777-4777-8777-777777777777";
    const staged = await client.stageSessionGit(
      local.id,
      ["WORKFLOW.txt"],
      stageOperationId,
    );
    assert.deepEqual(staged.status.stagedFiles, ["WORKFLOW.txt"]);
    assert.deepEqual(
      await client.stageSessionGit(local.id, ["WORKFLOW.txt"], stageOperationId),
      staged,
    );
    const unstaged = await client.unstageSessionGit(
      local.id,
      ["WORKFLOW.txt"],
      "88888888-8888-4888-8888-888888888888",
    );
    assert.deepEqual(unstaged.status.untrackedFiles, ["WORKFLOW.txt"]);
    await client.stageSessionGit(local.id, ["WORKFLOW.txt"], randomUUID());
    const commitOperationId = "99999999-9999-4999-8999-999999999999";
    const committed = await client.commitSessionGit(
      local.id,
      "local workflow",
      commitOperationId,
    );
    assert.equal(committed.commit, committed.head);
    assert.deepEqual(
      await client.commitSessionGit(local.id, "local workflow", commitOperationId),
      committed,
    );
    await assert.rejects(
      client.commitSessionGit(local.id, "nothing staged", randomUUID()),
      (error: unknown) => (
        error instanceof RuntimeRequestError
        && error.businessCode === "GIT_NOTHING_STAGED"
      ),
    );
    const first = await client.createSession(repositoryRoot, { executionMode: "worktree" });
    const second = await client.createSession(repositoryRoot, { executionMode: "worktree" });
    assert.ok(first.worktree);
    assert.ok(second.worktree);
    assert.equal(first.worktree.projectId, second.worktree.projectId);
    assert.equal(first.worktree.repositoryRoot, second.worktree.repositoryRoot);
    assert.notEqual(first.worktree.worktreeId, second.worktree.worktreeId);
    assert.notEqual(first.worktree.worktreeRoot, second.worktree.worktreeRoot);
    assert.equal(first.worktree.branch, null);
    assert.equal(second.worktree.branch, null);

    const firstSnapshot = await client.readSession(first.id);
    const listed = await client.listSessions();
    assert.deepEqual(firstSnapshot.session.worktree, first.worktree);
    assert.deepEqual(
      new Map(listed.items.map((session) => [session.id, session.worktree])),
      new Map([
        [local.id, undefined],
        [first.id, first.worktree],
        [second.id, second.worktree],
      ]),
    );

    await writeFile(path.join(first.worktree.worktreeRoot, "README.md"), "# Committed in A\n", "utf8");
    await execFileAsync("git", ["add", "README.md"], { cwd: first.worktree.worktreeRoot });
    await execFileAsync("git", ["commit", "-qm", "session A commit"], {
      cwd: first.worktree.worktreeRoot,
    });
    await writeFile(path.join(first.worktree.worktreeRoot, "ONLY_A.txt"), "isolated\n", "utf8");

    const [firstStatus, secondStatus, headDiff, baselineDiff] = await Promise.all([
      client.readSessionGitStatus(first.id),
      client.readSessionGitStatus(second.id),
      client.readSessionGitDiff(first.id, "head"),
      client.readSessionGitDiff(first.id, "baseline"),
    ]);

    assert.equal(firstStatus.dirty, true);
    assert.equal(secondStatus.dirty, false);
    assert.deepEqual(headDiff.changedFiles, ["ONLY_A.txt"]);
    assert.equal(headDiff.scope, "head");
    assert.equal(baselineDiff.scope, "baseline");
    assert.ok(baselineDiff.changedFiles.includes("README.md"));
    assert.ok(baselineDiff.changedFiles.includes("ONLY_A.txt"));
    assert.match(headDiff.unifiedDiff, /ONLY_A\.txt/);
  } finally {
    await client.shutdown().catch(() => undefined);
    await client.waitForExit().catch(() => undefined);
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(repositoryRoot, { recursive: true, force: true });
  }
});

test("completes Git fetch, pull, push, merge, and rebase through typed contracts", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-fetch-data-"));
  const repositoryRoot = await createGitRepository("eidos-fetch-repo-");
  const remoteRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-fetch-remote-"));
  const peerParent = await mkdtemp(path.join(os.tmpdir(), "eidos-fetch-peer-"));
  const peerRoot = path.join(peerParent, "peer");
  await execFileAsync("git", ["init", "--bare", "-q"], { cwd: remoteRoot });
  await execFileAsync("git", ["remote", "add", "origin", remoteRoot], {
    cwd: repositoryRoot,
  });
  await execFileAsync("git", ["push", "-qu", "origin", "main"], {
    cwd: repositoryRoot,
  });
  await execFileAsync("git", ["clone", "-q", remoteRoot, peerRoot]);
  await execFileAsync("git", ["config", "user.name", "Eidos Tests"], { cwd: peerRoot });
  await execFileAsync("git", ["config", "user.email", "eidos-tests@example.com"], {
    cwd: peerRoot,
  });
  await writeFile(path.join(peerRoot, "REMOTE.txt"), "remote\n", "utf8");
  await execFileAsync("git", ["add", "REMOTE.txt"], { cwd: peerRoot });
  await execFileAsync("git", ["commit", "-qm", "remote commit"], { cwd: peerRoot });
  await execFileAsync("git", ["push", "-q", "origin", "main"], { cwd: peerRoot });
  const client = new RuntimeClient({
    pythonExecutable,
    runtimeRoot: path.join(projectRoot, "runtime"),
    dataDirectory,
  });

  try {
    await client.initialize();
    const session = await client.createSession(repositoryRoot, { executionMode: "local" });
    const before = await client.readSessionGitRemoteStatus(session.id);
    const headBefore = (await execFileAsync("git", ["rev-parse", "HEAD"], {
      cwd: repositoryRoot,
    })).stdout.trim();
    assert.equal(before.upstream?.remote, "origin");
    assert.equal(before.behind, 0);

    const fetched = await client.fetchSessionGit(session.id, randomUUID());

    assert.equal(fetched.remote, "origin");
    assert.equal(fetched.head, headBefore);
    assert.equal(fetched.behind, 1);
    assert.equal((await execFileAsync("git", ["rev-parse", "HEAD"], {
      cwd: repositoryRoot,
    })).stdout.trim(), headBefore);

    const pulled = await client.pullSessionGit(session.id, randomUUID());
    assert.equal(pulled.behind, 0);
    assert.equal(pulled.status.dirty, false);
    assert.notEqual(pulled.head, headBefore);

    await writeFile(path.join(repositoryRoot, "LOCAL.txt"), "local\n", "utf8");
    await execFileAsync("git", ["add", "LOCAL.txt"], { cwd: repositoryRoot });
    await execFileAsync("git", ["commit", "-qm", "local commit"], {
      cwd: repositoryRoot,
    });
    const pushed = await client.pushSessionGit(session.id, randomUUID());
    assert.equal(pushed.ahead, 0);
    assert.equal(pushed.behind, 0);
    const remoteHead = (await execFileAsync(
      "git", ["ls-remote", "origin", "refs/heads/main"], { cwd: repositoryRoot },
    )).stdout.trim().split(/\s+/)[0];
    assert.equal(remoteHead, pushed.head);

    await execFileAsync("git", ["switch", "-qc", "topic"], { cwd: repositoryRoot });
    await writeFile(path.join(repositoryRoot, "TOPIC.txt"), "topic\n", "utf8");
    await execFileAsync("git", ["add", "TOPIC.txt"], { cwd: repositoryRoot });
    await execFileAsync("git", ["commit", "-qm", "topic"], { cwd: repositoryRoot });
    await execFileAsync("git", ["switch", "-q", "main"], { cwd: repositoryRoot });
    await writeFile(path.join(repositoryRoot, "MAIN.txt"), "main\n", "utf8");
    await execFileAsync("git", ["add", "MAIN.txt"], { cwd: repositoryRoot });
    await execFileAsync("git", ["commit", "-qm", "main"], { cwd: repositoryRoot });

    const merged = await client.mergeSessionGit(session.id, "topic", randomUUID());
    assert.equal(merged.operationState, "none");
    assert.deepEqual(merged.conflictFiles, []);
    assert.equal(merged.head, (await execFileAsync("git", ["rev-parse", "HEAD"], {
      cwd: repositoryRoot,
    })).stdout.trim());

    await execFileAsync("git", ["switch", "-qc", "rebase-feature"], {
      cwd: repositoryRoot,
    });
    await writeFile(path.join(repositoryRoot, "REBASE.txt"), "feature\n", "utf8");
    await execFileAsync("git", ["add", "REBASE.txt"], { cwd: repositoryRoot });
    await execFileAsync("git", ["commit", "-qm", "rebase feature"], {
      cwd: repositoryRoot,
    });
    await execFileAsync("git", ["switch", "-q", "main"], { cwd: repositoryRoot });
    await writeFile(path.join(repositoryRoot, "BASE.txt"), "base\n", "utf8");
    await execFileAsync("git", ["add", "BASE.txt"], { cwd: repositoryRoot });
    await execFileAsync("git", ["commit", "-qm", "rebase base"], {
      cwd: repositoryRoot,
    });
    await execFileAsync("git", ["switch", "-q", "rebase-feature"], {
      cwd: repositoryRoot,
    });

    const rebased = await client.rebaseSessionGit(session.id, "main", randomUUID());
    assert.equal(rebased.operationState, "none");
    assert.equal(rebased.branch, "rebase-feature");
    assert.equal(rebased.head, (await execFileAsync("git", ["rev-parse", "HEAD"], {
      cwd: repositoryRoot,
    })).stdout.trim());
  } finally {
    await client.shutdown();
    await client.waitForExit();
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(repositoryRoot, { recursive: true, force: true });
    await rm(remoteRoot, { recursive: true, force: true });
    await rm(peerParent, { recursive: true, force: true });
  }
});

test("imports and manages closed Plugin Skill and MCP records", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const pluginRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-plugin-"));
  await mkdir(path.join(pluginRoot, "skills", "review"), { recursive: true });
  await writeFile(
    path.join(pluginRoot, "skills", "review", "SKILL.md"),
    "---\nname: review\ndescription: Review files.\n---\nInspect first.\n",
    "utf8",
  );
  await writeFile(path.join(pluginRoot, "server.py"), "# fixture\n", "utf8");
  await writeFile(path.join(pluginRoot, "plugin.json"), JSON.stringify({
    schemaVersion: 1,
    id: "desktop_fixture",
    name: "Desktop Fixture",
    version: "1.0.0",
    description: "Fixture",
    skills: [{ root: "skills/review" }],
    mcpServers: [{
      id: "fixture",
      executable: "python3",
      argv: ["server.py"],
      envNames: [],
      permissionProfile: "workspace_read",
      startupTimeoutSeconds: 5,
      toolTimeoutSeconds: 10,
      enabled: true,
    }],
  }), "utf8");

  try {
    const client = new RuntimeClient({
      pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
    });
    await client.initialize();
    const imported = await client.importPlugin(pluginRoot);
    await client.setPluginEnabled(imported.id, true);
    const skills = await client.listSkills();
    const servers = await client.listMcpServers();
    const enabled = await client.setMcpEnabled(imported.id, "fixture", true);
    const extensionSnapshot = await client.readExtensions();
    const extensionEvents = await client.readExtensionEvents(0);

    assert.equal((await client.listPlugins()).plugins[0]?.id, "desktop_fixture");
    assert.equal(skills.skills[0]?.qualifiedId, "desktop_fixture:review");
    assert.equal(servers.servers[0]?.permissionProfile, "workspace_read");
    assert.equal(enabled.consented, true);
    assert.equal(extensionSnapshot.plugins[0]?.id, "desktop_fixture");
    assert.ok(extensionEvents.items.some((event) => event.eventType === "mcp_server.state_changed"));
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(pluginRoot, { recursive: true, force: true });
  }
});

test("lists only configured models and keeps task model history during session mutations", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await createGitRepository("eidos-workspace-");
  await writeFile(path.join(workspaceRoot, "README.md"), "# Keep me\n", "utf8");
  await execFileAsync("git", ["add", "README.md"], { cwd: workspaceRoot });
  await execFileAsync("git", ["commit", "-qm", "fixture"], { cwd: workspaceRoot });

  try {
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "1" },
      onNotification: (notification) => {
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const models = await client.listModels();
    const session = await client.createSession(workspaceRoot);
    const started = await client.startRun(
      session.id, "Read README.md", "deepseek-v4-pro",
    );
    await withTimeout(runCompleted, 5_000);
    const listed = await client.listSessions();
    const renamed = await client.renameSession(session.id, "检查项目");
    const deleted = await client.deleteSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.deepEqual(models.models, []);
    assert.equal(models.defaultModelId, undefined);
    assert.equal(started.modelId, "deepseek-v4-pro");
    assert.equal(listed.items[0]?.taskStatus, "completed");
    assert.equal(renamed.title, "检查项目");
    assert.deepEqual(deleted, { deletedSessionId: session.id });
    assert.equal(await readFile(path.join(workspaceRoot, "README.md"), "utf8"), "# Keep me\n");
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("routes runtime notifications during a fake model read loop", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await createGitRepository("eidos-workspace-");
  const notifications: RuntimeNotification[] = [];

  try {
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "1" },
      onNotification: (notification) => {
        notifications.push(notification);
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const session = await client.createSession(workspaceRoot);
    const started = await client.startRun(session.id, "Read README.md", "deepseek-v4-flash");
    const completed = await withTimeout(runCompleted, 5_000);
    const snapshot = await client.readSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.equal(started.status, "running");
    assert.equal(completed.method, "run/completed");
    assert.equal(
      notifications.filter(
        (notification) => notification.method === "session/titleUpdated",
      ).length,
      1,
    );
    assert.deepEqual(
      notifications
        .filter(
          (notification) => notification.method !== "session/titleUpdated",
        )
        .map((notification) => notification.method),
      [
        "run/started",
        "item/started",
        "item/completed",
        "item/started",
        "item/completed",
        "run/updated",
        "item/started",
        "item/delta",
        "item/completed",
        "run/completed",
      ],
    );
    assert.equal(snapshot.runs[0]?.status, "succeeded");
    assert.equal(snapshot.session.title, "Fixture task");
    assert.deepEqual(
      snapshot.items.map((item) => item.kind),
      ["user_message", "tool_call", "assistant_message"],
    );
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("routes a runtime approval request and commits only after approval", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await createGitRepository("eidos-workspace-");
  const approvals: string[] = [];
  const approvalNotifications: string[] = [];

  try {
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "write" },
      onApprovalRequest: async (request) => {
        assert.equal(request.kind, "file_change");
        if (request.kind === "file_change") {
          approvals.push(request.diff);
        }
        return { decision: "approve" };
      },
      onNotification: (notification) => {
        if (notification.method.startsWith("approval/")) {
          approvalNotifications.push(notification.method);
        }
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const session = await client.createSession(workspaceRoot, { executionMode: "worktree" });
    const executionRoot = await managedWorktreeRoot(workspaceRoot);
    assert.notEqual(executionRoot, path.resolve(workspaceRoot));
    await client.startRun(session.id, "Create approved.txt", "deepseek-v4-flash");
    const completed = await withTimeout(runCompleted, 5_000);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.equal(completed.method, "run/completed");
    assert.equal(await readFile(path.join(executionRoot, "approved.txt"), "utf8"), "approved\n");
    await assert.rejects(readFile(path.join(workspaceRoot, "approved.txt"), "utf8"));
    assert.equal(approvals.length, 1);
    assert.deepEqual(
      approvalNotifications,
      ["approval/requested", "approval/resolved"],
    );
    assert.match(approvals[0] ?? "", /\+\+\+ b\/approved\.txt/);
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("cancel while awaiting approval ignores a late approve response", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await createGitRepository("eidos-workspace-");

  try {
    let resolveApproval: ((decision: { decision: "approve" }) => void) | undefined;
    let approvalStarted: (() => void) | undefined;
    const approvalRequested = new Promise<void>((resolve) => {
      approvalStarted = resolve;
    });
    const delayedApproval = new Promise<{ decision: "approve" }>((resolve) => {
      resolveApproval = resolve;
    });
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "write" },
      onApprovalRequest: async () => {
        approvalStarted?.();
        return delayedApproval;
      },
      onNotification: (notification) => {
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const session = await client.createSession(workspaceRoot, { executionMode: "worktree" });
    const executionRoot = await managedWorktreeRoot(workspaceRoot);
    const run = await client.startRun(session.id, "Create approved.txt", "deepseek-v4-flash");
    await withTimeout(approvalRequested, 5_000);
    await client.cancelRun(run.id);
    const completed = await withTimeout(runCompleted, 5_000);
    resolveApproval?.({ decision: "approve" });
    await new Promise<void>((resolve) => setImmediate(resolve));
    const snapshot = await client.readSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.equal(completed.method, "run/completed");
    assert.equal(completed.params.run.status, "canceled");
    assert.equal(snapshot.runs[0]?.status, "canceled");
    await assert.rejects(readFile(path.join(executionRoot, "approved.txt"), "utf8"));
    await assert.rejects(readFile(path.join(workspaceRoot, "approved.txt"), "utf8"));
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("degraded Shell capability rejects execution without approval or workspace mutation", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-shell-unavailable-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-shell-unavailable-workspace-"));
  const sentinel = path.join(workspaceRoot, "sentinel.txt");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(sentinel, "unchanged\n", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, sys",
      "request = json.loads(sys.stdin.readline())",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'protocolVersion':1,'runtimeVersion':'fixture','capabilities':{'runShell':False,'modelConfigured':False}}}), flush=True)",
      "request = json.loads(sys.stdin.readline())",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32000,'message':'Request failed','data':{'code':'SANDBOX_UNAVAILABLE','retryable':False}}}), flush=True)",
      "request = json.loads(sys.stdin.readline())",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':None}), flush=True)",
    ].join("\n"),
    "utf8",
  );

  try {
    const approvals: unknown[] = [];
    const client = new RuntimeClient({
      pythonExecutable,
      runtimeRoot,
      onApprovalRequest: async (request) => {
        approvals.push(request);
        return { decision: "approve" };
      },
    });
    const initialized = await client.initialize();
    assert.equal(initialized.capabilities.runShell, false);
    await assert.rejects(
      client.startRun("session-1", "Run printf", "deepseek-v4-flash"),
      (error: unknown) => (
        error instanceof RuntimeRequestError
        && error.businessCode === "SANDBOX_UNAVAILABLE"
      ),
    );
    assert.deepEqual(approvals, []);
    assert.equal(await readFile(sentinel, "utf8"), "unchanged\n");
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("routes shell approval and streams sandboxed command completion", async (context) => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await createGitRepository("eidos-workspace-");
  const approvals: string[] = [];
  let client: RuntimeClient | undefined;

  try {
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "shell" },
      onApprovalRequest: async (request) => {
        assert.equal(request.kind, "command_execution");
        if (request.kind === "command_execution") {
          approvals.push(request.command);
          assert.equal(request.networkEnabled, false);
        }
        return { decision: "approve" };
      },
      onNotification: (notification) => {
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    const initialized = await client.initialize();
    if (initialized.capabilities.runShell === false) {
      context.skip("real Seatbelt Self-Test is unavailable in this environment");
      await client.shutdown();
      assert.equal(await client.waitForExit(), 0);
      return;
    }
    const session = await client.createSession(workspaceRoot);
    await client.startRun(session.id, "Run printf", "deepseek-v4-flash");
    await withTimeout(runCompleted, 5_000);
    const snapshot = await client.readSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.deepEqual(approvals, ["printf desktop-shell-ok"]);
    const commandItem = snapshot.items.find((item) => item.kind === "command_execution");
    assert.ok(commandItem?.toolCall?.resultJson?.includes("desktop-shell-ok"));
  } finally {
    client?.terminate();
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(`${dataDirectory}-worktrees`, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortJson);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, sortJson(item)]),
    );
  }
  return value;
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error("run completion timed out")), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}

test("terminates a runtime that writes non-protocol stdout", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-invalid-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import sys",
      "sys.stdin.readline()",
      "print('not-json', flush=True)",
      "sys.stdin.read()",
    ].join("\n"),
    "utf8",
  );

  try {
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
    });

    await assert.rejects(
      client.initialize(),
      /Runtime wrote invalid JSON to stdout/,
    );
    await client.waitForExit();
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

test("uses the shared v1 vectors for requests, approvals, and notifications", async () => {
  const vectors = JSON.parse(await readFile(protocolV1Fixture, "utf8")) as {
    initialize: { request: object; response: object };
    shutdown: { request: object; response: object };
    approval: { request: object; approveResponse: object };
    notifications: object[];
    workspaceExplorer: { changedNotification: object };
  };
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-vector-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, pathlib, sys",
      `vectors = json.loads(pathlib.Path(${JSON.stringify(protocolV1Fixture)}).read_text(encoding='utf-8'))`,
      "def receive(expected):",
      "    actual = json.loads(sys.stdin.readline())",
      "    if actual != expected: raise SystemExit(2)",
      "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
      "receive(vectors['initialize']['request'])",
      "send(vectors['initialize']['response'])",
      "send(vectors['approval']['request'])",
      "receive(vectors['approval']['approveResponse'])",
      "for notification in vectors['notifications']: send(notification)",
      "send(vectors['workspaceExplorer']['changedNotification'])",
      "receive(vectors['shutdown']['request'])",
      "send(vectors['shutdown']['response'])",
    ].join("\n"),
    "utf8",
  );

  try {
    const notifications: RuntimeNotification[] = [];
    let approvalSeen: (() => void) | undefined;
    const approvalReceived = new Promise<void>((resolve) => {
      approvalSeen = resolve;
    });
    let runFinished: (() => void) | undefined;
    const runCompleted = new Promise<void>((resolve) => {
      runFinished = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
      onApprovalRequest: async () => {
        approvalSeen?.();
        return { decision: "approve" };
      },
      onNotification: (notification) => {
        notifications.push(notification);
        if (notification.method === "run/completed") {
          runFinished?.();
        }
      },
    });

    assert.equal((await client.initialize()).protocolVersion, 1);
    await withTimeout(approvalReceived, 5_000);
    await withTimeout(runCompleted, 5_000);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    assert.deepEqual(
      notifications.map((notification) => notification.method),
      [
        "run/started", "item/started", "item/delta", "item/completed", "run/completed",
        "workspace/changed",
      ],
    );
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

test("rejects an oversized unterminated frame before waiting for a newline", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-oversize-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import sys, time",
      "sys.stdin.readline()",
      "for _ in range(17):",
      "    sys.stdout.buffer.write(b'x' * (64 * 1024))",
      "    sys.stdout.buffer.flush()",
      "    time.sleep(0.005)",
      "time.sleep(5)",
    ].join("\n"),
    "utf8",
  );

  try {
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
    });
    await assert.rejects(
      withTimeout(client.initialize(), 3_000),
      /Runtime response exceeds 1 MiB/,
    );
    await client.waitForExit();
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

test("drains a bounded notification burst even when its consumer is slow", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-slow-consumer-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, pathlib, sys",
      `vectors = json.loads(pathlib.Path(${JSON.stringify(protocolV1Fixture)}).read_text(encoding='utf-8'))`,
      "json.loads(sys.stdin.readline())",
      "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
      "send(vectors['initialize']['response'])",
      "send(vectors['notifications'][0])",
      "send(vectors['notifications'][1])",
      "for sequence in range(1, 129):",
      "    message = json.loads(json.dumps(vectors['notifications'][2]))",
      "    message['params']['sequence'] = sequence",
      "    message['params']['delta'] = 'x' * 512",
      "    send(message)",
      "send(vectors['notifications'][3])",
      "send(vectors['notifications'][4])",
      "json.loads(sys.stdin.readline())",
      "send(vectors['shutdown']['response'])",
    ].join("\n"),
    "utf8",
  );

  try {
    let deltaCount = 0;
    let finish: (() => void) | undefined;
    const completed = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
      onNotification: (notification) => {
        const busyUntil = Date.now() + 1;
        while (Date.now() < busyUntil) {
          // Exercise bounded stdout backpressure with a deliberately slow callback.
        }
        if (notification.method === "item/delta") {
          deltaCount += 1;
        } else if (notification.method === "run/completed") {
          finish?.();
        }
      },
    });

    await client.initialize();
    await withTimeout(completed, 5_000);
    assert.equal(deltaCount, 128);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

test("projects Approval requests strictly, strips unknown fields, and respects message.id authority", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-approval-proj-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");

  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, sys",
      "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
      "json.loads(sys.stdin.readline())",
      "send({'jsonrpc':'2.0','id':'client-1','result':{'protocolVersion':1,'runtimeVersion':'0.3.0','capabilities':{'runShell':True,'modelConfigured':False}}})",
      "send({'jsonrpc':'2.0','id':'server-file-1','method':'item/requestApproval','params':{'id':'forged-id','sessionId':'s1','runId':'r1','itemId':'i1','toolCallId':'tc1','summary':'file summary','kind':'file_change','diff':'diff text','apiKey':'secret-key','environment':{'PRIVATE_TOKEN':'secret'}}})",
      "sys.stdin.readline()",
      "send({'jsonrpc':'2.0','id':'server-cmd-1','method':'item/requestApproval','params':{'id':'forged-id','sessionId':'s1','runId':'r1','itemId':'i2','toolCallId':'tc2','summary':'cmd summary','kind':'command_execution','command':'ls','cwd':'/tmp','networkEnabled':False,'timeoutSeconds':30,'token':'secret-token'}})",
      "sys.stdin.readline()",
      "send({'jsonrpc':'2.0','id':'server-ext-1','method':'item/requestApproval','params':{'id':'forged-id','sessionId':'s1','runId':'r1','itemId':'i3','toolCallId':'tc3','summary':'ext summary','kind':'external_tool','toolName':'my_tool','arguments':{'foo':'bar'},'provenance':{'kind':'mcp','sourceId':'srv1','sourceVersion':'1.0','contentHash':'h1','extraProvField':'secret'},'permissionProfile':'connector','timeoutSeconds':60,'envNames':['PATH'],'internalDiagnostics':{'stack':'path'}}})",
      "sys.stdin.readline()",
      "send({'jsonrpc':'2.0','id':'server-net-1','method':'item/requestApproval','params':{'id':'forged-id','sessionId':'s1','runId':'r1','itemId':'i4','toolCallId':'tc4','summary':'net summary','kind':'network_access','toolName':'fetch','hosts':['api.example.com'],'target':'https://api.example.com','secretHeader':'Bearer ...'}})",
      "sys.stdin.readline()",
      "json.loads(sys.stdin.readline())",
      "send({'jsonrpc':'2.0','id':'client-2','result':None})",
    ].join("\n"),
    "utf8",
  );

  const receivedRequests: Array<Record<string, unknown>> = [];
  try {
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
      onApprovalRequest: async (req) => {
        receivedRequests.push(req as unknown as Record<string, unknown>);
        return { decision: "approve" };
      },
    });

    await client.initialize();
    for (let i = 0; i < 50 && receivedRequests.length < 4; i += 1) {
      await new Promise((r) => setTimeout(r, 50));
    }

    assert.equal(receivedRequests.length, 4);

    // 1. file_change
    const req1 = receivedRequests[0]!;
    assert.equal(req1.id, "server-file-1");
    assert.equal(req1.kind, "file_change");
    assert.equal(req1.diff, "diff text");
    assert.equal(req1.apiKey, undefined);
    assert.equal(req1.environment, undefined);
    assert.deepEqual(Object.keys(req1).sort(), [
      "diff", "id", "itemId", "kind", "runId", "sessionId", "summary", "toolCallId",
    ]);

    // 2. command_execution
    const req2 = receivedRequests[1]!;
    assert.equal(req2.id, "server-cmd-1");
    assert.equal(req2.kind, "command_execution");
    assert.equal(req2.command, "ls");
    assert.equal(req2.networkEnabled, false);
    assert.equal(req2.token, undefined);
    assert.deepEqual(Object.keys(req2).sort(), [
      "additionalExecutableAccess", "additionalReadAccess", "additionalWriteAccess",
      "attemptOrdinal", "command", "cwd", "escalationReason", "executionMode",
      "id", "itemId", "kind", "networkEnabled", "reason", "runId",
      "sandboxPermissions", "sessionId", "summary", "timeoutSeconds", "toolCallId",
    ]);

    // 3. external_tool
    const req3 = receivedRequests[2]!;
    assert.equal(req3.id, "server-ext-1");
    assert.equal(req3.kind, "external_tool");
    assert.deepEqual(req3.arguments, { foo: "bar" });
    assert.deepEqual(req3.envNames, ["PATH"]);
    assert.equal(req3.internalDiagnostics, undefined);
    const prov = req3.provenance as Record<string, unknown>;
    assert.equal(prov.extraProvField, undefined);
    assert.deepEqual(Object.keys(req3).sort(), [
      "arguments", "envNames", "id", "itemId", "kind", "permissionProfile", "provenance", "runId", "sessionId", "summary", "timeoutSeconds", "toolCallId", "toolName",
    ]);

    // 4. network_access
    const req4 = receivedRequests[3]!;
    assert.equal(req4.id, "server-net-1");
    assert.equal(req4.kind, "network_access");
    assert.deepEqual(req4.hosts, ["api.example.com"]);
    assert.equal(req4.secretHeader, undefined);
    assert.deepEqual(Object.keys(req4).sort(), [
      "hosts", "id", "itemId", "kind", "runId", "sessionId", "summary", "target", "toolCallId", "toolName",
    ]);

    await client.shutdown();
    await client.waitForExit();
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

test("rejects ordinary ToolCall unknown provenance at the Runtime Client boundary", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-invalid-tool-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, sys",
      "request = json.loads(sys.stdin.readline())",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'protocolVersion':1,'runtimeVersion':'fixture','capabilities':{'runShell':False,'modelConfigured':False}}}), flush=True)",
      "request = json.loads(sys.stdin.readline())",
      "snapshot = {'session':{'id':'session-1','workspaceRoot':'/tmp','taskStatus':'new','createdAt':0,'updatedAt':0},'runs':[],'items':[{'id':'item-1','sessionId':'session-1','runId':'run-1','ordinal':0,'modelStepIndex':0,'kind':'tool_call','status':'completed','createdAt':0,'completedAt':1,'toolCall':{'id':'tool-1','itemId':'item-1','modelStepIndex':0,'batchOrder':0,'providerCallId':'provider-1','toolName':'mcp__server__tool','status':'completed','startedAt':0,'completedAt':1,'provenance':{'kind':'mcp','sourceId':'server-a','sourceVersion':'1','contentHash':'hash','apiKey':'secret','internalDiagnostics':{'path':'/private/path'}}}}],'throughEventId':1}",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':snapshot}), flush=True)",
      "sys.stdin.read()",
    ].join("\n"),
    "utf8",
  );

  try {
    const notifications: RuntimeNotification[] = [];
    const client = new RuntimeClient({
      pythonExecutable,
      runtimeRoot,
      onNotification: (notification) => notifications.push(notification),
    });
    await client.initialize();

    let snapshot: unknown;
    await assert.rejects(
      client.readSession("session-1").then((value) => { snapshot = value; }),
      /invalid result for session\/read/,
    );
    assert.equal(snapshot, undefined);
    assert.deepEqual(notifications, []);
    assert.notEqual(await client.waitForExit(), 0);
    await assert.rejects(client.health(), /Runtime (client is closed|process is not available)/);
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

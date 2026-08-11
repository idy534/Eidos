import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function pytest(args, reason) {
  return { command: "uv", args: ["run", "--locked", "pytest", ...args], reason };
}

function pnpm(script, reason) {
  return { command: "pnpm", args: [script], reason };
}

function addUnique(commands, command) {
  const key = [command.command, ...command.args].join("\u0000");
  if (!commands.some((item) => [item.command, ...item.args].join("\u0000") === key)) {
    commands.push(command);
  }
}

function hasPath(files, predicate) {
  return files.some(predicate);
}

export function selectAffectedCommands(changedFiles, options = {}) {
  const files = [...new Set(changedFiles.filter(Boolean).map((file) => file.replaceAll("\\", "/")))];
  const fallback = options.fallback ?? pnpm("test:fast", "No narrower affected-test mapping is available");
  const commands = [];

  if (files.length === 0) return [fallback];

  const runtimeTests = files
    .filter((file) => file.startsWith("runtime/tests/") && file.endsWith(".py"))
    .filter((file) => existsSync(path.join(root, file)));
  if (runtimeTests.length > 0) {
    addUnique(commands, pytest(runtimeTests, "Directly changed Runtime tests"));
  }

  const runtimeProductionFiles = files.filter((file) => file.startsWith("runtime/eidos_runtime/"));
  const mappedRuntimeFiles = new Set();

  function addRuntimeMapping(predicate, command) {
    const matched = runtimeProductionFiles.filter(predicate);
    if (matched.length === 0) return;
    matched.forEach((file) => mappedRuntimeFiles.add(file));
    addUnique(commands, command);
  }

  addRuntimeMapping(
    (file) => file.startsWith("runtime/eidos_runtime/model/"),
    pytest(["runtime/tests", "-k", "model or gateway or pydantic_ai"], "Runtime model changes"),
  );
  addRuntimeMapping(
    (file) => file.startsWith("runtime/eidos_runtime/context/"),
    pytest(["runtime/tests", "-k", "context or instruction or project_rule"], "Runtime context changes"),
  );
  addRuntimeMapping(
    (file) => file.startsWith("runtime/eidos_runtime/tools/"),
    pytest(["runtime/tests", "-k", "tool or shell or workspace"], "Runtime tool changes"),
  );
  addRuntimeMapping(
    (file) => file.startsWith("runtime/eidos_runtime/sandbox/"),
    pytest(["runtime/tests", "-k", "sandbox or seatbelt or shell or workspace"], "Runtime sandbox changes"),
  );
  addRuntimeMapping(
    (file) => file.startsWith("runtime/eidos_runtime/repo_intelligence/"),
    pytest(
      ["runtime/tests", "-k", "repository or repo or retrieval or inventory or index or watcher"],
      "Repository intelligence changes",
    ),
  );
  addRuntimeMapping(
    (file) => (
      file.startsWith("runtime/eidos_runtime/git/")
      || file.includes("/worktree")
      || file.includes("/checkpoint")
    ),
    pytest(["runtime/tests", "-k", "git or worktree or checkpoint"], "Git and Worktree changes"),
  );

  if (runtimeProductionFiles.some((file) => !mappedRuntimeFiles.has(file))) {
    addUnique(commands, pnpm("test:runtime:fast", "Runtime changes include files without a narrower mapping"));
  }

  if (hasPath(files, (file) => file.startsWith("desktop/main/"))) {
    addUnique(commands, pnpm("test:main", "Electron Main changes"));
  }
  if (hasPath(files, (file) => file.startsWith("desktop/renderer/") || file.startsWith("desktop/shared/"))) {
    addUnique(commands, pnpm("test:desktop", "Desktop contract or Renderer changes"));
  }

  if (hasPath(files, (file) => (
    file === "package.json"
    || file === "pnpm-lock.yaml"
    || file.startsWith(".github/")
    || file.startsWith("docs/")
    || file === "AGENTS.md"
    || file === "DEVELOPMENT.md"
    || file === "pyproject.toml"
    || file.startsWith("vitest")
    || file.startsWith("tsconfig")
  ))) {
    addUnique(commands, fallback);
  }

  if (hasPath(files, (file) => (
    file === "electron-builder.yml"
    || file.startsWith("scripts/package-")
    || file === "scripts/build-macos-runtime.sh"
    || file === "scripts/test-affected.mjs"
    || file === "scripts/test-affected.test.mjs"
    || file.endsWith("packaging-config.test.mjs")
    || file.endsWith("package-macos.test.mjs")
  ))) {
    addUnique(commands, pnpm("test:packaging", "Packaging or test-infrastructure changes"));
  }

  return commands.length > 0 ? commands : [fallback];
}

function runGit(args) {
  const result = spawnSync("git", args, { cwd: root, encoding: "utf8" });
  if (result.status !== 0) return "";
  return result.stdout.trim();
}

function mergeBase() {
  for (const ref of ["origin/main", "main", "HEAD^"]) {
    const base = runGit(["merge-base", ref, "HEAD"]);
    if (base) return base;
  }
  return "HEAD";
}

function changedFilesSinceBase() {
  const base = mergeBase();
  const committedDiff = runGit(["diff", "--name-only", base, "HEAD"]);
  const workingDiff = runGit(["diff", "--name-only", "HEAD"]);
  const untracked = runGit(["ls-files", "--others", "--exclude-standard"]);
  return [...new Set(`${committedDiff}\n${workingDiff}\n${untracked}`.split("\n").map((file) => file.trim()).filter(Boolean))];
}

function commandText({ command, args }) {
  return [command, ...args].join(" ");
}

export function runAffectedTests() {
  const changedFiles = changedFilesSinceBase();
  const commands = selectAffectedCommands(changedFiles);
  console.log(`test:affected: ${changedFiles.length} changed file(s)`);
  for (const selected of commands) {
    console.log(`test:affected: ${selected.reason}`);
    console.log(`test:affected: running ${commandText(selected)}`);
    const result = spawnSync(selected.command, selected.args, { cwd: root, stdio: "inherit" });
    if (result.error) {
      console.error(`test:affected: ${result.error.message}`);
      return 1;
    }
    if (result.status !== 0) return result.status ?? 1;
  }
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  process.exitCode = runAffectedTests();
}

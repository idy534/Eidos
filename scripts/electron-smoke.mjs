import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-electron-smoke-"));
const dataDirectory = path.join(temporaryRoot, "eidos-data");
const userDataDirectory = path.join(temporaryRoot, "electron-user-data");
const executable = path.join(root, "node_modules", ".bin", "electron");
const environment = {
  ...process.env,
  EIDOS_DATA_DIR: dataDirectory,
  EIDOS_ELECTRON_SMOKE: "1",
  EIDOS_PYTHON: process.env.EIDOS_PYTHON ?? path.join(root, ".venv", "bin", "python"),
};
delete environment.EIDOS_FAKE_MODEL;
delete environment.ELECTRON_RUN_AS_NODE;

let output = "";
try {
  const code = await new Promise((resolve, reject) => {
    const child = spawn(
      executable,
      [".", `--user-data-dir=${userDataDirectory}`],
      { cwd: root, env: environment, stdio: ["ignore", "pipe", "pipe"] },
    );
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`Electron smoke timed out.\n${output}`));
    }, 20_000);
    const capture = (chunk) => {
      output = (output + chunk.toString("utf8")).slice(-1024 * 1024);
    };
    child.stdout.on("data", capture);
    child.stderr.on("data", capture);
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("exit", (exitCode) => {
      clearTimeout(timer);
      resolve(exitCode);
    });
  });

  assert.equal(code, 0, output);
  assert.match(output, /Runtime initialized/);
  assert.match(output, /Runtime shutdown complete/);
  console.log("Electron smoke initialized Runtime and exited cleanly.");
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

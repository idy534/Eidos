import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {
  RuntimePathResolutionError,
  resolveRuntimePaths,
  type RuntimePathType,
} from "./runtime-paths.js";


function packagedFileTypes(paths: string[]): (candidate: string) => RuntimePathType {
  const available = new Set(paths);
  return (candidate) => available.has(candidate)
    ? candidate.endsWith("eidos_runtime") ? "directory" : "file"
    : "missing";
}


// These cases pin the macOS layout, so they name the platform and use POSIX
// joins; the Windows layout has its own case below. Both run on every host.
test("development runtime paths use the repository Python by default", () => {
  const paths = resolveRuntimePaths({
    isPackaged: false,
    platform: "darwin",
    appPath: "/workspace/eidos",
    resourcesPath: "/unused/resources",
    environment: {},
  });

  assert.deepEqual(paths, {
    pythonExecutable: path.posix.join("/workspace/eidos", ".venv", "bin", "python"),
    runtimeRoot: path.posix.join("/workspace/eidos", "runtime"),
  });
});


test("development runtime paths keep the repository runtime with EIDOS_PYTHON override", () => {
  const paths = resolveRuntimePaths({
    isPackaged: false,
    platform: "darwin",
    appPath: "/workspace/eidos",
    resourcesPath: "/unused/resources",
    environment: { EIDOS_PYTHON: "/custom/python" },
  });

  assert.deepEqual(paths, {
    pythonExecutable: "/custom/python",
    runtimeRoot: path.posix.join("/workspace/eidos", "runtime"),
  });
});


test("packaged runtime paths use Resources and never the app path or environment override", () => {
  const resourcesPath = "/Applications/Eidos.app/Contents/Resources";
  const runtimeRoot = path.posix.join(resourcesPath, "runtime");
  const paths = resolveRuntimePaths({
    isPackaged: true,
    platform: "darwin",
    appPath: "/Applications/Eidos.app/Contents/Resources/app.asar",
    resourcesPath,
    environment: { EIDOS_PYTHON: "/should/not/be-used" },
    pathType: packagedFileTypes([
      path.posix.join(runtimeRoot, "python", "bin", "python3"),
      path.posix.join(runtimeRoot, "app", "eidos_runtime"),
    ]),
  });

  assert.deepEqual(paths, {
    pythonExecutable: path.posix.join(runtimeRoot, "python", "bin", "python3"),
    runtimeRoot: path.posix.join(runtimeRoot, "app"),
  });
});


test("packaged runtime resolution fails closed when the bundled interpreter is missing", () => {
  assert.throws(
    () => resolveRuntimePaths({
      isPackaged: true,
      platform: "darwin",
      appPath: "/Applications/Eidos.app/Contents/Resources/app.asar",
      resourcesPath: "/Applications/Eidos.app/Contents/Resources",
      pathType: () => "missing",
    }),
    (error: unknown) => (
      error instanceof RuntimePathResolutionError
      && error.message.includes("bundled runtime unavailable")
      && error.message.includes("python/bin/python3")
      && error.message.includes("app/eidos_runtime")
    ),
  );
});

test("Windows selects the repository venv and the bundled interpreter from distinct paths", () => {
  const appPath = "C:\\Workspace with spaces\\Eidos";
  const resourcesPath = "C:\\Users\\测试\\AppData\\Local\\Programs\\Eidos\\resources";
  const development = resolveRuntimePaths({
    isPackaged: false, platform: "win32", appPath, resourcesPath,
  });
  assert.equal(development.pythonExecutable, `${appPath}\\.venv\\Scripts\\python.exe`);
  const runtimeRoot = `${resourcesPath}\\runtime`;
  const packaged = resolveRuntimePaths({
    isPackaged: true, platform: "win32", appPath, resourcesPath,
    environment: { EIDOS_PYTHON: "C:\\wrong\\python.exe" },
    pathType: packagedFileTypes([
      `${runtimeRoot}\\python\\python.exe`,
      `${runtimeRoot}\\app\\eidos_runtime`,
    ]),
  });
  assert.deepEqual(packaged, {
    pythonExecutable: `${runtimeRoot}\\python\\python.exe`,
    runtimeRoot: `${runtimeRoot}\\app`,
  });
});

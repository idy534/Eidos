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


test("development runtime paths use the repository Python by default", () => {
  const paths = resolveRuntimePaths({
    isPackaged: false,
    appPath: "/workspace/eidos",
    resourcesPath: "/unused/resources",
    environment: {},
  });

  assert.deepEqual(paths, {
    pythonExecutable: path.join("/workspace/eidos", ".venv", "bin", "python"),
    runtimeRoot: path.join("/workspace/eidos", "runtime"),
  });
});


test("development runtime paths keep the repository runtime with EIDOS_PYTHON override", () => {
  const paths = resolveRuntimePaths({
    isPackaged: false,
    appPath: "/workspace/eidos",
    resourcesPath: "/unused/resources",
    environment: { EIDOS_PYTHON: "/custom/python" },
  });

  assert.deepEqual(paths, {
    pythonExecutable: "/custom/python",
    runtimeRoot: path.join("/workspace/eidos", "runtime"),
  });
});


test("packaged runtime paths use Resources and never the app path or environment override", () => {
  const resourcesPath = "/Applications/Eidos.app/Contents/Resources";
  const runtimeRoot = path.join(resourcesPath, "runtime");
  const paths = resolveRuntimePaths({
    isPackaged: true,
    appPath: "/Applications/Eidos.app/Contents/Resources/app.asar",
    resourcesPath,
    environment: { EIDOS_PYTHON: "/should/not/be-used" },
    pathType: packagedFileTypes([
      path.join(runtimeRoot, "python", "bin", "python3"),
      path.join(runtimeRoot, "app", "eidos_runtime"),
    ]),
  });

  assert.deepEqual(paths, {
    pythonExecutable: path.join(runtimeRoot, "python", "bin", "python3"),
    runtimeRoot: path.join(runtimeRoot, "app"),
  });
});


test("packaged runtime resolution fails closed when the bundled interpreter is missing", () => {
  assert.throws(
    () => resolveRuntimePaths({
      isPackaged: true,
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

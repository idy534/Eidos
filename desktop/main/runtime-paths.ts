import { statSync } from "node:fs";
import path from "node:path";


export interface RuntimePaths {
  pythonExecutable: string;
  runtimeRoot: string;
}


export type RuntimePathType = "file" | "directory" | "missing";


export interface RuntimePathResolutionContext {
  isPackaged: boolean;
  appPath: string;
  resourcesPath: string;
  environment?: NodeJS.ProcessEnv;
  pathType?: (candidate: string) => RuntimePathType;
}


export class RuntimePathResolutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RuntimePathResolutionError";
  }
}


export function resolveRuntimePaths(
  context: RuntimePathResolutionContext,
): RuntimePaths {
  if (!context.isPackaged) {
    return {
      pythonExecutable: context.environment?.EIDOS_PYTHON
        ?? path.join(context.appPath, ".venv", "bin", "python"),
      runtimeRoot: path.join(context.appPath, "runtime"),
    };
  }

  const runtimeRoot = path.join(context.resourcesPath, "runtime");
  const pythonExecutable = path.join(runtimeRoot, "python", "bin", "python3");
  const packagedAppRoot = path.join(runtimeRoot, "app");
  const pathType = context.pathType ?? defaultPathType;
  const missing: string[] = [];

  if (pathType(pythonExecutable) !== "file") {
    missing.push("runtime/python/bin/python3");
  }
  if (pathType(path.join(packagedAppRoot, "eidos_runtime")) !== "directory") {
    missing.push("runtime/app/eidos_runtime");
  }
  if (missing.length > 0) {
    throw new RuntimePathResolutionError(
      `bundled runtime unavailable: ${missing.join(", ")}`,
    );
  }

  return { pythonExecutable, runtimeRoot: packagedAppRoot };
}


function defaultPathType(candidate: string): RuntimePathType {
  try {
    const metadata = statSync(candidate);
    if (metadata.isFile() && (metadata.mode & 0o111) !== 0) return "file";
    if (metadata.isDirectory()) return "directory";
    return "missing";
  } catch {
    return "missing";
  }
}

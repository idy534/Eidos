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
  platform?: NodeJS.Platform;
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
  const windows = (context.platform ?? process.platform) === "win32";
  const paths = windows ? path.win32 : path.posix;
  if (!context.isPackaged) {
    return {
      pythonExecutable: context.environment?.EIDOS_PYTHON
        ?? paths.join(context.appPath, ".venv", ...(windows ? ["Scripts", "python.exe"] : ["bin", "python"])),
      runtimeRoot: paths.join(context.appPath, "runtime"),
    };
  }

  const runtimeRoot = paths.join(context.resourcesPath, "runtime");
  const pythonParts = windows ? ["python", "python.exe"] : ["python", "bin", "python3"];
  const pythonExecutable = paths.join(runtimeRoot, ...pythonParts);
  const packagedAppRoot = paths.join(runtimeRoot, "app");
  const pathType = context.pathType ?? ((candidate) => defaultPathType(candidate, windows));
  const missing: string[] = [];

  if (pathType(pythonExecutable) !== "file") {
    missing.push(`runtime/${pythonParts.join("/")}`);
  }
  if (pathType(paths.join(packagedAppRoot, "eidos_runtime")) !== "directory") {
    missing.push("runtime/app/eidos_runtime");
  }
  if (missing.length > 0) {
    throw new RuntimePathResolutionError(
      `bundled runtime unavailable: ${missing.join(", ")}`,
    );
  }

  return { pythonExecutable, runtimeRoot: packagedAppRoot };
}


function defaultPathType(candidate: string, windows: boolean): RuntimePathType {
  try {
    const metadata = statSync(candidate);
    if (metadata.isFile() && (windows || (metadata.mode & 0o111) !== 0)) return "file";
    if (metadata.isDirectory()) return "directory";
    return "missing";
  } catch {
    return "missing";
  }
}

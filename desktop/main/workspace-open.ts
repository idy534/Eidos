import { realpath, stat } from "node:fs/promises";
import path from "node:path";


export async function resolveWorkspaceFileForOpen(
  workspaceRoot: string,
  relativePath: string,
): Promise<string> {
  if (
    !relativePath
    || relativePath.includes("\0")
    || path.isAbsolute(relativePath)
    || relativePath.split("/").some((part) => part === "" || part === "." || part === "..")
  ) {
    throw new Error("Workspace 文件参数无效。");
  }

  let canonicalRoot: string;
  let canonicalTarget: string;
  try {
    canonicalRoot = await realpath(workspaceRoot);
    canonicalTarget = await realpath(path.resolve(canonicalRoot, ...relativePath.split("/")));
  } catch {
    throw new Error("Workspace 文件不可用。");
  }
  const relation = path.relative(canonicalRoot, canonicalTarget);
  if (!relation || relation.startsWith("..") || path.isAbsolute(relation)) {
    throw new Error("Workspace 文件路径越界。");
  }
  try {
    if (!(await stat(canonicalTarget)).isFile()) throw new Error();
  } catch {
    throw new Error("Workspace 文件不可用。");
  }
  return canonicalTarget;
}

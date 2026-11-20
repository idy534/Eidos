export function isWindowsPlatform(
  userAgent = typeof navigator !== "undefined" ? navigator.userAgent : "",
): boolean {
  return userAgent.includes("Windows");
}

export const isWindows = isWindowsPlatform();

export const revealFileLabel = isWindows
  ? "在文件资源管理器中显示"
  : "在 Finder 中显示";

export function formatPlatformShortcut(macShortcut: string, windows = isWindowsPlatform()): string {
  if (!windows) return macShortcut;
  return macShortcut
    .replace(/^⌘\[$/, "Alt+←")
    .replace(/^⌘\]$/, "Alt+→")
    .replace(/⌘/g, "Ctrl+")
    .replace(/\bCmd\s*\+\s*/g, "Ctrl+")
    .replace(/\bCmd\s+/g, "Ctrl+");
}

export function currentOsUserLabel(windows = isWindowsPlatform()): string {
  return windows ? "当前 Windows 用户" : "当前 macOS 用户";
}

export function applyPlatformDataset(
  root: HTMLElement | null = typeof document !== "undefined" ? document.documentElement : null,
  windows = isWindowsPlatform(),
): void {
  if (!root) return;
  if (windows) {
    root.dataset.platform = "windows";
  } else {
    delete root.dataset.platform;
  }
}

export function pathBasename(path: string): string {
  const separator = isWindowsDrivePath(path) || (isWindows && !path.startsWith("/")) ? /[\\/]/ : "/";
  return path.split(separator).filter(Boolean).at(-1) ?? path;
}

export function isWindowsDrivePath(path: string): boolean {
  return /^[a-zA-Z]:[/\\]/.test(path);
}

export function isAbsoluteFilesystemPath(path: string): boolean {
  return (path.startsWith("/") && !path.startsWith("//")) || isWindowsDrivePath(path);
}

export function isPathWithinRoot(targetPath: string, rootPath: string): boolean {
  if (!targetPath || !rootPath) return false;
  if (isWindowsDrivePath(targetPath) && isWindowsDrivePath(rootPath)) {
    const normTarget = targetPath.replaceAll("\\", "/");
    const normRoot = rootPath.replaceAll("\\", "/").replace(/\/+$/, "");
    return normTarget.toLowerCase().startsWith(`${normRoot.toLowerCase()}/`);
  }
  const cleanRoot = rootPath.replace(/\/$/, "");
  return targetPath.startsWith(`${cleanRoot}/`);
}

export function joinWorkspacePath(rootPath: string, relativePath: string): string {
  if (!isWindowsDrivePath(rootPath)) {
    return `${rootPath.replace(/\/+$/, "")}/${relativePath.replace(/^\.?\/+/, "")}`;
  }
  const separator = rootPath.includes("\\") ? "\\" : "/";
  const cleanRoot = rootPath.replace(/[\\/]+$/, "");
  const cleanRelative = relativePath
    .replace(/^\.?[\\/]+/, "")
    .replaceAll(separator === "\\" ? "/" : "\\", separator);
  return `${cleanRoot}${separator}${cleanRelative}`;
}

import { execFileSync } from "node:child_process";
import { appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export function selectCiScopes(files, full = false) {
  const paths = [...new Set(files)];
  const global = full || paths.some((file) =>
    /^(package\.json|pnpm-lock\.yaml|pyproject\.toml|uv\.lock|tsconfig.*\.json|vite.*\.ts)$/.test(file)
    || file.startsWith(".github/") || file.startsWith("scripts/"));
  const runtime = global || paths.some((file) => file.startsWith("runtime/"));
  const shared = paths.some((file) => file.startsWith("desktop/shared/") || file.startsWith("runtime/eidos_runtime/protocol/"));
  const desktop = global || shared || paths.some((file) => file.startsWith("desktop/"));
  const native = global || paths.some((file) =>
    file.startsWith("runtime/eidos_runtime/") || file.startsWith("resources/")
    || file.startsWith("desktop/main/") || file.startsWith("desktop/shared/"));
  return { runtime: runtime || shared, desktop, native };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [base, head] = process.argv.slice(2);
  if (![base, head].every((sha) => /^[a-f0-9]{40}$/.test(sha ?? ""))) throw new Error("Expected two Git commit SHAs");
  const files = /^0+$/.test(base) ? [] : execFileSync("git", ["diff", "--no-renames", "--name-only", "-z", base, head], { encoding: "utf8" }).split("\0").filter(Boolean);
  const scopes = selectCiScopes(files, /^0+$/.test(base) || process.env.GITHUB_EVENT_NAME === "push");
  const output = Object.entries(scopes).map(([key, value]) => `${key}=${value}\n`).join("");
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, output);
  process.stdout.write(output);
}

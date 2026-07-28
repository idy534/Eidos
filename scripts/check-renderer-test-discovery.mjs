import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const config = JSON.parse(fs.readFileSync(path.join(root, "tsconfig.renderer-test.json"), "utf8"));
const requiredIncludes = [
  "desktop/renderer/src/**/*.test.ts",
  "desktop/renderer/src/**/*.test.tsx",
];
const requiredExcludes = [
  "desktop/renderer/src/**/*.behavior.test.ts",
  "desktop/renderer/src/**/*.behavior.test.tsx",
];

for (const pattern of requiredIncludes) {
  if (!config.include?.includes(pattern)) throw new Error(`Missing renderer test include: ${pattern}`);
}
for (const pattern of requiredExcludes) {
  if (!config.exclude?.includes(pattern)) throw new Error(`Missing renderer test exclude: ${pattern}`);
}
if (config.include.some((pattern) => /\.test\.tsx?$/.test(pattern) && !pattern.includes("**"))) {
  throw new Error("Renderer pure-test discovery must not list individual test files");
}

const compiled = spawnSync(
  process.platform === "win32" ? "pnpm.cmd" : "pnpm",
  ["exec", "tsc", "-p", "tsconfig.renderer-test.json"],
  { cwd: root, encoding: "utf8" },
);
if (compiled.status !== 0) {
  process.stderr.write(compiled.stdout);
  process.stderr.write(compiled.stderr);
  process.exit(compiled.status ?? 1);
}

const sourceRoot = path.join(root, "desktop/renderer/src");
const outputRoot = path.join(root, "dist/renderer-test/renderer/src");
const sourceTests = walk(sourceRoot)
  .filter((file) => /\.test\.tsx?$/.test(file) && !/\.behavior\.test\.tsx?$/.test(file))
  .map((file) => path.relative(sourceRoot, file).replace(/\.tsx?$/, ".js"))
  .sort();
const compiledTests = walk(outputRoot)
  .filter((file) => /\.test\.js$/.test(file) && !/\.behavior\.test\.js$/.test(file))
  .map((file) => path.relative(outputRoot, file))
  .sort();

const missing = sourceTests.filter((file) => !compiledTests.includes(file));
const stale = compiledTests.filter((file) => !sourceTests.includes(file));
if (missing.length || stale.length) {
  throw new Error(`Renderer test discovery mismatch; missing=${missing.join(",")}; stale=${stale.join(",")}`);
}
console.log(`Renderer pure-test discovery passed: ${sourceTests.length} source, ${compiledTests.length} compiled.`);

function walk(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(target) : [target];
  });
}

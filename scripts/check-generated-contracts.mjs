// Regenerate every Desktop contract from the Python DTOs and compare the result
// with the committed desktop/shared/*.generated.ts files.
//
//   node scripts/check-generated-contracts.mjs          fail on drift, leave the tree as it was
//   node scripts/check-generated-contracts.mjs --write  keep the regenerated files
import { spawn } from "node:child_process";
import { readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const shared = path.join(root, "desktop/shared");
const PARALLEL_GENERATORS = 4;

function generatedFiles(directory) {
  return readdirSync(directory).filter((name) => name.endsWith(".generated.ts")).sort();
}

function snapshot(directory) {
  return new Map(generatedFiles(directory).map((name) => {
    const file = path.join(directory, name);
    return [name, { text: readFileSync(file, "utf8"), modified: statSync(file, { bigint: true }).mtimeNs }];
  }));
}

function runGenerator(script) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [script], { cwd: root, stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => resolve({ script, error: String(error) }));
    child.on("close", (code) => resolve(code === 0 ? { script } : { script, error: stderr.trim() || `exit ${code}` }));
  });
}

/** Compare two snapshots. A file no generator rewrote keeps its old modification time. */
export function compareSnapshots(before, after) {
  const changed = [...after].filter(([name, value]) => before.has(name) && before.get(name).text !== value.text).map(([name]) => name);
  const added = [...after.keys()].filter((name) => !before.has(name));
  const orphaned = [...after].filter(([name, value]) => before.get(name)?.modified === value.modified).map(([name]) => name);
  return { changed, added, orphaned };
}

export async function checkGeneratedContracts({ write = false } = {}) {
  const generators = readdirSync(path.join(root, "scripts"))
    .filter((name) => /^generate-.+-contracts\.mjs$/.test(name)).sort()
    .map((name) => path.join(root, "scripts", name));
  const before = snapshot(shared);
  const failed = [];
  const queue = [...generators];
  await Promise.all(Array.from({ length: PARALLEL_GENERATORS }, async () => {
    for (let script = queue.shift(); script; script = queue.shift()) {
      const result = await runGenerator(script);
      if (result.error) failed.push(`${path.basename(result.script)}: ${result.error}`);
    }
  }));
  const result = { ...compareSnapshots(before, snapshot(shared)), failed, generators: generators.length };
  if (!write) {
    for (const name of result.changed) writeFileSync(path.join(shared, name), before.get(name).text);
    for (const name of result.added) rmSync(path.join(shared, name));
  }
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const write = process.argv.includes("--write");
  const { changed, added, orphaned, failed, generators } = await checkGeneratedContracts({ write });
  const problems = [
    ...failed.map((message) => `generator failed: ${message}`),
    ...orphaned.map((name) => `desktop/shared/${name} is not written by any generator`),
    ...(write ? [] : [
      ...changed.map((name) => `desktop/shared/${name} differs from the Python DTOs`),
      ...added.map((name) => `desktop/shared/${name} is generated but not committed`),
    ]),
  ];
  if (problems.length > 0) {
    console.error(problems.join("\n"));
    if (!write && changed.length + added.length > 0) {
      console.error("Run `node scripts/check-generated-contracts.mjs --write` and commit the result.");
    }
    process.exit(1);
  }
  console.log(write
    ? `Regenerated contracts from ${generators} generators (${changed.length} changed, ${added.length} added).`
    : `Generated Desktop contracts match the Python DTOs (${generators} generators).`);
}

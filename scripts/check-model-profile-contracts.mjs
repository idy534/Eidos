import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { generateModelProfileTypes } from "./generate-model-profile-contracts.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const committedSchemaPath = path.join(
  projectRoot,
  "contracts/generated/model-profile.schema.json",
);
const committedTypePath = path.join(
  projectRoot,
  "desktop/shared/generated/runtime/model-profile.ts",
);
const runtimePath = path.join(projectRoot, "runtime");

function run(command, args) {
  return new Promise((resolve, reject) => {
    execFile(command, args, {
      cwd: projectRoot,
      env: {
        ...process.env,
        PYTHONPATH: [runtimePath, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      },
    }, (error, _stdout, stderr) => {
      if (error) {
        reject(new Error(`${command} ${args.join(" ")} failed:\n${stderr}`));
        return;
      }
      resolve();
    });
  });
}

async function assertSameFile(generatedPath, committedPath) {
  const [generated, committed] = await Promise.all([
    readFile(generatedPath),
    readFile(committedPath),
  ]);
  return generated.equals(committed);
}

async function main() {
  const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-model-contract-check-"));
  const schemaPath = path.join(temporaryDirectory, "model-profile.schema.json");
  const typePath = path.join(temporaryDirectory, "model-profile.ts");

  try {
    await run("uv", [
      "run", "--locked", "python", "-m", "eidos_runtime.contracts.export_model_profile",
      "--output", schemaPath,
    ]);
    await generateModelProfileTypes({ schemaPath, outputPath: typePath });
    const current = await Promise.all([
      assertSameFile(schemaPath, committedSchemaPath),
      assertSameFile(typePath, committedTypePath),
    ]);
    if (!current.every(Boolean)) {
      throw new Error(
        "Generated Model Profile contracts are out of date.\nRun: pnpm generate:contracts:model-profile",
      );
    }
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

await main();

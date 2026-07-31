import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { compileFromFile } from "json-schema-to-typescript";

export const GENERATED_HEADER = [
  "/**",
  " * Generated from Eidos Runtime Pydantic models.",
  " * Do not edit manually.",
  " */",
].join("\n");

export async function generateModelProfileTypes({ schemaPath, outputPath }) {
  const generated = await compileFromFile(schemaPath, {
    additionalProperties: false,
    bannerComment: GENERATED_HEADER,
    declareExternallyReferenced: true,
    enableConstEnums: false,
    format: true,
    ignoreMinAndMaxItems: false,
    inferStringEnumKeysFromValues: false,
    strictIndexSignatures: true,
    style: { singleQuote: true },
    unreachableDefinitions: true,
    unknownAny: true,
  });

  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, generated.replaceAll("\r\n", "\n"), "utf8");
}

function parseArguments(argv) {
  const values = {
    schemaPath: "contracts/generated/model-profile.schema.json",
    outputPath: "desktop/shared/generated/runtime/model-profile.ts",
  };
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if ((flag !== "--schema" && flag !== "--output") || value === undefined) {
      throw new Error("Usage: node scripts/generate-model-profile-contracts.mjs [--schema path] [--output path]");
    }
    values[flag === "--schema" ? "schemaPath" : "outputPath"] = value;
  }
  return values;
}

async function main() {
  await generateModelProfileTypes(parseArguments(process.argv.slice(2)));
}

if (
  process.argv[1] !== undefined
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  await main();
}

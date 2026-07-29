import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const checker = path.join(root, "scripts/check-desktop-contracts.mjs");

function parseTypeScript(relativePath) {
  const filePath = path.join(root, relativePath);
  return ts.createSourceFile(
    filePath,
    readFileSync(filePath, "utf8"),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
}

function descendants(node, predicate) {
  const found = [];
  function visit(current) {
    if (predicate(current)) found.push(current);
    ts.forEachChild(current, visit);
  }
  visit(node);
  return found;
}

function interfaceFields(sourceFile, name) {
  const declaration = sourceFile.statements.find(
    (node) => ts.isInterfaceDeclaration(node) && node.name.text === name,
  );
  assert.ok(declaration, `missing TypeScript interface ${name}`);
  return declaration.members
    .filter(ts.isPropertySignature)
    .map((member) => member.name.getText(sourceFile).replaceAll(/['"]/g, ""));
}

function validatorFields(sourceFile, name) {
  const declaration = sourceFile.statements.find(
    (node) => ts.isFunctionDeclaration(node) && node.name?.text === name,
  );
  assert.ok(declaration, `missing TypeScript validator ${name}`);
  const arrays = descendants(
    declaration,
    (node) => (
      ts.isCallExpression(node)
      && node.expression.getText(sourceFile) === "hasOnlyKeys"
      && ts.isArrayLiteralExpression(node.arguments[1])
    ),
  ).map((call) => call.arguments[1].elements.map((element) => element.text));
  assert.equal(arrays.length, 1, `${name} must have one exact-key list`);
  return arrays[0];
}

test("Python DTOs, TypeScript interfaces, and validators share key fields", () => {
  const python = process.env.EIDOS_PYTHON ?? path.join(root, ".venv", "bin", "python");
  const dtoFields = JSON.parse(execFileSync(
    python,
    [
      "-c",
      "import json; from eidos_runtime.protocol import schemas; "
        + "print(json.dumps({name: [field.alias or key for key, field in "
        + "getattr(schemas, name).model_fields.items()] for name in "
        + "('SessionDto','RunDto','ItemDto','ToolCallDto','PluginRecordDto',"
        + "'SkillMetadataDto','McpServerRecordDto')}))",
    ],
    { cwd: path.join(root, "runtime"), encoding: "utf8" },
  ));
  const contracts = parseTypeScript("desktop/shared/domain-contracts.ts");
  const validators = parseTypeScript("desktop/main/runtime-client.ts");
  const mappings = [
    ["SessionDto", "Session", "isSession"],
    ["RunDto", "Run", "isRun"],
    ["ItemDto", "Item", "isItem"],
    ["ToolCallDto", "ToolCall", "isToolCall"],
    ["PluginRecordDto", "PluginRecord", "isPluginRecord"],
    ["SkillMetadataDto", "SkillMetadata", "isSkillMetadata"],
    ["McpServerRecordDto", "McpServerRecord", "isMcpServerRecord"],
  ];

  for (const [dto, contract, validator] of mappings) {
    const expected = [...dtoFields[dto]].sort();
    const interfaceKeys = interfaceFields(contracts, contract);
    const validatorKeys = validatorFields(validators, validator);
    assert.equal(new Set(interfaceKeys).size, interfaceKeys.length, `${contract} has duplicate fields`);
    assert.equal(new Set(validatorKeys).size, validatorKeys.length, `${validator} has duplicate fields`);
    assert.deepEqual([...interfaceKeys].sort(), expected, `${contract} drifted from ${dto}`);
    assert.deepEqual([...validatorKeys].sort(), expected, `${validator} drifted from ${dto}`);
  }

  assert.deepEqual(
    [...interfaceFields(contracts, "SessionListResult")].sort(),
    ["items", "nextCursor"],
  );
  assert.deepEqual(
    [...validatorFields(validators, "isSessionListResult")].sort(),
    ["items", "nextCursor"],
  );
});

function rejectsMutation(relativePath, mutate, expectedError) {
  const fixtureRoot = mkdtempSync(path.join(tmpdir(), "eidos-contracts-"));
  try {
    cpSync(path.join(root, "desktop"), path.join(fixtureRoot, "desktop"), {
      recursive: true,
    });
    cpSync(
      path.join(root, "tsconfig.renderer-test.json"),
      path.join(fixtureRoot, "tsconfig.renderer-test.json"),
    );
    const target = path.join(fixtureRoot, relativePath);
    const source = readFileSync(target, "utf8");
    const mutated = mutate(source);
    assert.notEqual(mutated, source, `fixture mutation did not change ${relativePath}`);
    writeFileSync(target, mutated);
    const result = spawnSync(process.execPath, [checker, "--root", fixtureRoot], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0, result.stdout);
    assert.match(`${result.stdout}\n${result.stderr}`, expectedError);
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
}

test("Approval projection rejects an extra explicit field", () => {
  rejectsMutation(
    "desktop/main/runtime-client.ts",
    (source) => source.replace(
      "      diff: params.diff as string,\n",
      "      diff: params.diff as string,\n      secret: params.secret,\n",
    ),
    /file_change.*exact fields/,
  );
});

test("Approval clearing rejects the run notification parent branch", () => {
  rejectsMutation(
    "desktop/renderer/src/app/AppShell.tsx",
    (source) => source.replace(
      '        if (notification.method === "run/completed") {\n'
        + "          approvalActions.clearApprovalsForRun(run.id);\n",
      "        approvalActions.clearApprovalsForRun(run.id);\n"
        + '        if (notification.method === "run/completed") {\n',
    ),
    /clearApprovalsForRun.*run\/completed/,
  );
});

test("Approval removal rejects an unconditional notification block", () => {
  rejectsMutation(
    "desktop/renderer/src/app/AppShell.tsx",
    (source) => source
      .replace(
        '      if (notification.method === "session/titleUpdated") {\n',
        "      approvalActions.removeApproval(notification.params.approvalId);\n"
          + '      if (notification.method === "session/titleUpdated") {\n',
      )
      .replace(
        "        approvalActions.removeApproval(notification.params.approvalId);\n",
        "",
      ),
    /removeApproval.*approval\/resolved.*approval\/canceled/,
  );
});

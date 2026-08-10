import assert from "node:assert/strict";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "scripts", "package-macos.sh");


function run(args, environment = process.env) {
  return new Promise((resolve, reject) => {
    const child = spawn("bash", [script, ...args], {
      cwd: root,
      env: environment,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk) => { output += chunk.toString("utf8"); });
    child.once("error", reject);
    child.once("close", (code) => resolve({ code, output }));
  });
}


test("packaging script rejects an invalid mode before doing build work", async () => {
  const result = await run(["invalid"]);
  assert.notEqual(result.code, 0);
  assert.match(result.output, /invalid|unsupported mode/i);
});


test("packaging script rejects a non-arm64 host", async () => {
  const fakeBin = await mkdtemp(path.join(os.tmpdir(), "eidos-package-platform-test-"));
  const fakeUname = path.join(fakeBin, "uname");
  await writeFile(fakeUname, "#!/bin/sh\nif [ \"$1\" = \"-s\" ]; then echo Linux; else echo x86_64; fi\n", "utf8");
  await chmod(fakeUname, 0o755);
  try {
    const result = await run(["local"], {
      ...process.env,
      PATH: `${fakeBin}:/usr/bin:/bin`,
    });
    assert.notEqual(result.code, 0);
    assert.match(result.output, /Darwin.*arm64|macOS.*arm64/i);
  } finally {
    await rm(fakeBin, { recursive: true, force: true });
  }
});


test("release packaging rejects missing signing and notarization credentials", async () => {
  const environment = { ...process.env };
  for (const key of [
    "CSC_LINK",
    "CSC_KEY_PASSWORD",
    "CSC_NAME",
    "APPLE_API_KEY",
    "APPLE_API_KEY_ID",
    "APPLE_API_ISSUER",
    "APPLE_ID",
    "APPLE_APP_SPECIFIC_PASSWORD",
    "APPLE_TEAM_ID",
    "APPLE_KEYCHAIN",
    "APPLE_KEYCHAIN_PROFILE",
  ]) {
    delete environment[key];
  }
  const result = await run(["release"], environment);
  assert.notEqual(result.code, 0);
  assert.match(result.output, /Developer ID|CSC_LINK|notarization|APPLE_/i);
});

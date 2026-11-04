import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { RuntimeClient } from "./runtime-client.js";
import type { RuntimeNotification } from "./runtime-client.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const fixture = path.join(root, "protocol/fixtures/run-thinking-v1.json");
const v1Fixture = path.join(root, "protocol/fixtures/v1.json");
const python = process.env.EIDOS_PYTHON ?? path.join(root, ".venv/bin/python");

void test("run/thinking reaches Main as a live-only notification", { timeout: 15000 }, async () => {
  const vectors = JSON.parse(await readFile(fixture, "utf8")) as Record<string, object>;
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-thinking-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(path.join(packageRoot, "__main__.py"), [
    "import json, sys",
    `vectors = json.load(open(${JSON.stringify(fixture)}, encoding='utf-8'))`,
    `v1 = json.load(open(${JSON.stringify(v1Fixture)}, encoding='utf-8'))`,
    "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
    "sys.stdin.readline()",
    "send(v1['initialize']['response'])",
    "send(vectors['notification'])",
    "send(vectors['titleOnlyNotification'])",
    "sys.stdin.readline()",
  ].join("\n"), "utf8");
  const received: RuntimeNotification[] = [];
  const client = new RuntimeClient({
    pythonExecutable: python,
    runtimeRoot,
    onNotification: (notification) => { received.push(notification); },
  });
  try {
    await client.initialize();
    for (let attempt = 0; attempt < 100 && received.length < 2; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.deepEqual(received, [vectors.notification, vectors.titleOnlyNotification]);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

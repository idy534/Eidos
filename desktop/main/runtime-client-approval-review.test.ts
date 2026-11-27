import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { RuntimeClient } from "./runtime-client.js";
import type { RuntimeNotification } from "./runtime-client.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const progressFixture = path.join(root, "protocol", "fixtures", "run-approval-review-progress-v1.json");
const v1Fixture = path.join(root, "protocol", "fixtures", "v1.json");
const python = process.env.EIDOS_PYTHON
  ?? (process.platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python"));

const validItemUpdated: { jsonrpc: "2.0" } & RuntimeNotification = {
  jsonrpc: "2.0",
  method: "item/updated",
  params: {
    sessionId: "session-1",
    runId: "run-1",
    item: {
      id: "item-1",
      sessionId: "session-1",
      runId: "run-1",
      ordinal: 1,
      modelStepIndex: 0,
      kind: "command_execution",
      status: "completed",
      createdAt: 1000,
      completedAt: 1500,
      toolCall: {
        id: "tool-1",
        itemId: "item-1",
        modelStepIndex: 0,
        batchOrder: 0,
        providerCallId: "call-1",
        toolName: "run_shell",
        status: "completed",
        startedAt: 1000,
        completedAt: 1500,
        approvalStatus: "resolved",
        approvalDecision: "approve",
        approvalReview: {
          source: "model",
          decision: "approve",
          reasonCode: "auto_review_approved",
          rationale: "Read-only probe requested by user.",
          risk: "low",
          durationMs: 420,
        },
      },
    },
  },
};

void test("run/approvalReviewProgress and ToolCall.approvalReview reach Main when valid", { timeout: 15000 }, async () => {
  const vectors = JSON.parse(await readFile(progressFixture, "utf8")) as Record<string, object>;
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-approval-review-valid-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");

  const validMessages = [
    vectors.preparingNotification,
    vectors.evaluatingNotification,
    validItemUpdated,
    vectors.finishedNotification,
  ];

  await writeFile(path.join(packageRoot, "__main__.py"), [
    "import json, sys",
    `v1 = json.load(open(${JSON.stringify(v1Fixture)}, encoding='utf-8'))`,
    `stream = json.loads(${JSON.stringify(JSON.stringify(validMessages))})`,
    "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
    "sys.stdin.readline()",
    "send(v1['initialize']['response'])",
    "for msg in stream: send(msg)",
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
    for (let attempt = 0; attempt < 100 && received.length < validMessages.length; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.deepEqual(received, validMessages);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

void test("malformed run/approvalReviewProgress and approvalReview fail closed in Main", { timeout: 20000 }, async () => {
  const malformedCases: Array<{ name: string; message: unknown }> = [
    {
      name: "unknown stage",
      message: {
        jsonrpc: "2.0",
        method: "run/approvalReviewProgress",
        params: {
          sessionId: "session-1",
          runId: "run-1",
          itemId: "item-1",
          stage: "unknown_stage",
          stepIndex: 1,
          totalSteps: 3,
          actionTitle: "检查",
        },
      },
    },
    {
      name: "extra property on progress",
      message: {
        jsonrpc: "2.0",
        method: "run/approvalReviewProgress",
        params: {
          sessionId: "session-1",
          runId: "run-1",
          itemId: "item-1",
          stage: "preparing",
          stepIndex: 1,
          totalSteps: 3,
          actionTitle: "检查",
          extraField: true,
        },
      },
    },
    {
      name: "stepIndex > totalSteps",
      message: {
        jsonrpc: "2.0",
        method: "run/approvalReviewProgress",
        params: {
          sessionId: "session-1",
          runId: "run-1",
          itemId: "item-1",
          stage: "evaluating",
          stepIndex: 4,
          totalSteps: 3,
          actionTitle: "检查",
        },
      },
    },
    {
      name: "empty actionTitle",
      message: {
        jsonrpc: "2.0",
        method: "run/approvalReviewProgress",
        params: {
          sessionId: "session-1",
          runId: "run-1",
          itemId: "item-1",
          stage: "evaluating",
          stepIndex: 2,
          totalSteps: 3,
          actionTitle: "",
        },
      },
    },
    {
      name: "invalid approvalReview source",
      message: {
        jsonrpc: "2.0",
        method: "item/updated",
        params: {
          ...validItemUpdated.params,
          item: {
            ...validItemUpdated.params.item,
            toolCall: {
              ...validItemUpdated.params.item.toolCall!,
              approvalReview: {
                ...validItemUpdated.params.item.toolCall!.approvalReview!,
                source: "auto_review",
              },
            },
          },
        },
      },
    },
    {
      name: "negative durationMs",
      message: {
        jsonrpc: "2.0",
        method: "item/updated",
        params: {
          ...validItemUpdated.params,
          item: {
            ...validItemUpdated.params.item,
            toolCall: {
              ...validItemUpdated.params.item.toolCall!,
              approvalReview: {
                source: "model",
                decision: "reject",
                reasonCode: "auto_review_rejected",
                rationale: "Blocked.",
                risk: "high",
                durationMs: -1,
              },
            },
          },
        },
      },
    },
    {
      name: "missing risk",
      message: {
        jsonrpc: "2.0",
        method: "item/updated",
        params: {
          ...validItemUpdated.params,
          item: {
            ...validItemUpdated.params.item,
            toolCall: {
              ...validItemUpdated.params.item.toolCall!,
              approvalReview: {
                source: "model",
                decision: "reject",
                reasonCode: "auto_review_rejected",
                rationale: "Blocked.",
              },
            },
          },
        },
      },
    },
    {
      name: "extra property on approvalReview",
      message: {
        jsonrpc: "2.0",
        method: "item/updated",
        params: {
          ...validItemUpdated.params,
          item: {
            ...validItemUpdated.params.item,
            toolCall: {
              ...validItemUpdated.params.item.toolCall!,
              approvalReview: {
                source: "model",
                decision: "approve",
                reasonCode: "auto_review_approved",
                rationale: "Safe.",
                risk: "low",
                unexpected: 1,
              },
            },
          },
        },
      },
    },
  ];

  for (const testCase of malformedCases) {
    const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-approval-review-bad-"));
    const packageRoot = path.join(runtimeRoot, "eidos_runtime");
    await mkdir(packageRoot);
    await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
    await writeFile(path.join(packageRoot, "__main__.py"), [
      "import json, sys",
      `msg = json.loads(${JSON.stringify(JSON.stringify(testCase.message))})`,
      "sys.stdin.readline()",
      "print(json.dumps(msg, separators=(',', ':')), flush=True)",
      "sys.stdin.readline()",
    ].join("\n"), "utf8");

    const received: RuntimeNotification[] = [];
    const client = new RuntimeClient({
      pythonExecutable: python,
      runtimeRoot,
      onNotification: (notification) => { received.push(notification); },
    });
    try {
      await assert.rejects(
        () => client.initialize(),
        /Runtime wrote an invalid JSON-RPC message/,
        testCase.name,
      );
      assert.equal(received.length, 0, `${testCase.name} must not deliver notification`);
    } finally {
      client.terminate();
      await client.waitForExit().catch(() => undefined);
      await rm(runtimeRoot, { recursive: true, force: true });
    }
  }
});

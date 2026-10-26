import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RuntimeClient, type RuntimeNotification } from "./runtime-client.js";

for (const mode of ["read", "off"] as const) {
  test(`memory replacement uses Desktop file approval in ${mode} mode`, async () => {
    const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-memory-approval-"));
    const runtimeRoot = path.join(temp, "runtime");
    const pkg = path.join(runtimeRoot, "eidos_runtime");
    await mkdir(pkg, { recursive: true });
    const realPackage = path.resolve("runtime/eidos_runtime");
    await writeFile(path.join(pkg, "__init__.py"),
      (await readFile(path.join(realPackage, "__init__.py"), "utf8"))
      + `\n__path__.append(${JSON.stringify(realPackage)})\n`);
    await writeFile(path.join(pkg, "__main__.py"), [
      "from eidos_runtime.protocol import server",
      "from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel",
      "responses=[]",
      ...(mode === "read" ? [
        "responses.append(ModelResponse(tool_calls=(ModelToolCall('read-note','memory_read',{'path':'profile.md'}),)))",
      ] : []),
      "responses.append(ModelResponse(tool_calls=(ModelToolCall('replace-note','memory_edit',{'path':'profile.md','content':'明确替换后的正文。'}),)))",
      "responses.append(ModelResponse(text='保存完成'))",
      "server._model_from_environment=lambda: ScriptedModel(responses)",
      "raise SystemExit(server.run())",
    ].join("\n"));
    let complete!: (notification: RuntimeNotification) => void;
    const completed = new Promise<RuntimeNotification>((resolve) => { complete = resolve; });
    let approvals = 0;
    const client = new RuntimeClient({
      pythonExecutable: path.resolve(".venv/bin/python"),
      runtimeRoot,
      dataDirectory: path.join(temp, "data"),
      environment: { EIDOS_FAKE_MODEL: "1", EIDOS_PROJECTLESS_ROOT: path.join(temp, "workspaces") },
      onApprovalRequest: async (request) => {
        approvals++;
        assert.equal(request.kind, "file_change");
        assert.match(request.summary, /memory\/current\/profile\.md/);
        if (request.kind === "file_change") {
          assert.match(request.diff, /\+明确替换后的正文。/);
          if (mode === "off") assert.doesNotMatch(request.diff, /旧正文/);
          else assert.match(request.diff, /-旧正文/);
        }
        return { decision: "approve" };
      },
      onNotification: (notification) => {
        if (notification.method === "run/completed") complete(notification);
      },
    });
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await client.initialize();
      const session = await client.createSession(null);
      await client.memory("memory/edit", {
        sessionId: session.id, operationId: randomUUID(), path: "profile.md",
        content: "旧正文", kind: "user", expectedRevision: 0,
      });
      await client.memory("memory/policy", { sessionId: session.id, scope: "current", mode });
      await client.startRun(session.id, "请将这篇记忆完整替换为新正文", "deepseek-v4-flash");
      const notice = await Promise.race([completed, new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("Memory approval did not complete")), 15_000);
      })]);
      assert.equal(notice.method, "run/completed");
      if (notice.method === "run/completed") assert.equal(notice.params.run.status, "succeeded");
      assert.equal(approvals, 1);
      const note = await client.memory("memory/read", { sessionId: session.id, path: "profile.md" });
      assert.equal(note.content, "明确替换后的正文。");
      assert.equal(note.revision, 2);
      await client.shutdown();
      assert.equal(await client.waitForExit(), 0);
    } finally {
      clearTimeout(timer);
      client.terminate();
      await client.waitForExit().catch(() => undefined);
      await rm(temp, { recursive: true, force: true });
    }
  });
}

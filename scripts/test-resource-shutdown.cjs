const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, rm, writeFile } = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow, ipcMain } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(os.tmpdir(), "eidos-resource-quit-ipc-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});

app.whenReady().then(async () => {
  let client, window, code = 0;
  const deadline = setTimeout(() => { client?.terminate(); app.exit(1); }, 20000);
  try {
    const { RuntimeClient } = await import(pathToFileURL(path.join(root, "dist/main/runtime-client.js")).href);
    const runtimeRoot = path.join(temporary, "runtime");
    await mkdir(path.join(runtimeRoot, "eidos_runtime"), { recursive: true });
    await writeFile(path.join(runtimeRoot, "eidos_runtime/__init__.py"), "");
    await writeFile(path.join(runtimeRoot, "eidos_runtime/__main__.py"), `
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if request["method"] == "runtime/shutdown":
        print(json.dumps({"jsonrpc":"2.0", "id":request["id"], "result":{}}), flush=True)
        break
`);
    client = new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot });
    let receivedStatus;
    const statusStarted = new Promise(resolve => { receivedStatus = resolve; });
    ipcMain.handle("resource:status", () => {
      const result = client.getResourceStatus();
      receivedStatus();
      return result;
    });
    ipcMain.handle("resource:retry", (_event, request) => client.retryResource(request));
    window = new BrowserWindow({ show: false, webPreferences: {
      preload: path.join(root, "dist/main/preload.cjs"), contextIsolation: true, nodeIntegration: false,
    } });
    await window.loadURL("data:text/html,<html><body>Resource shutdown IPC regression</body></html>");
    const query = () => window.webContents.executeJavaScript("window.eidosRuntime.getResourceStatus()");
    const pending = query();
    await statusStarted;
    const shutdown = client.shutdown();
    const closing = query();
    const results = await Promise.all([pending, closing]);
    for (const result of results) assert.deepEqual(result, { available: false, directory: "", packs: [] });
    await shutdown;
    assert.equal(await client.waitForExit(), 0);
    assert.deepEqual(await query(), { available: false, directory: "", packs: [] });
    assert.deepEqual(await window.webContents.executeJavaScript("window.eidosRuntime.retryResource({ packId: 'document-python' })"), { accepted: false });
    console.log("Electron resource IPC settled in-flight and late polls during normal shutdown without rejection.");
  } catch (error) { code = 1; console.error(error); }
  finally {
    clearTimeout(deadline);
    window?.destroy();
    client?.terminate();
    await client?.waitForExit();
    try {
      // Chromium can finish a final storage write after the window closes.
      await rm(temporary, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    } catch (error) { code = 1; console.error(error); }
    app.exit(code);
  }
});

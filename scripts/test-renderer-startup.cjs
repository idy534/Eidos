const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { app, BrowserWindow, ipcMain } = require("electron");

const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-renderer-startup-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});

app.whenReady().then(async () => {
  let win;
  let exitCode = 0;
  const deadline = setTimeout(() => {
    console.error("Production Renderer startup timed out");
    app.exit(1);
  }, 20_000);
  try {
    // Only the initial status is stubbed. The production HTML, CSP, bundle and
    // sandboxed preload are loaded without starting Runtime or reading user data.
    ipcMain.handle("runtime:get-status", () => ({ state: "starting" }));
    win = new BrowserWindow({
      show: false,
      webPreferences: {
        preload: path.join(root, "dist/main/preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: false,
      },
    });
    const errors = [];
    win.webContents.on("console-message", event => {
      if (event.level === "error") errors.push(event.message);
    });
    await win.loadFile(path.join(root, "dist/renderer/index.html"));
    const evaluate = code => win.webContents.executeJavaScript(code);
    for (let attempt = 0; attempt < 100; attempt++) {
      if (errors.length || await evaluate("Boolean(document.querySelector('.runtime-gate'))")) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.deepEqual(errors, [], "Production Renderer must load without console errors");
    assert.equal(await evaluate("document.querySelector('.runtime-gate h1')?.textContent"), "正在启动");
    assert.equal(await evaluate("typeof window.eidosRuntime.getStatus"), "function");
    const policy = await evaluate("document.querySelector('meta[http-equiv=\"Content-Security-Policy\"]').content");
    const scriptDirective = policy.split(";").find(value => value.trim().startsWith("script-src "));
    assert.ok(scriptDirective);
    assert.equal(scriptDirective.trim().split(/\s+/).includes("'unsafe-eval'"), false);
    console.log("Production Renderer startup passed: React mounted under CSP with sandboxed preload");
  } catch (error) {
    exitCode = 1;
    console.error(error);
  } finally {
    clearTimeout(deadline);
    win?.destroy();
    ipcMain.removeHandler("runtime:get-status");
    await rm(temporaryRoot, { recursive: true, force: true });
    app.exit(exitCode);
  }
});

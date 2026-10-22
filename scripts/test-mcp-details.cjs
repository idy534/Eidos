const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temp = mkdtempSync(path.join(tmpdir(), "eidos-mcp-ui-"));
app.setPath("userData", path.join(temp, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let vite, win;
  let code = 0;
  const timeout = setTimeout(() => app.exit(1), 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    vite = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await vite.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    const evaluate = source => win.webContents.executeJavaScript(source);
    const until = async source => {
      for (let index = 0; index < 100; index++) { if (await evaluate(source)) return; await new Promise(resolve => setTimeout(resolve, 50)); }
      throw new Error(`Timed out: ${source}`);
    };
    win.setContentSize(1100, 900);
    await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/test/mcp-details.html`);
    await until("Boolean(document.querySelector('.mcp-card__name-button'))");
    await evaluate("document.querySelector('.mcp-card__name-button').focus(); document.querySelector('.mcp-card__name-button').click()");
    await until("document.querySelectorAll('.mcp-tool-row').length === 10");
    await evaluate("document.querySelector('.mcp-tool-row summary').click()");
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 900);
      await new Promise(resolve => setTimeout(resolve, 100));
      const metrics = await evaluate(`(() => {
        const dialog = document.querySelector('[role="dialog"]'), rect = dialog.getBoundingClientRect(), list = document.querySelector('.mcp-detail-list');
        return { left: rect.left, right: rect.right, bottom: rect.bottom, width: innerWidth, height: innerHeight,
          dialogOverflow: dialog.scrollWidth > dialog.clientWidth + 1, scrollable: list.scrollHeight > list.clientHeight,
          closeVisible: document.querySelector('.mcp-detail-close').getBoundingClientRect().right <= innerWidth };
      })()`);
      assert.ok(metrics.left >= 0 && metrics.right <= metrics.width && metrics.bottom <= metrics.height, JSON.stringify(metrics));
      assert.equal(metrics.dialogOverflow, false);
      assert.equal(metrics.scrollable, true);
      assert.equal(metrics.closeVisible, true);
    }
    win.setContentSize(1100, 900);
    await evaluate("document.querySelector('.mcp-detail-list').scrollTop = 100000");
    await until("document.querySelectorAll('.mcp-tool-row').length === 20");
    assert.equal(await evaluate("Boolean(document.querySelector('.mcp-tool-row[open]'))"), true);
    await evaluate("document.querySelector('.mcp-detail-list').scrollTop = 100000");
    await until("document.querySelectorAll('.mcp-tool-row').length === 23");
    assert.equal(await evaluate("window.getMcpCalls()"), 3);
    win.webContents.focus();
    await evaluate("document.querySelector('.mcp-detail-close').focus()");
    for (let index = 0; index < 35; index++) {
      win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
      win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" });
      const focus = await evaluate(`new Promise(resolve => requestAnimationFrame(() => resolve({
        inside: Boolean(document.activeElement.closest('[role=dialog]')), element: document.activeElement.outerHTML.slice(0, 180)
      })))`);
      assert.equal(focus.inside, true, `Tab ${index}: ${focus.element}`);
    }
    await evaluate("document.querySelector('.mcp-detail-list').scrollTop = 0");
    if (process.env.EIDOS_MCP_SCREENSHOT) {
      await new Promise(resolve => setTimeout(resolve, 100));
      await writeFile(process.env.EIDOS_MCP_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    }
    await evaluate("document.querySelector('.mcp-detail-close').click()");
    await until("!document.querySelector('[role=dialog]')");
    assert.equal(await evaluate("document.activeElement.classList.contains('mcp-card__name-button')"), true);
    assert.deepEqual(errors, []);
    console.log("MCP details: 320/768/1024/1440px, real scroll 10→20→23, accordion retention, close/focus passed");
  } catch (error) { code = 1; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await vite?.close(); await rm(temp, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); app.exit(code); }
});

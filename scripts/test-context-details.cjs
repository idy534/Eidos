const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-context-details-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});

app.whenReady().then(async () => {
  let server;
  let window;
  let code = 0;
  const timeout = setTimeout(() => app.exit(1), 50000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    window = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    window.webContents.on("console-message", (event) => { if (event.level === "error") errors.push(event.message); });
    const evaluate = (value) => window.webContents.executeJavaScript(value);
    const key = (value) => {
      window.webContents.sendInputEvent({ type: "keyDown", keyCode: value });
      if (value === "Return") window.webContents.sendInputEvent({ type: "char", keyCode: "\r" });
      window.webContents.sendInputEvent({ type: "keyUp", keyCode: value });
    };
    const wait = (condition) => evaluate(`new Promise((resolve,reject) => { let n=0; const poll=() => { if (${condition}) resolve(); else if(n++>100) reject(new Error('context fixture timed out')); else setTimeout(poll,20); };poll();})`).catch(() => { throw new Error(`Context fixture timed out: ${condition}; errors: ${errors.join("; ")}`); });
    const layouts = [];
    for (const width of [320, 768, 1024, 1440]) {
      window.setContentSize(width, 800);
      await window.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/test/context-details.html`);
      await wait("document.querySelector('.context-indicator-trigger')");
      window.webContents.focus();
      await evaluate("document.querySelector('.context-indicator-trigger').focus()");
      await evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
      key("Return");
      await wait("document.querySelector('dialog[open]')?.textContent.includes('已使用')");
      const layout = await evaluate(`(() => { const dialog=document.querySelector('dialog'); const box=dialog.getBoundingClientRect(); return {width:innerWidth,left:box.left,right:box.right,top:box.top,bottom:box.bottom,overflow:dialog.scrollWidth>dialog.clientWidth,focused:dialog.contains(document.activeElement)};})()`);
      assert.ok(layout.left >= 0 && layout.right <= width && layout.top >= 0 && layout.bottom <= 800);
      assert.equal(layout.overflow, false);
      assert.equal(layout.focused, true);
      layouts.push(layout);
      await evaluate("window.contextSetScenario('overflow')");
      await wait("document.querySelector('.context-hero-percent')?.textContent==='181.4%'");
      assert.equal(await evaluate("document.querySelector('.composer-context-usage').getAttribute('aria-label')"), "上下文 181.4% · 181.4K / 100K");
      assert.equal(await evaluate("Math.round([...document.querySelectorAll('.context-section-value')].reduce((sum,node)=>sum+parseFloat(node.textContent),0)*10)/10"), 181.4);
      await evaluate("window.contextSetScenario('compacted')");
      await wait("document.querySelector('.context-hero-percent')?.textContent==='18%'");
      assert.equal(await evaluate("document.querySelector('.composer-context-usage').getAttribute('aria-label')"), "上下文 ≈18% · ≈18K / 100K");
      assert.equal(await evaluate("Math.round([...document.querySelectorAll('.context-section-value')].reduce((sum,node)=>sum+parseFloat(node.textContent),0)*10)/10"), 18);
      if (width === 1440 && process.env.EIDOS_CONTEXT_SCREENSHOT) {
        await evaluate("new Promise(resolve => setTimeout(resolve, 100))");
        await writeFile(process.env.EIDOS_CONTEXT_SCREENSHOT, (await window.webContents.capturePage()).toPNG());
      }
      await evaluate("document.querySelector('.context-details-compact').focus()");
      key("Return");
      await wait("document.querySelector('.context-details-compact')?.disabled");
      assert.equal(await evaluate("window.contextRequestCount()"), 1);
      key("Escape");
      await wait("!document.querySelector('dialog[open]')");
      assert.equal(await evaluate("document.activeElement?.classList.contains('context-indicator-trigger')"), true);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ contextDetails: "passed", layouts }));
  } catch (error) {
    code = 1;
    console.error(error);
  } finally {
    clearTimeout(timeout);
    window?.destroy();
    await server?.close();
    await rm(temporary, { recursive: true, force: true });
    app.exit(code);
  }
});

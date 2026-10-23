const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, writeFile, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-plugin-hooks-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});

app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Plugin Hook UI test timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    const run = value => win.webContents.executeJavaScript(value);
    const wait = condition => run(`new Promise((resolve,reject) => { let count=0; const timer=setInterval(() => { if (${condition}) { clearInterval(timer); resolve(); } else if (++count>100) { clearInterval(timer); reject(new Error('UI condition timed out: ' + ${JSON.stringify(condition)})); } },20); })`);
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 900);
      await win.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/test/plugin-hooks.html`);
      win.webContents.focus();
      await wait("Array.from(document.querySelectorAll('button')).some(button => button.textContent==='审阅 Hook')");
      assert.ok(await run("document.documentElement.scrollWidth <= innerWidth + 1"));
      await run("const review = Array.from(document.querySelectorAll('button')).find(button => button.textContent==='审阅 Hook'); review.focus(); review.click()");
      await wait("document.querySelector('[role=alertdialog]')");
      await wait("document.activeElement?.textContent==='取消'");
      await run("Promise.all(document.querySelector('.modal-backdrop').getAnimations({ subtree:true }).map(animation => animation.finished.catch(() => {})))");
      const layout = await run(`(() => { const dialog=document.querySelector('[role=alertdialog]'); const bounds=dialog.getBoundingClientRect(); const body=dialog.querySelector('.modal-body'); return { left:bounds.left, right:bounds.right, top:bounds.top, bottom:bounds.bottom, width:innerWidth, height:innerHeight, overflow:body.scrollWidth-body.clientWidth, named:!!document.getElementById(dialog.getAttribute('aria-labelledby')), described:!!document.getElementById(dialog.getAttribute('aria-describedby')) }; })()`);
      assert.ok(layout.left >= -1 && layout.right <= layout.width + 1 && layout.top >= -1 && layout.bottom <= layout.height + 1, JSON.stringify(layout));
      assert.ok(layout.overflow <= 1, JSON.stringify(layout));
      assert.ok(layout.named && layout.described);
      await run("Array.from(document.querySelectorAll('[role=alertdialog] button')).find(button => button.textContent==='信任此版本的 Hook').focus(); window.dispatchEvent(new KeyboardEvent('keydown', { key:'Tab', bubbles:true }))");
      assert.equal(await run("document.activeElement.textContent"), "取消");
      await run("window.dispatchEvent(new KeyboardEvent('keydown', { key:'Tab', shiftKey:true, bubbles:true }))");
      assert.equal(await run("document.activeElement.textContent"), "信任此版本的 Hook");
      if (process.env.EIDOS_HOOK_SCREENSHOT_DIR) {
        await mkdir(process.env.EIDOS_HOOK_SCREENSHOT_DIR, { recursive: true });
        await writeFile(path.join(process.env.EIDOS_HOOK_SCREENSHOT_DIR, `hook-review-${width}.png`), (await win.webContents.capturePage()).toPNG());
      }
      await run("window.dispatchEvent(new KeyboardEvent('keydown', { key:'Escape', bubbles:true }))");
      await wait("!document.querySelector('[role=alertdialog]')");
      assert.equal(await run("pluginHookFixtureCalls.length"), 0);
      await wait("document.activeElement?.textContent==='审阅 Hook'");
      await run("document.querySelector('[role=switch]').click()");
      await wait("document.querySelector('[role=alertdialog]')");
      await run("Array.from(document.querySelectorAll('[role=alertdialog] button')).find(button => button.textContent==='信任此版本的 Hook').click()");
      await wait("pluginHookFixtureCalls.length===2");
      assert.deepEqual(await run("pluginHookFixtureCalls.map(call => call.action)"), ["trust", "enable"]);
      assert.equal(await run("pluginHookFixtureCalls[0].contentHash"), "a".repeat(64));
    }
    assert.deepEqual(errors, []);
    console.log("Plugin Hook review passed keyboard, focus, trust ordering and 320/768/1024/1440 layout checks.");
  } catch (error) {
    console.error(error);
    code = 1;
  } finally {
    clearTimeout(deadline);
    win?.destroy();
    await server?.close();
    await rm(temporary, { recursive: true, force: true });
    app.exit(code);
  }
});

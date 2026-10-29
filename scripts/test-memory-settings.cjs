const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-memory-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Memory UI test timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, width: 1440, height: 900, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    const script = source => win.webContents.executeJavaScript(source);
    const settle = () => script("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    const waitFor = async expression => {
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await script(expression)) return;
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      assert.fail(`UI did not settle: ${expression}`);
    };
    const select = async (label, value) => {
      await script(`(() => { const select = document.querySelector('[aria-label="${label}"]'); select.value = ${JSON.stringify(value)}; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
      await settle();
    };
    await win.loadURL(`${server.resolvedUrls.local[0]}test/memory-settings.html`);
    await waitFor("Boolean(document.querySelector('.memory-note-body'))");
    assert.equal(await script("document.querySelector('[aria-label=位置]').value"), "");
    await select("位置", "eidos");
    await waitFor("document.querySelector('[aria-label=模式]').value === 'read'");
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 900);
      await settle();
      const overflow = await script(`Array.from(document.querySelectorAll('.memory-header select, .memory-header button')).filter(element => {
        const rect = element.getBoundingClientRect(); return rect.left < -1 || rect.right > innerWidth + 1;
      }).map(element => element.getAttribute('aria-label') || element.textContent)`);
      assert.deepEqual(overflow, [], `Header controls must fit at ${width}px`);
      if (process.env.EIDOS_MEMORY_SCREENSHOT_DIR) {
        await mkdir(process.env.EIDOS_MEMORY_SCREENSHOT_DIR, { recursive: true });
        await writeFile(path.join(process.env.EIDOS_MEMORY_SCREENSHOT_DIR, `memory-${width}.png`), (await win.webContents.capturePage()).toPNG());
      }
    }
    await script("window.failNextMemoryList(); window.dispatchEvent(new Event('focus'))");
    await waitFor("document.querySelector('.memory-location-status').textContent.includes('记忆无法读取')");
    await script("Array.from(document.querySelectorAll('button')).find(button => button.textContent === '重新读取').click()");
    await waitFor("!document.querySelector('.memory-location-status').textContent.includes('记忆无法读取')");
    await select("范围", "global");
    await waitFor("document.querySelector('[aria-label=模式]').value === 'auto'");
    await select("位置", "");
    assert.equal(await script("Boolean(document.querySelector('[aria-label=范围]'))"), false);
    assert.equal(await script("window.memoryFixtureCalls.every(call => !call.request || call.request.sessionId == null)"), true);
    assert.deepEqual(errors, []);
    console.log("Memory settings: independent location, project/global switching, failure retry, 320/768/1024/1440px controls and clean console passed.");
  } catch (error) { console.error(error); code = 1; }
  finally {
    clearTimeout(deadline);
    win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(code);
  }
});

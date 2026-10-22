const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-web-search-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Web search UI test timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    const script = source => win.webContents.executeJavaScript(source);
    const settle = () => script("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    await win.loadURL(`${server.resolvedUrls.local[0]}test/web-search.html`);
    for (let attempt = 0; attempt < 100 && !await script("Boolean(document.querySelector('[aria-label=\"LangSearch 默认使用\"]'))"); attempt++) {
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    assert.equal(await script("document.querySelectorAll('.web-search-settings input[type=checkbox]:checked').length"), 0);
    assert.equal(await script("document.querySelector('[aria-label=\"LangSearch API Key\"]').value"), "fixture-langsearch-key");
    assert.equal(await script("document.body.textContent.includes('清除已保存的 Key')"), false);
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 900);
      await settle();
      if (width === 320) await script("document.querySelectorAll('.web-search-settings .web-search-expand-btn').forEach(button => button.click())");
      await settle();
      const overflow = await script(`Array.from(document.querySelectorAll('.web-search-settings input, .web-search-settings button, .web-search-settings .setting-section')).filter(element => {
        const rect = element.getBoundingClientRect(); return rect.left < -1 || rect.right > innerWidth + 1;
      }).map(element => ({ tag: element.tagName, label: element.getAttribute('aria-label') || element.textContent, width: element.getBoundingClientRect().width }))`);
      assert.deepEqual(overflow, [], `Controls must fit at ${width}px`);
      assert.equal(await script("Array.from(document.querySelectorAll('.web-search-settings input:not([type=checkbox])')).every(input => input.getBoundingClientRect().height >= 32)"), true, `Text fields retain their normal height at ${width}px: ${JSON.stringify(await script("Array.from(document.querySelectorAll('.web-search-settings input:not([type=checkbox])')).map(input => ({ label: input.getAttribute('aria-label'), height: input.getBoundingClientRect().height }))"))}`);
      if (process.env.EIDOS_WEB_SEARCH_SCREENSHOT_DIR) {
        await mkdir(process.env.EIDOS_WEB_SEARCH_SCREENSHOT_DIR, { recursive: true });
        await writeFile(path.join(process.env.EIDOS_WEB_SEARCH_SCREENSHOT_DIR, `web-search-${width}.png`), (await win.webContents.capturePage()).toPNG());
      }
    }
    await script("document.querySelector('[aria-label=\"DeepSeek Search 默认使用\"]').focus()");
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Space" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Space" });
    await settle();
    assert.equal(await script("document.querySelector('[aria-label=\"DeepSeek Search 默认使用\"]').checked"), true);
    await script("document.querySelector('[aria-label=\"LangSearch 默认使用\"]').click()");
    await settle();
    assert.equal(await script("document.querySelector('[aria-label=\"DeepSeek Search 默认使用\"]').checked"), false);
    assert.equal(await script("document.querySelector('[aria-label=\"LangSearch 默认使用\"]').checked"), true);
    assert.equal(await script("document.querySelector('[aria-label=\"LangSearch API Key\"]').type"), "password");
    await script("document.querySelector('[aria-label=\"显示 LangSearch API Key\"]').click()");
    await settle();
    assert.equal(await script("document.querySelector('[aria-label=\"LangSearch API Key\"]').type"), "text");
    assert.deepEqual(errors, []);
    console.log("Web search settings fit 320/768/1024/1440px and support keyboard default selection and secret visibility.");
  } catch (error) { console.error(error); code = 1; }
  finally {
    clearTimeout(deadline);
    win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(code);
  }
});

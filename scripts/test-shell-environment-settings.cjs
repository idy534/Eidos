const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-shell-environment-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Shell environment UI test timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    const script = source => win.webContents.executeJavaScript(source);
    const settle = () => script("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    await win.loadURL(`${server.resolvedUrls.local[0]}test/shell-environment.html`);
    for (let attempt = 0; attempt < 100 && !await script("Boolean(document.querySelector('[aria-label=\"环境继承方式\"]'))"); attempt++) {
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    const tabs = await script("Array.from(document.querySelectorAll('[role=tab]')).map(element => element.textContent)");
    assert.equal(tabs.includes("沙盒环境设置"), false);
    assert.equal(await script("document.querySelector('h1').textContent"), "Runtime 引擎环境");
    assert.equal(await script("document.querySelectorAll('.shell-environment-settings .setting-row').length"), 2);
    assert.equal(await script("Array.from(document.querySelectorAll('button')).some(button => button.textContent === '保存设置')"), false);
    assert.equal(await script("document.querySelector('[aria-label=\"环境继承方式\"]').value"), "all");
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 900);
      await settle();
      const overflow = await script(`Array.from(document.querySelectorAll('.shell-environment-settings input, .shell-environment-settings select, .shell-environment-settings textarea, .shell-environment-settings button')).filter(element => {
        const rect = element.getBoundingClientRect(); return rect.left < -1 || rect.right > innerWidth + 1;
      }).map(element => ({ tag: element.tagName, label: element.getAttribute('aria-label') || element.textContent }))`);
      assert.deepEqual(overflow, [], `Controls must fit at ${width}px`);
      if (process.env.EIDOS_SHELL_ENVIRONMENT_SCREENSHOT_DIR) {
        await mkdir(process.env.EIDOS_SHELL_ENVIRONMENT_SCREENSHOT_DIR, { recursive: true });
        await writeFile(path.join(process.env.EIDOS_SHELL_ENVIRONMENT_SCREENSHOT_DIR, `shell-environment-${width}.png`), (await win.webContents.capturePage()).toPNG());
      }
    }
    const target = await script("(() => { const rect = document.querySelector('[aria-label=\"继承方式说明\"]').getBoundingClientRect(); return { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) }; })()");
    win.webContents.sendInputEvent({ type: "mouseMove", ...target });
    await new Promise(resolve => setTimeout(resolve, 250));
    assert.equal(await script("Boolean(document.querySelector('[role=tooltip]'))"), true);
    assert.equal(await script("(() => { const node = document.querySelector('[role=tooltip] .eidos-tooltip__content'); return node.scrollWidth <= node.clientWidth + 1; })()"), true);
    await script("(() => { const select = document.querySelector('[aria-label=\"环境继承方式\"]'); const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set; setter.call(select, 'core'); select.dispatchEvent(new Event('change', { bubbles: true })); })()");
    await settle();
    assert.equal(await script("window.shellEnvironmentFixtureCalls[0].policy.inherit"), "core");
    await script("Array.from(document.querySelectorAll('.shell-environment-settings button')).find(button => button.textContent === '重新读取').click()");
    await settle();
    assert.equal(await script("window.shellEnvironmentFixtureCalls.includes('refresh')"), true);
    assert.deepEqual(errors, []);
    console.log("Shell environment settings: Runtime section, two controls, 320/768/1024/1440px layout, complete help tooltip, automatic save and reread passed.");
  } catch (error) { console.error(error); code = 1; }
  finally {
    clearTimeout(deadline);
    win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(code);
  }
});

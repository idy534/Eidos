const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-personalization-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Personalization UI test timed out"); app.exit(1); }, 45000);
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
      for (let attempt = 0; attempt < 160; attempt++) {
        if (await script(expression)) return;
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      assert.fail(`UI did not settle: ${expression}`);
    };
    const capture = async name => {
      if (!process.env.EIDOS_PERSONALIZATION_SCREENSHOT_DIR) return;
      await mkdir(process.env.EIDOS_PERSONALIZATION_SCREENSHOT_DIR, { recursive: true });
      await writeFile(path.join(process.env.EIDOS_PERSONALIZATION_SCREENSHOT_DIR, `${name}.png`), (await win.webContents.capturePage()).toPNG());
    };
    const findControl = label => `document.getElementById(Array.from(document.querySelectorAll('label')).find(label => label.textContent === ${JSON.stringify(label)}).htmlFor)`;
    const button = label => `Array.from(document.querySelectorAll('button')).find(button => button.textContent === ${JSON.stringify(label)})`;
    await win.loadURL(`${server.resolvedUrls.local[0]}test/personalization-settings.html`);
    await waitFor("document.querySelector('.personalization-settings') && document.querySelector('.personalization-settings').getAttribute('aria-busy') !== 'true'");
    await script("document.querySelector('.settings-content-col').style.scrollBehavior = 'auto'");
    assert.deepEqual(await script("Array.from(document.querySelectorAll('.personalization-settings h2')).map(node => node.textContent)"), ["语言与表达", "关于你", "自定义指令"]);
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 900);
      await script("document.querySelector('.settings-content-col').scrollTop = 0");
      await settle();
      const overflow = await script(`Array.from(document.querySelectorAll('.personalization-settings input, .personalization-settings textarea, .personalization-settings select, .personalization-settings button')).filter(element => {
        const rect = element.getBoundingClientRect(); return rect.left < -1 || rect.right > innerWidth + 1;
      }).map(element => ({ tag: element.tagName, label: element.getAttribute('aria-label') || element.textContent }))`);
      assert.deepEqual(overflow, [], `Controls must fit at ${width}px`);
      await capture(`personalization-${width}-top`);
      await script("document.querySelector('.settings-content-col').scrollTop = document.querySelector('.settings-content-col').scrollHeight");
      await settle();
      assert.equal(await script("(() => { const area = document.querySelector('.settings-content-col'); return Math.abs(area.scrollHeight - area.clientHeight - area.scrollTop) < 2; })()"), true);
      await capture(`personalization-${width}-bottom`);
    }
    await script(`${findControl("回答语言")}.focus()`);
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" });
    await settle();
    assert.equal(await script(`document.activeElement === ${findControl("语气")}`), true);
    await script(`(() => { window.personalizationFixture.failSave=true; const node = ${findControl("语气")}; node.value='friendly'; node.dispatchEvent(new Event('change', {bubbles:true})); })()`);
    await waitFor("document.body.textContent.includes('当前修改已保留')");
    assert.equal(await script(`${findControl("语气")}.value`), "friendly");
    await capture("personalization-save-error");
    await script(`(() => { window.personalizationFixture.failSave=false; const node = ${findControl("语气")}; node.value='formal'; node.dispatchEvent(new Event('change', {bubbles:true})); })()`);
    await waitFor("!document.body.textContent.includes('当前修改已保留')");
    assert.equal(await script("window.personalizationFixture.saves.length >= 2"), true);
    await win.loadURL(`${server.resolvedUrls.local[0]}test/personalization-settings.html?failRead=1`);
    await waitFor("document.body.textContent.includes('无法读取')");
    assert.equal(await script(`${findControl("称呼")}.disabled || ${findControl("称呼")}.matches(':disabled')`), true);
    await capture("personalization-read-error");
    await script(`${button("重新读取")}.click()`);
    await waitFor("!document.body.textContent.includes('无法读取')");
    assert.equal(await script(`${findControl("称呼")}.disabled`), false);
    assert.deepEqual(errors, []);
    console.log("Personalization settings: three sections, 320/768/1024/1440px controls, keyboard navigation, save failure/draft retry, read failure/retry and clean console passed.");
  } catch (error) { console.error(error); code = 1; }
  finally {
    clearTimeout(deadline);
    win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(code);
  }
});

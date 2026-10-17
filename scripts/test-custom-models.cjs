const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile, mkdir } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-custom-model-layout-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Custom model layout test timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    win.setContentSize(1024, 900);
    const script = value => win.webContents.executeJavaScript(value);
    const wait = condition => script(`new Promise((resolve, reject) => { let count = 0; const timer = setInterval(() => { if (${condition}) { clearInterval(timer); resolve(); } else if (++count > 100) { clearInterval(timer); reject(new Error('condition timed out: ' + ${JSON.stringify(condition)})); } }, 20); })`);
    const screenshot = async name => {
      if (!process.env.EIDOS_CUSTOM_MODEL_SCREENSHOT_DIR) return;
      await mkdir(process.env.EIDOS_CUSTOM_MODEL_SCREENSHOT_DIR, { recursive: true });
      await writeFile(path.join(process.env.EIDOS_CUSTOM_MODEL_SCREENSHOT_DIR, name), (await win.webContents.capturePage()).toPNG());
    };
    const connectionSize = () => script(`(() => {
      const button = document.querySelector('.custom-model-test-actions > button');
      return { button: button.getBoundingClientRect().width, key: document.querySelector('[aria-label="API Key"]').getBoundingClientRect().width };
    })()`);
    await win.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/test/custom-models.html`);
    await wait("document.querySelector('.model-settings-panel button:not(:disabled)')");
    await script("document.querySelector('.model-settings-panel button').click()");
    await wait("document.querySelector('[aria-label=\"提供商\"]')");
    assert.equal(await script("Array.from(document.querySelectorAll('button')).some(button => button.textContent === '测试连接')"), false);
    await script("const select = document.querySelector('[aria-label=\"提供商\"]'); select.value = 'custom'; select.dispatchEvent(new Event('change', { bubbles: true }));");
    await wait("document.querySelector('[aria-label=\"接口地址\"]')");
    assert.equal(await script("document.querySelector('[aria-label=\"提供商名称\"]').value"), "");
    await script("Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(document.querySelector('[aria-label=\"提供商 ID\"]'), 'company-gateway'); document.querySelector('[aria-label=\"提供商 ID\"]').dispatchEvent(new Event('input', { bubbles: true }));");
    await wait("document.querySelector('[aria-label=\"提供商名称\"]').value === 'company-gateway'");
    await script("document.querySelector('.modal-backdrop').click()");
    assert.equal(await script("document.querySelector('[role=dialog] [aria-label=\"提供商 ID\"]').value"), "company-gateway");
    assert.equal(await script("document.querySelector('[aria-label=\"API Key\"]').placeholder"), "");
    assert.equal(await script("document.body.textContent.includes('无需 API Key') || document.body.textContent.includes('高级兼容设置') || document.body.textContent.includes('只发送你声明的档位')"), false);
    await script("Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(document.querySelector('[aria-label=\"接口地址\"]'), 'http://127.0.0.1:3425/v1'); document.querySelector('[aria-label=\"接口地址\"]').dispatchEvent(new Event('input', { bubbles: true }));");
    await wait("Array.from(document.querySelectorAll('button')).find(button => button.textContent === '测试连接' && !button.disabled)");
    await script("Promise.all(document.querySelector('.model-dialog').getAnimations().map(animation => animation.finished.catch(() => {})))");
    const beforeRequest = await connectionSize();
    await script("Array.from(document.querySelectorAll('button')).find(button => button.textContent === '测试连接').click()");
    await wait("document.querySelector('.custom-model-test-actions > button').getAttribute('aria-busy') === 'true'");
    assert.equal(await script("document.querySelector('.custom-model-test-actions > button svg') === null"), true);
    assert.deepEqual(await connectionSize(), beforeRequest);
    await wait("document.querySelector('[role=status]')?.textContent.includes('正在获取模型')");
    await screenshot("custom-model-connection-loading.png");
    await wait("document.querySelector('[role=status]').textContent.includes('已获取 2 个模型')");
    assert.deepEqual(await connectionSize(), beforeRequest);
    assert.equal(await script("document.querySelectorAll('.model-dialog [aria-required=true]').length"), 5);
    assert.equal(await script("document.querySelector('.model-dialog').textContent.includes('必填')"), false);
    await script("document.querySelector('[aria-label=\"模型 ID\"]').click()");
    await wait("document.querySelector('[role=option]')");
    assert.equal(await script("document.querySelector('[role=option] small').textContent"), "公司推理模型");
    await screenshot("custom-model-list.png");
    await script("document.querySelector('[role=option]').click()");
    await wait("document.querySelector('[aria-label=\"上下文窗口\"]').value === '128000'");
    assert.equal(await script("document.querySelector('[aria-label=\"最大输出\"]').value"), "8192");
    assert.equal(await script("document.querySelector('[aria-label=\"展示名\"]').value"), "公司推理模型");
    assert.deepEqual(await script("Array.from(document.querySelectorAll('.custom-model-efforts label')).filter(label => label.querySelector('input').checked).map(label => label.textContent.trim())"), ["低", "高", "最高"]);
    assert.equal(await script("document.querySelector('[aria-label=\"默认思考强度\"]').value"), "auto");
    await script("document.querySelector('.modal-body').scrollTop = 0");
    await screenshot("custom-model-connection.png");
    await script("document.querySelector('[aria-label=\"上下文窗口\"]').focus(); document.querySelector('[aria-label=\"上下文窗口\"]').click()");
    await wait("document.querySelector('[role=listbox][aria-label=\"上下文窗口预设\"]')");
    await screenshot("custom-model-token-presets.png");
    await win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Down" });
    await win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Down" });
    await win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Down" });
    await win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Down" });
    await win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
    await win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
    await wait("document.querySelector('[aria-label=\"上下文窗口\"]').value === '524288'");
    await script("document.querySelector('[aria-label=\"上下文窗口\"]').click()");
    await wait("document.querySelector('.custom-model-token-options')");
    await script("document.querySelectorAll('.custom-model-token-options [role=option]')[2].dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))");
    await wait("document.querySelectorAll('.custom-model-token-options .is-active')[0]?.textContent.includes('1024K')");
    const menuState = await script(`(() => {
      const menu = document.querySelector('.custom-model-token-options');
      return { selected: menu.querySelectorAll('[aria-selected=true]').length, checkmarks: menu.querySelectorAll('.custom-model-token-check').length,
        filled: Array.from(menu.querySelectorAll('[role=option]')).filter(option => !['transparent', 'rgba(0, 0, 0, 0)'].includes(getComputedStyle(option).backgroundColor)).length };
    })()`);
    assert.deepEqual(menuState, { selected: 1, checkmarks: 1, filled: 1 });
    await screenshot("custom-model-token-selected-hover.png");
    await script("document.querySelector('.custom-model-section-heading').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))");
    await wait("document.querySelector('.custom-model-token-options') === null");
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 900);
      const measured = await script(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
        const dialog = document.querySelector('.model-dialog');
        const bounds = dialog.getBoundingClientRect();
        const footer = dialog.querySelector('.modal-footer').getBoundingClientRect();
        const body = dialog.querySelector('.modal-body');
        const providerFields = Array.from(dialog.querySelectorAll('.custom-model-provider-row input')).map(input => input.getBoundingClientRect());
        const capabilities = Array.from(dialog.querySelectorAll('.custom-model-capabilities input')).map(input => input.getBoundingClientRect());
        const status = dialog.querySelector('.custom-model-test-actions [role=status]').getBoundingClientRect();
        const testButton = dialog.querySelector('.custom-model-test-actions > button').getBoundingClientRect();
        const secretInput = dialog.querySelector('[aria-label="API Key"]').getBoundingClientRect();
        const secretToggle = dialog.querySelector('.custom-model-secret-toggle').getBoundingClientRect();
        const overflow = Array.from(body.querySelectorAll('input:not([type=checkbox]), select')).some(control => {
          const rect = control.getBoundingClientRect(); return rect.left < 0 || rect.right > innerWidth;
        });
        resolve({ width: innerWidth, left: bounds.left, right: bounds.right, bottom: bounds.bottom, footerBottom: footer.bottom, overflow,
          pageOverflow: document.documentElement.scrollWidth > innerWidth,
          providerSameRow: Math.abs(providerFields[0].top - providerFields[1].top) < 1,
          capabilitiesSameRow: capabilities.every(rect => Math.abs(rect.top - capabilities[0].top) < 1),
          statusAboveButton: status.bottom <= testButton.top,
          iconInsideInput: secretToggle.left >= secretInput.left && secretToggle.right <= secretInput.right && secretToggle.top >= secretInput.top && secretToggle.bottom <= secretInput.bottom,
          gridColumns: getComputedStyle(document.querySelector('.custom-model-grid')).gridTemplateColumns });
      })))`);
      assert.ok(measured.left >= 0 && measured.right <= width + 1, JSON.stringify(measured));
      assert.ok(measured.bottom <= 900 && measured.footerBottom <= 900, JSON.stringify(measured));
      assert.equal(measured.overflow, false, JSON.stringify(measured));
      assert.equal(measured.pageOverflow, false, JSON.stringify(measured));
      assert.equal(measured.capabilitiesSameRow, true, JSON.stringify(measured));
      assert.equal(measured.statusAboveButton, true, JSON.stringify(measured));
      assert.equal(measured.iconInsideInput, true, JSON.stringify(measured));
      if (width > 480) assert.equal(measured.providerSameRow, true, JSON.stringify(measured));
      if (width === 320) assert.equal(measured.gridColumns.split(' ').length, 1);
      await script("document.activeElement.blur(); document.querySelector('.modal-body').scrollTop = document.querySelector('.modal-body').scrollHeight");
      await script("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
      await screenshot(`custom-model-options-${width}.png`);
    }
    assert.equal(await script("window.customModelCalls.length"), 1);
    assert.deepEqual(errors, []);
    console.log("Custom model Electron checks passed: stable connection feedback, one menu highlight, actual token values with K presets, metadata and reasoning fill, required stars, input icons, keyboard presets and layout at 320/768/1024/1440px.");
  } catch (error) { code = 1; console.error(error); }
  finally { clearTimeout(deadline); win?.destroy(); await server?.close(); await rm(temporaryRoot, { recursive: true, force: true }); app.exit(code); }
});

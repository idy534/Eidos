const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, mkdir, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-qoder-layout-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win, code = 0;
  const deadline = setTimeout(() => app.exit(1), 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    win.setContentSize(1024, 800);
    const script = value => win.webContents.executeJavaScript(value);
    const wait = condition => script(`new Promise((resolve, reject) => { let n=0; const timer=setInterval(() => { if (${condition}) { clearInterval(timer); resolve(); } else if (++n>150) { clearInterval(timer); reject(new Error('Qoder UI timed out')); } },20); })`);
    const screenshot = async name => {
      if (!process.env.EIDOS_QODER_SCREENSHOT_DIR) return;
      await mkdir(process.env.EIDOS_QODER_SCREENSHOT_DIR, { recursive: true });
      await writeFile(path.join(process.env.EIDOS_QODER_SCREENSHOT_DIR, name), (await win.webContents.capturePage()).toPNG());
    };
    await win.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/test/qoder-models.html`);
    await wait("document.querySelector('.model-settings-panel button:not(:disabled)')");
    await script("document.querySelector('.model-settings-panel button').click()");
    await wait("document.querySelector('[aria-label=\"提供商\"]')");
    await script("const select=document.querySelector('[aria-label=\"提供商\"]');select.value='qoder';select.dispatchEvent(new Event('change',{bubbles:true}));");
    await wait("document.body.textContent.includes('正在检查本地登录状态')");
    await script("Promise.all(document.querySelector('.model-dialog').getAnimations().map(a=>a.finished.catch(()=>{})))");
    await screenshot("qoder-model-checking-login.png");
    await wait("document.querySelector('[aria-label=\"模型名称\"]')?.value==='efficient'");
    assert.equal(await script("document.querySelector('[aria-label=\"API Key\"]')"), null);
    assert.deepEqual(await script("Array.from(document.querySelectorAll('.custom-model-check')).map(label=>({label:label.textContent,checked:label.querySelector('input').checked,disabled:label.querySelector('input').disabled}))"), [
      { label: "文本", checked: true, disabled: true },
      { label: "图像", checked: true, disabled: false },
      { label: "支持工具调用", checked: true, disabled: false },
    ]);
    await script("Promise.all(document.querySelector('.model-dialog').getAnimations().map(a=>a.finished.catch(()=>{})))");
    await script("const details = document.querySelector('details'); if (details) details.open = true;");
    for (const width of [320, 768, 1024]) {
      win.setContentSize(width, 800);
      const measured = await script(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
        const dialog=document.querySelector('.model-dialog'), body=dialog.querySelector('.modal-body');
        const r=dialog.getBoundingClientRect(), footer=dialog.querySelector('.modal-footer').getBoundingClientRect();
        resolve({ left:r.left, right:r.right, bottom:r.bottom, footer:footer.bottom,
          overflow:body.scrollWidth>body.clientWidth+1, page:document.documentElement.scrollWidth>innerWidth });
      })))`);
      assert.ok(measured.left>=0 && measured.right<=width+1 && measured.bottom<=800 && measured.footer<=800, JSON.stringify(measured));
      assert.equal(measured.overflow, false); assert.equal(measured.page, false);
      await screenshot(`qoder-model-${width}.png`);
    }
    await script("document.querySelector('[aria-label=\"展开上下文窗口预设\"]').click()");
    await wait("document.querySelector('[role=listbox]')");
    assert.deepEqual(await script("Array.from(document.querySelectorAll('[role=listbox] [role=option]')).map(option=>option.textContent)"), ["200K", "400K", "1000K"]);
    await screenshot("qoder-model-token-dropdown.png");
    await script("document.querySelector('[aria-label=\"展开上下文窗口预设\"]').click()");
    await script("document.querySelector('[aria-label=\"展开最大输出预设\"]').click()");
    await wait("document.querySelector('[role=listbox]')");
    assert.deepEqual(await script("Array.from(document.querySelectorAll('[role=listbox] [role=option]')).map(option=>option.textContent)"), ["8K", "16K", "32K"]);
    await screenshot("qoder-model-output-dropdown.png");
    await script("document.querySelector('[aria-label=\"展开最大输出预设\"]').click()");
    assert.equal(await script("document.body.textContent.includes('已登录')"), true);
    await script("Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='重新登录').click()");
    await wait("document.querySelector('[role=status]')?.textContent.includes('请在系统浏览器')");
    await screenshot("qoder-model-login.png");
    await script("window.qoderFixturePhase('error')");
    await wait("document.querySelector('[role=alert]')?.textContent.includes('登录已失效')");
    await screenshot("qoder-model-error.png");
    await script("document.querySelector('[aria-label=\"提供商\"]').focus()");
    await script("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))");
    assert.equal(await script("document.activeElement.textContent"), "保存");
    await script("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");
    await wait("!document.querySelector('[role=dialog]')");
    assert.equal(await script("Boolean(document.querySelector('[aria-label=\"编辑 DeepSeek-Flash\"]')) && Boolean(document.querySelector('[aria-label=\"编辑 Kimi-K3.1-Pro\"]'))"), true);
    assert.equal(await script("Boolean(document.querySelector('.model-selector__trigger')?.textContent.includes('DeepSeek-Flash'))"), true);
    await screenshot("qoder-model-saved-names.png");
    console.log("Qoder Electron layout passed at 320/768/1024px; shared input types, saved names, selector, login, error and keyboard passed.");
  } catch (error) { code=1; console.error(error); }
  finally { clearTimeout(deadline); win?.destroy(); await server?.close(); await rm(temporary,{recursive:true,force:true}); app.exit(code); }
});

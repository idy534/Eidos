const assert = require("node:assert/strict");
const { mkdir, writeFile, rm } = require("node:fs/promises");
const { mkdtempSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(os.tmpdir(), "eidos-resource-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win, code = 0;
  const timeout = setTimeout(() => app.exit(1), 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const script = code => win.webContents.executeJavaScript(code);
    await win.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/test/managed-resources.html`);
    await script(`new Promise((resolve, reject) => { let count=0; const timer=setInterval(() => { if(document.querySelector('summary')) { clearInterval(timer); resolve(); } else if(++count>100) { clearInterval(timer); reject(Error('resource panel missing')); } },50); })`);
    await script("document.querySelector('summary').click()");
    const screenshots = path.join(root, ".cache/document-runtime/resource-ui");
    await mkdir(screenshots, { recursive: true });
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 720);
      await script("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
      assert.equal(await script("document.documentElement.scrollWidth <= innerWidth"), true, `overflow at ${width}`);
      assert.equal(await script("document.body.textContent.includes('下载中 50%') && document.body.textContent.includes('磁盘空间不足')"), true);
      await writeFile(path.join(screenshots, `resources-${width}.png`), (await win.webContents.capturePage()).toPNG());
    }
    await script("document.querySelector('summary').focus()");
    assert.equal(await script("document.activeElement.tagName"), "SUMMARY");
    await script("document.querySelector('[aria-label=\"重试OCR 引擎\"]').click()");
    await script("new Promise(resolve => setTimeout(resolve,100))");
    assert.equal(await script("document.body.textContent.includes('磁盘空间不足')"), false);
    assert.equal(await script("[...document.querySelectorAll('[role=status]')].filter(el=>el.textContent.includes('下载中')).length"), 2);
    console.log("Managed resource waiting/error/retry and layout passed at 320/768/1024/1440px.");
  } catch (error) { code=1; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await server?.close(); await rm(temporary,{recursive:true,force:true}); app.exit(code); }
});

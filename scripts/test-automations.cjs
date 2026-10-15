const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-automations-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win, exitCode = 0;
  const timeout = setTimeout(() => { console.error("Automation UI timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, width: 1100, height: 850, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    await win.loadURL(`${server.resolvedUrls.local[0]}test/automations.html`);
    const evaluate = code => win.webContents.executeJavaScript(code);
    const settle = () => evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    for (let i = 0; i < 100 && !await evaluate("Boolean(document.querySelector('.automation-list-item'))"); i++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(await evaluate("Boolean(document.querySelector('.automation-list-item'))"), true);
    await evaluate("document.querySelector('.automation-list-item').click()"); await settle();
    assert.equal(await evaluate("document.querySelector('.automation-detail').textContent.includes('替我审批')"), true);
    if (process.env.EIDOS_AUTOMATION_SCREENSHOT) await writeFile(process.env.EIDOS_AUTOMATION_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent==='新建任务').click()"); await settle();
    assert.equal(await evaluate("[...document.querySelectorAll('label')].find(label=>label.textContent.startsWith('审批模式')).querySelector('select').value"), "auto_review");
    for (const width of [1440, 1024, 768, 320]) {
      win.setSize(width, 900); await settle();
      const dimensions = await evaluate("({width:document.querySelector('.automation-page').clientWidth,scroll:document.querySelector('.automation-page').scrollWidth})");
      assert.ok(dimensions.scroll <= dimensions.width + 1, `No horizontal overflow at ${width}px: ${JSON.stringify(dimensions)}`);
    }
    win.setSize(1100, 850); await settle();
    await evaluate(`(() => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      const input = [...document.querySelectorAll('label')].find(label=>label.textContent==='名称').querySelector('input');
      setValue.call(input,'每日检查'); input.dispatchEvent(new Event('input',{bubbles:true}));
      const textarea=document.querySelector('textarea');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(textarea,'检查项目状态。');
      textarea.dispatchEvent(new Event('input',{bubbles:true}));
    })()`); await settle();
    await evaluate("document.querySelector('button[type=submit]').click()"); await settle();
    const saved = await evaluate("window.automationTestState.commands.find(command=>command.mode==='create')");
    assert.equal(saved.approvalMode, "auto_review");
    assert.equal(saved.name, "每日检查");
    assert.equal("sessionId" in saved, false);
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" }); await settle();
    assert.equal(await evaluate("document.activeElement.tagName==='BUTTON' || document.activeElement.tagName==='SELECT' || document.activeElement.tagName==='INPUT'"), true);
    assert.deepEqual(errors, []);
    console.log("Automation UI passed: create, default approval, Session omission, keyboard and 4 viewport sizes");
  } catch (error) { exitCode = 1; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await server?.close(); await rm(temporaryRoot, { recursive: true, force: true }); app.exit(exitCode); }
});

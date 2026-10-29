const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-project-editor-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Project editor UI test timed out"); app.exit(1); }, 45000);
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
      if (!process.env.EIDOS_PROJECT_EDITOR_SCREENSHOT_DIR) return;
      await mkdir(process.env.EIDOS_PROJECT_EDITOR_SCREENSHOT_DIR, { recursive: true });
      await writeFile(path.join(process.env.EIDOS_PROJECT_EDITOR_SCREENSHOT_DIR, `${name}.png`), (await win.webContents.capturePage()).toPNG());
    };
    const button = label => `Array.from(document.querySelectorAll('button')).find(button => button.textContent === ${JSON.stringify(label)})`;
    const aria = label => `document.querySelector('button[aria-label="${label}"]')`;
    await win.loadURL(`${server.resolvedUrls.local[0]}test/project-editor.html`);
    await waitFor("document.querySelectorAll('.project-editor-folder-row').length === 3");
    for (const width of [320, 768, 1440]) {
      win.setContentSize(width, 900);
      await settle();
      assert.deepEqual(await script(`Array.from(document.querySelectorAll('.project-editor-dialog button, .project-editor-dialog input')).filter(element => {
        const rect = element.getBoundingClientRect(); return rect.left < -1 || rect.right > innerWidth + 1;
      }).map(element => element.getAttribute('aria-label') || element.textContent)`), [], `Controls must fit at ${width}px`);
      await capture(`project-editor-${width}`);
    }
    await script(`${button("保存")}.focus()`);
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" });
    await settle();
    assert.equal(await script("document.activeElement.getAttribute('aria-label')"), "关闭");
    await script(`${aria("将 appforge-service 设为主要")}.click()`);
    await script(`${aria("移除文件夹 rag-engine")}.click()`);
    await script(`${button("添加文件夹")}.click()`);
    await waitFor("document.body.textContent.includes('/Users/example/Projects/extra')");
    await script(`${aria("移除文件夹 extra")}.click()`);
    await script(`${button("添加文件夹")}.click()`);
    await waitFor("document.querySelectorAll('.project-editor-folder-row').length === 3");
    await script(`(() => { const input=document.querySelector('.project-editor-dialog input'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'agentic workspace'); input.dispatchEvent(new Event('input',{bubbles:true})); window.projectEditorFixture.failSave=true; window.projectEditorFixture.delay=700; })()`);
    await script(`${button("保存")}.click()`);
    await waitFor("document.querySelector('.project-editor-dialog input').disabled");
    assert.equal(await script(`${button("取消")}.disabled`), true);
    await capture("project-editor-saving");
    await waitFor("document.body.textContent.includes('你的修改已保留')");
    assert.equal(await script("document.querySelector('.project-editor-dialog input').value"), "agentic workspace");
    await capture("project-editor-save-error");
    await script("window.projectEditorFixture.failSave=false");
    await script(`${button("保存")}.click()`);
    await waitFor("!document.querySelector('.project-editor-dialog')");
    const saves = await script("window.projectEditorFixture.saves");
    assert.equal(saves.length, 2);
    assert.deepEqual(saves[0], saves[1]);
    assert.equal(saves[0].primaryRootId, "service");
    assert.deepEqual(saves[0].roots.map(root => root.path), ["/Users/example/Projects/agentic-kit", "/Users/example/Projects/appforge-service", "/Users/example/Projects/extra"]);
    assert.deepEqual(errors, []);
    console.log("Project editor: 320/768/1440px, keyboard focus, add/remove/re-add, primary selection, saving/error state and same-operation retry passed.");
  } catch (error) { console.error(error); code = 1; }
  finally {
    clearTimeout(deadline);
    win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(code);
  }
});

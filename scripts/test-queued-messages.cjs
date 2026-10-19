const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile, mkdir } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-queued-layout-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});

async function check() {
  const frame = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  await frame();
  const list = document.querySelector(".queued-messages");
  const composer = document.querySelector(".composer");
  if (!list || !composer) throw new Error("queue and Composer must be present");
  const queue = list.getBoundingClientRect();
  const input = composer.getBoundingClientRect();
  if (Math.abs(queue.left - input.left) > 1 || Math.abs(queue.right - input.right) > 1) throw new Error("queue must align with Composer");
  if (queue.top >= input.top || queue.bottom - input.top > 14) throw new Error("queue must sit immediately above Composer");
  for (const button of list.querySelectorAll("button")) {
    const bounds = button.getBoundingClientRect();
    if (bounds.left < 0 || bounds.right > innerWidth) throw new Error("queue controls must fit the viewport");
  }
  for (const icon of list.querySelectorAll('.queued-message-row svg')) {
    const bounds = icon.getBoundingClientRect();
    if (bounds.width !== 16 || bounds.height !== 16) throw new Error("queue icons must retain their 16px size");
  }
  if (document.documentElement.scrollWidth > innerWidth) throw new Error("queue must not create horizontal page overflow");
  return { width: innerWidth, queueHeight: queue.height, composerTop: input.top, queueTop: queue.top };
}

app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Queued message test timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", (event) => { if (event.level === "error") errors.push(event.message); });
    win.setContentSize(1100, 800);
    await win.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/test/queued-messages.html`);
    const script = (code) => win.webContents.executeJavaScript(code);
    await script(`new Promise((resolve, reject) => { let count = 0; const timer = setInterval(() => {
      if (document.querySelector('.queued-messages')) { clearInterval(timer); resolve(); }
      else if (++count > 50) { clearInterval(timer); reject(new Error('queue did not load')); }
    }, 50); })`);
    const desktop = await script(`(${check.toString()})()`);
    if (process.env.EIDOS_QUEUE_SCREENSHOT_DIR) {
      await mkdir(process.env.EIDOS_QUEUE_SCREENSHOT_DIR, { recursive: true });
      await writeFile(path.join(process.env.EIDOS_QUEUE_SCREENSHOT_DIR, "queued-messages-desktop.png"), (await win.webContents.capturePage()).toPNG());
      const clip = await script(`(() => { const queue = document.querySelector('.queued-messages').getBoundingClientRect(); const composer = document.querySelector('.composer').getBoundingClientRect(); return { x: Math.floor(queue.left - 8), y: Math.floor(queue.top - 8), width: Math.ceil(queue.width + 16), height: Math.ceil(composer.bottom - queue.top + 16) }; })()`);
      await writeFile(path.join(process.env.EIDOS_QUEUE_SCREENSHOT_DIR, "queued-messages-detail.png"), (await win.webContents.capturePage(clip)).toPNG());
    }
    await script("document.querySelector('[aria-label=\"编辑第 1 条排队消息\"]').click()");
    await script("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    await script(`Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(document.querySelector('.queued-message-editor textarea'), '修改后的排队消息'); document.querySelector('.queued-message-editor textarea').dispatchEvent(new Event('input', { bubbles: true }));`);
    await script("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    await script("document.querySelector('.queued-message-editor [type=submit]').click()");
    await script("new Promise(resolve => setTimeout(resolve, 100))");
    assert.equal(await script("document.querySelector('.queued-message-preview').textContent"), "修改后的排队消息");
    assert.equal(await script("document.querySelector('.composer textarea').value"), "主输入框中的草稿");
    for (const width of [320, 768, 1024, 1440, 420]) {
      win.setContentSize(width, 700);
      await script("new Promise(resolve => setTimeout(resolve, 100))");
      await script(`(${check.toString()})()`);
    }
    await script(`window.setQueuedMessages(Array.from({ length: 8 }, (_, index) => ({ sessionId: 's', runId: 'queued-' + index, userInput: '第 ' + index + ' 条消息：' + '长消息内容'.repeat(40), references: [] })))`);
    await script("new Promise(resolve => setTimeout(resolve, 100))");
    const narrow = await script(`(${check.toString()})()`);
    assert.ok(narrow.queueHeight <= 210, "long queues must scroll within 30vh");
    await script("document.querySelector('[aria-label=\"编辑第 1 条排队消息\"]').click()");
    await script("new Promise(resolve => setTimeout(resolve, 100))");
    await script(`(${check.toString()})()`);
    assert.equal(await script(`(() => { const list = document.querySelector('.queued-messages').getBoundingClientRect(); return [...document.querySelectorAll('.queued-message-editor-actions button')].every(button => { const bounds = button.getBoundingClientRect(); return bounds.top >= list.top && bounds.bottom <= list.bottom; }); })()`), true, "editor actions must be visible without scrolling");
    if (process.env.EIDOS_QUEUE_SCREENSHOT_DIR) await writeFile(path.join(process.env.EIDOS_QUEUE_SCREENSHOT_DIR, "queued-messages-narrow.png"), (await win.webContents.capturePage()).toPNG());
    await script("document.querySelector('.queued-message-editor [type=button]').click()");
    await script("document.querySelector('[aria-label=\"删除第 2 条排队消息\"]').click()");
    await script("new Promise(resolve => setTimeout(resolve, 100))");
    assert.equal(await script("document.querySelectorAll('.queued-message').length"), 7);
    await script(`document.querySelector('[aria-label="将第 1 条排队消息用于引导当前任务"]').focus()`);
    win.show();
    win.focus();
    win.webContents.focus();
    await script("new Promise(resolve => setTimeout(resolve, 250))");
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
    win.webContents.sendInputEvent({ type: "char", keyCode: "\r" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
    await script("new Promise(resolve => setTimeout(resolve, 100))");
    assert.equal(await script("document.querySelectorAll('.queued-message').length"), 6);
    assert.equal(await script("document.querySelector('.process-group .process-steering .feed-label').textContent"), "引导对话 · 等待下一步读取");
    assert.equal(await script("document.querySelector('.composer textarea').value"), "主输入框中的草稿");
    await script("window.consumeSteering()");
    await script("new Promise(resolve => setTimeout(resolve, 100))");
    assert.equal(await script("document.querySelector('.process-steering .feed-label').textContent"), "引导对话 · 已加入当前对话");
    if (process.env.EIDOS_QUEUE_SCREENSHOT_DIR) await writeFile(path.join(process.env.EIDOS_QUEUE_SCREENSHOT_DIR, "queued-steering.png"), (await win.webContents.capturePage()).toPNG());
    await script("window.setQueuedMessages([])");
    await script("new Promise(resolve => setTimeout(resolve, 100))");
    assert.equal(await script("Boolean(document.querySelector('.queued-messages'))"), false);
    assert.deepEqual(errors, [], "queue fixture must have no renderer errors");
    console.log(JSON.stringify({ desktop, narrow }));
    console.log("Queued message native layout, editing, deletion, keyboard steering and draft isolation passed.");
  } catch (error) { console.error(error); code = 1; }
  finally { clearTimeout(deadline); win?.destroy(); await server?.close(); await rm(temporaryRoot, { recursive: true, force: true }); app.exit(code); }
});

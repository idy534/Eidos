const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-welcome-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});

async function checkWelcome() {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const input = document.querySelector("textarea");
  const composer = document.querySelector(".composer");
  const rect = composer.getBoundingClientRect();
  const header = document.querySelector(".session-header");
  check(!input.disabled, "Welcome input must be editable");
  check(Boolean(document.querySelector(".session-canvas--welcome")), "Welcome page must be visible");
  check(!header.querySelector("h1"), "Draft header must hide its title");
  check(!document.querySelector(".feed-empty"), "Draft must not show an empty execution record");
  check(rect.left >= 0 && rect.right <= innerWidth, "Composer must fit the viewport");
  check(rect.bottom <= innerHeight && rect.top > header.getBoundingClientRect().bottom, "Composer must be fully visible");
  if (innerHeight >= 800) {
    check(Math.abs(rect.top - innerHeight / 2) <= 1, "The welcome Composer top edge must align with the window midpoint");
  }
  for (const button of composer.querySelectorAll("button")) {
    const box = button.getBoundingClientRect();
    check(box.left >= rect.left && box.right <= rect.right, "Composer controls must fit at narrow widths");
  }
  check(document.documentElement.scrollWidth <= innerWidth, "Welcome must not overflow horizontally");
  return { width: innerWidth, height: innerHeight, composerTop: rect.top, composerBottom: rect.bottom };
}

app.whenReady().then(async () => {
  let server;
  let win;
  let exitCode = 0;
  const deadline = setTimeout(() => { console.error("Welcome layout timed out"); app.exit(1); }, 50_000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"),
      server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", (event) => { if (event.level === "error") errors.push(event.message); });
    const evaluate = (code) => win.webContents.executeJavaScript(code);
    const url = `http://127.0.0.1:${server.httpServer.address().port}/test/welcome-layout.html`;
    const layouts = [];
    for (const [width, height] of [[1440, 800], [1024, 800], [768, 800], [320, 800], [1024, 1000], [1024, 640]]) {
      win.setContentSize(width, height);
      await win.loadURL(url);
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await evaluate("Boolean(document.querySelector('.session-canvas--welcome textarea:not(:disabled)'))")) break;
        await new Promise((resolve) => setTimeout(resolve, 30));
      }
      layouts.push(await evaluate(`(${checkWelcome.toString()})()`));
      if (width === 1440 && process.env.EIDOS_WELCOME_SCREENSHOT) {
        await writeFile(process.env.EIDOS_WELCOME_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
      }
      if (width === 1440) {
        const logo = await evaluate("(() => { const box = document.querySelector('.empty-logo').getBoundingClientRect(); return { x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height / 2) }; })()");
        win.webContents.sendInputEvent({ type: "mouseMove", ...logo });
        await new Promise((resolve) => setTimeout(resolve, 350));
        assert.equal(await evaluate("document.querySelector('.empty-state').matches(':hover') && getComputedStyle(document.querySelector('.empty-logo')).transform !== 'none'"), true, "The original welcome logo hover must remain available");
      }
    }
    // Use native typing. Stall creation to inspect the real animation before IPC completes.
    win.setContentSize(1024, 800);
    await win.loadURL(url);
    await evaluate(`new Promise(resolve => {
      const poll = () => document.querySelector('textarea:not(:disabled)') ? resolve() : setTimeout(poll, 20); poll();
    })`);
    const frame = () => evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    const sizing = () => evaluate(`(() => {
      const form = document.querySelector('.composer');
      const input = form.querySelector('textarea');
      const box = form.getBoundingClientRect();
      return { top: box.top, bottom: box.bottom, height: box.height,
        inputHeight: input.getBoundingClientRect().height, scrollable: input.scrollHeight > input.clientHeight,
        focused: document.activeElement === input };
    })()`);
    const normalHeight = await evaluate(`(() => {
      const form = document.querySelector('.composer');
      const clone = form.cloneNode(true);
      clone.inert = true;
      clone.style.cssText = 'position:fixed;left:-10000px;width:' + form.getBoundingClientRect().width + 'px';
      document.body.append(clone);
      const height = clone.getBoundingClientRect().height;
      clone.remove();
      return height;
    })()`);
    const empty = await sizing();
    assert.equal(empty.inputHeight, 56, "Welcome must reuse the normal 56px textarea minimum");
    assert.ok(Math.abs(empty.height - normalHeight) <= 1, "Empty welcome and normal Composers must have the same height");
    await evaluate("document.querySelector('textarea').focus()");
    await win.webContents.insertText("短文本");
    await frame();
    const short = await sizing();
    assert.equal(short.height, empty.height, "Short input must retain normal Composer height");
    await evaluate("document.querySelector('textarea').select()");
    await win.webContents.insertText(Array.from({ length: 100 }, (_, index) => `第 ${index + 1} 行：这是一段用于验证草稿向下增长的长文本。`).join("\n"));
    await frame();
    const long = await sizing();
    assert.ok(Math.abs(long.top - empty.top) <= 1, "Long drafts must grow downward from the same top edge");
    assert.ok(Math.abs(long.bottom - (800 - 16)) <= 1, "Long drafts must stop at the normal conversation bottom edge");
    assert.ok(long.inputHeight > 168 && long.scrollable && long.focused, "Long drafts must use available space and then scroll internally");
    if (process.env.EIDOS_WELCOME_LONG_SCREENSHOT) {
      await writeFile(process.env.EIDOS_WELCOME_LONG_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    }
    const resized = [];
    for (const height of [640, 1000]) {
      win.setContentSize(1024, height);
      await frame();
      const current = await sizing();
      assert.ok(Math.abs(current.bottom - (height - 16)) <= 1, "Resizing must keep the long draft above the normal bottom inset");
      assert.ok(current.scrollable && current.focused, "Resizing must preserve editable, internally scrolling text");
      resized.push({ viewportHeight: height, ...current });
    }
    win.setContentSize(1024, 800);
    await frame();
    await evaluate("document.querySelector('textarea').select()");
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Backspace" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Backspace" });
    await frame();
    const cleared = await sizing();
    assert.equal(cleared.height, empty.height, "Clearing a long draft must restore the normal Composer height");
    assert.equal(cleared.top, empty.top, "Clearing a long draft must preserve its top edge");
    await evaluate("document.querySelector('textarea').select()");
    await win.webContents.insertText("{\"hash\":\"" + "72fa7bf510eb2a779dd920675938de7df265143435e4466939968ca29ec856a".repeat(10) + "\"}");
    await frame();
    const unbrokenCanvasScrollWidth = await evaluate("document.querySelector('.session-canvas--welcome').scrollWidth");
    const unbrokenCanvasClientWidth = await evaluate("document.querySelector('.session-canvas--welcome').clientWidth");
    assert.ok(unbrokenCanvasScrollWidth <= unbrokenCanvasClientWidth, "Unbroken long draft text must wrap without expanding welcome canvas");
    assert.ok(await evaluate("document.documentElement.scrollWidth <= innerWidth"), "Unbroken long draft must not cause window horizontal overflow");
    await evaluate("document.querySelector('textarea').select()");
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Backspace" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Backspace" });
    await frame();
    await evaluate(`window.welcomeInput = document.querySelector('textarea'); welcomeInput.focus();
      window.originalCreate = eidosRuntime.createSession;
      eidosRuntime.createSession = () => new Promise(resolve => { window.finishCreate = async () => resolve(await originalCreate(null)); }); void 0;`);
    await win.webContents.insertText("请整理今天的任务");
    const startTop = await evaluate("document.querySelector('.composer').getBoundingClientRect().top");
    await evaluate("document.querySelector('.composer').requestSubmit()");
    const animation = await evaluate(`(async () => {
      const surface = document.querySelector('.composer');
      const moving = surface.getAnimations().find(value => value.effect.getKeyframes().some(frame => "transform" in frame));
      if (!moving) throw new Error('Sending must animate the Composer');
      moving.pause(); await moving.ready; moving.currentTime = 100;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return { intermediateTop: surface.getBoundingClientRect().top, targetTop: document.querySelector('.session-canvas__composer').getBoundingClientRect().top };
    })()`);
    assert.ok(animation.intermediateTop > startTop && animation.intermediateTop < animation.targetTop, "Composer must move smoothly toward the bottom");
    await evaluate("document.querySelector('.composer').getAnimations().find(value => value.effect.getKeyframes().some(frame => 'transform' in frame)).finish(); finishCreate()");
    await evaluate(`new Promise(resolve => {
      const poll = () => document.querySelector('[aria-label="取消 Run"]') ? resolve() : setTimeout(poll, 20); poll();
    })`);
    assert.equal(await evaluate("document.querySelector('textarea') === welcomeInput"), true, "The Composer must retain its textarea");
    assert.equal(await evaluate("Boolean(document.querySelector('.session-canvas--welcome'))"), false);
    assert.equal(await evaluate("Math.round(innerHeight - document.querySelector('.composer').getBoundingClientRect().bottom)"), 16);
    await evaluate("document.querySelector('.sidebar .primary-action-btn').click()");
    await evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    await evaluate(`(${checkWelcome.toString()})()`);
    assert.deepEqual(errors, [], "Welcome flow must have no console errors");
    console.log(JSON.stringify({ layouts, sizing: { empty, short, long, resized, cleared }, animation, sameTextarea: true, newConversation: true }));
  } catch (error) { exitCode = 1; console.error(error); }
  finally {
    clearTimeout(deadline);
    win?.destroy();
    await server?.close();
    await rm(temporaryRoot, { recursive: true, force: true });
    app.exit(exitCode);
  }
});

const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");

const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-composer-layout-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});

// jsdom cannot reproduce scroll clamping during intermediate textarea layouts.
async function checkLayout() {
  const frame = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  for (let attempt = 0; !window.setComposerDraft && attempt < 100; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  check(Boolean(window.setComposerDraft), "Composer fixture did not render");
  check(CSS.supports("field-sizing", "content"), "Electron must support native textarea sizing");
  const textarea = document.querySelector("textarea");
  const feed = document.querySelector(".feed");
  const setDraft = async (value) => { window.setComposerDraft(value); await frame(); };
  const metrics = () => ({
    height: textarea.getBoundingClientRect().height,
    scrollTop: feed.scrollTop,
    feedHeight: feed.clientHeight,
    recordTop: feed.querySelector(".feed-item").getBoundingClientRect().top,
  });
  const unchanged = (before, label) => {
    const after = metrics();
    for (const key of Object.keys(before)) {
      check(Math.abs(before[key] - after[key]) < 1, `${label}: ${key} changed from ${before[key]} to ${after[key]}`);
    }
    check(document.activeElement === textarea, `${label}: textarea lost focus`);
    check(window.scrollY === 0, `${label}: window scrolled`);
  };
  const edit = async (value, isComposing = false, inputType = "insertText") => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(textarea, value);
    textarea.dispatchEvent(new InputEvent("input", { bubbles: true, inputType, isComposing }));
    await frame();
  };

  await setDraft("");
  textarea.focus({ preventScroll: true });
  for (let attempt = 0; attempt < 100; attempt++) {
    if (feed.querySelector(".shiki-code-body") && feed.querySelector("img")?.naturalHeight > 0) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  check(Boolean(feed.querySelector(".shiki-code-body")), "Code highlight must finish loading");
  check(feed.querySelector("img")?.naturalHeight > 0, "Image preview must finish loading");
  feed.scrollTop = feed.scrollHeight;
  await frame();
  check(metrics().height === 56, "Empty textarea must be 56px tall");
  const short = metrics();
  for (const value of ["a", "ab", "abc", "ab", "a"]) {
    await edit(value);
    unchanged(short, "single-line editing");
  }
  textarea.select();
  await edit("", false, "deleteContentBackward");
  unchanged(short, "select-all deletion of a single-line draft");
  check(textarea.value === "", "Select-all deletion must clear the draft");

  const multiline = Array.from({ length: 6 }, (_, index) => `Line ${index}`).join("\n");
  await setDraft(multiline);
  check(metrics().height > 56 && metrics().height < 168, "Six lines must grow within the height bounds");
  feed.scrollTop = feed.scrollHeight;
  await frame();
  const bottom = metrics();
  await edit(multiline.replace("Line 0", "Line "));
  unchanged(bottom, "deleting one character from a multiline draft at the bottom");
  const equalHeightScrollDelta = metrics().scrollTop - bottom.scrollTop;
  textarea.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
  await edit(multiline.replace("Line 0", "\u62fc\u97f3"), true);
  unchanged(bottom, "composition update");
  await edit(multiline.replace("Line 0", "\u4e2d"), true);
  unchanged(bottom, "shorter composition update");
  textarea.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "\u4e2d" }));
  await frame();
  unchanged(bottom, "composition end");

  feed.scrollTop = 400;
  await frame();
  const history = metrics();
  await edit(multiline);
  unchanged(history, "editing while reading earlier history");
  await setDraft(Array.from({ length: 20 }, () => "Long draft line").join("\n"));
  check(metrics().height === 168, "Long draft must stop growing at 168px");
  check(textarea.scrollHeight > textarea.clientHeight, "Long draft must scroll internally");
  await setDraft("Line 1\nLine 2\nLine 3");
  check(metrics().height > 56 && metrics().height < 168, "Deleting lines must shrink the textarea");
  await setDraft("");
  check(metrics().height === 56, "Clearing the draft must restore 56px height");
  return { width: window.innerWidth, multilineHeight: bottom.height, equalHeightScrollDelta };
}

async function checkNativeEditing(win) {
  const read = () => win.webContents.executeJavaScript(`(async () => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const textarea = document.querySelector('textarea');
    const feed = document.querySelector('.feed');
    return { value: textarea.value, height: textarea.getBoundingClientRect().height,
      scrollTop: feed.scrollTop, recordTop: feed.querySelector('.feed-item').getBoundingClientRect().top,
      previewRequests: window.getPreviewRequests() };
  })()`);
  await win.webContents.executeJavaScript("document.querySelector('textarea').focus({ preventScroll: true })");
  win.webContents.focus();
  const baseline = await read();
  let value = "";
  for (const character of "hello") {
    await win.webContents.insertText(character);
    value += character;
    assert.deepEqual(await read(), { ...baseline, value }, "Native single-line typing must preserve history and loaded previews");
  }
  win.webContents.selectAll();
  win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Backspace" });
  win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Backspace" });
  assert.deepEqual(await read(), { ...baseline, value: "" }, "Native select-all and Backspace must preserve history and loaded previews");
  return { singleLineScrollDelta: 0, selectAllDeleteScrollDelta: 0 };
}

app.whenReady().then(async () => {
  let server;
  let win;
  let exitCode = 0;
  const deadline = setTimeout(() => { console.error("Composer layout test timed out"); app.exit(1); }, 45_000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({
      configFile: path.join(root, "vite.config.ts"),
      server: { host: "127.0.0.1", port: 0, open: false },
    });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const pageErrors = [];
    win.webContents.on("console-message", (event) => {
      if (event.level === "error") pageErrors.push(event.message);
    });
    win.setContentSize(1100, 800);
    const address = server.httpServer.address();
    await win.loadURL(`http://127.0.0.1:${address.port}/test/composer-layout.html`);
    const desktop = await win.webContents.executeJavaScript(`(${checkLayout.toString()})()`);
    const desktopEditing = await checkNativeEditing(win);
    await win.webContents.executeJavaScript("window.setComposerDraft('word '.repeat(24))");
    await new Promise((resolve) => setTimeout(resolve, 100));
    const wideHeight = await win.webContents.executeJavaScript("document.querySelector('textarea').getBoundingClientRect().height");
    win.setContentSize(420, 700);
    await new Promise((resolve) => setTimeout(resolve, 100));
    const narrowHeight = await win.webContents.executeJavaScript("document.querySelector('textarea').getBoundingClientRect().height");
    assert.ok(narrowHeight > wideHeight, "Width changes must resize a wrapped draft without editing it");
    const narrow = await win.webContents.executeJavaScript(`(${checkLayout.toString()})()`);
    const narrowEditing = await checkNativeEditing(win);
    assert.deepEqual(pageErrors, [], "The layout fixture must have no renderer errors");
    if (process.env.EIDOS_LAYOUT_SCREENSHOT) {
      await writeFile(process.env.EIDOS_LAYOUT_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    }
    console.log(JSON.stringify({ desktop, desktopEditing, narrow, narrowEditing, wideHeight, narrowHeight }));
    console.log("Composer native layout and feed scroll regression checks passed.");
  } catch (error) {
    console.error(error);
    exitCode = 1;
  } finally {
    clearTimeout(deadline);
    win?.destroy();
    await server?.close();
    await rm(temporaryRoot, { recursive: true, force: true });
    app.exit(exitCode);
  }
});

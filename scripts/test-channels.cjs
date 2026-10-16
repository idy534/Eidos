const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-channels-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win, exitCode = 0;
  const timeout = setTimeout(() => { console.error("Channel UI timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, width: 1100, height: 1000, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    await win.loadURL(`${server.resolvedUrls.local[0]}test/channels.html`);
    const evaluate = code => win.webContents.executeJavaScript(code);
    const settle = () => evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    for (let i = 0; i < 100 && !await evaluate("Boolean(document.querySelector('.channel-form'))"); i++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(await evaluate("Boolean(document.querySelector('.channel-form'))"), true);
    assert.equal(await evaluate("document.querySelector('input[type=password]').value"), "");
    assert.ok(await evaluate("document.querySelector('.channel-check input').getBoundingClientRect().width <= 24"));
    for (const width of [1440, 1024, 768, 320]) {
      win.setSize(width, 1000); await settle();
      const dimensions = await evaluate("({width:document.querySelector('.settings-content-col').clientWidth,scroll:document.querySelector('.settings-content-col').scrollWidth})");
      assert.ok(dimensions.scroll <= dimensions.width + 1, `Channel settings overflow at ${width}px: ${JSON.stringify(dimensions)}`);
    }
    win.setSize(1100, 1000); await settle();
    if (process.env.EIDOS_CHANNEL_SCREENSHOT) await writeFile(process.env.EIDOS_CHANNEL_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    await evaluate("document.querySelector('.channel-form button[type=submit]').click()"); await settle();
    assert.equal(await evaluate("window.channelSaved.expectedRevision"), 1);
    assert.equal(await evaluate("'clientSecret' in window.channelSaved"), false);
    await evaluate("document.querySelector('.channel-form input').focus()");
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" }); await settle();
    assert.equal(await evaluate("document.activeElement.tagName==='INPUT'"), true);
    assert.deepEqual(errors, []);
    console.log("Channel UI passed: masked credentials, save, keyboard and 4 viewport sizes");
  } catch (error) { exitCode = 1; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(exitCode); }
});

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
    for (let i = 0; i < 100 && !await evaluate("Boolean([...document.querySelectorAll('button')].find(button => button.textContent.trim()==='设置'))"); i++) await new Promise(resolve => setTimeout(resolve, 50));
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.trim()==='设置').click()");
    await settle();
    assert.equal(await evaluate("Boolean(document.querySelector('.channel-form'))"), true);
    assert.equal(await evaluate("document.querySelector('input[type=password]').value"), "");
    assert.equal(await evaluate("document.querySelectorAll('.channel-form input[role=switch]').length"), 1);
    assert.equal(await evaluate("Boolean(document.querySelector('#field-allow-dm,#field-user-ids,#field-group-ids,#field-robot-code,#field-corp-id'))"), false);
    for (const width of [1440, 1024, 768, 320]) {
      win.setSize(width, 1000); await settle();
      await evaluate("document.querySelector('.model-selector__trigger').scrollIntoView({block:'center'})"); await settle();
      await evaluate("document.querySelector('.model-selector__trigger').click()"); await settle();
      const picker = await evaluate(`(() => {
        const panel=document.querySelector('.model-selector__panel'); const rect=panel.getBoundingClientRect();
        const slider=panel.querySelector('input[type=range]');
        return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:innerWidth,height:innerHeight,
          logo:!!panel.querySelector('img.provider-logo'),padding:getComputedStyle(slider).padding};
      })()`);
      assert.ok(picker.left >= 0 && picker.right <= picker.width && picker.top >= 0 && picker.bottom <= picker.height, `Picker outside viewport at ${width}px: ${JSON.stringify(picker)}`);
      assert.equal(picker.logo, true);
      assert.equal(picker.padding, "0px");
      win.webContents.focus();
      win.webContents.sendInputEvent({type:"keyDown",keyCode:"Escape"});
      win.webContents.sendInputEvent({type:"keyUp",keyCode:"Escape"}); await settle();
      assert.equal(await evaluate("document.activeElement.classList.contains('model-selector__trigger')"), true);

      const dimensions = await evaluate("({width:document.querySelector('.settings-content-col').clientWidth,scroll:document.querySelector('.settings-content-col').scrollWidth})");
      assert.ok(dimensions.scroll <= dimensions.width + 1, `Channel settings overflow at ${width}px: ${JSON.stringify(dimensions)}`);
    }
    win.setSize(1100, 1000); await settle();
    if (process.env.EIDOS_CHANNEL_SCREENSHOT) await writeFile(process.env.EIDOS_CHANNEL_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    await evaluate("document.querySelector('.channel-form button[type=submit]').click()"); await settle();
    assert.equal(await evaluate("window.channelSaved.expectedRevision"), 1);
    assert.equal(await evaluate("'clientSecret' in window.channelSaved"), false);
    assert.deepEqual(await evaluate("Object.keys(window.channelSaved.config).sort()"), ["approvalMode", "clientId", "enabled", "modelId"]);
    await evaluate("document.querySelector('#field-client-id').focus()");
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" }); await settle();
    assert.equal(await evaluate("document.activeElement.id"), "field-client-secret");
    const selectMode = async value => {
      await evaluate(`(() => { const select=document.querySelector('#field-approval-mode'); select.value=${JSON.stringify(value)}; select.dispatchEvent(new Event('change',{bubbles:true})); })()`);
      await settle();
    };
    await selectMode("full_access");
    assert.equal(await evaluate("document.querySelector('#field-approval-mode').value"), "manual");
    assert.equal(await evaluate("document.activeElement.textContent.trim()"), "取消");
    assert.equal(await evaluate("Boolean(document.querySelector('[role=alertdialog] .btn--danger'))"), true);
    for (const width of [1440, 1024, 768, 320]) {
      win.setSize(width, 1000); await settle();
      assert.ok(await evaluate("(() => { const r=document.querySelector('[role=alertdialog]').getBoundingClientRect(); return r.left>=-1 && r.right<=innerWidth+1 && r.top>=-1 && r.bottom<=innerHeight+1; })()"), `Full access dialog overflow at ${width}px`);
    }
    win.setSize(1100, 1000); await settle();
    if (process.env.EIDOS_CHANNEL_FULL_ACCESS_SCREENSHOT) await writeFile(process.env.EIDOS_CHANNEL_FULL_ACCESS_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    await evaluate("[...document.querySelectorAll('[role=alertdialog] button')].find(button=>button.textContent.trim()==='取消').click()"); await settle();
    assert.equal(await evaluate("document.querySelector('#field-approval-mode').value"), "manual");
    await selectMode("full_access");
    await evaluate("document.querySelector('[role=alertdialog] .btn--danger').click()"); await settle();
    assert.equal(await evaluate("document.querySelector('#field-approval-mode').value"), "full_access");
    await evaluate("document.querySelector('.channel-form button[type=submit]').click()"); await settle();
    assert.equal(await evaluate("window.channelSaved.fullAccessConfirmation"), "full-access-v1");
    await selectMode("auto_review");
    await evaluate("document.querySelector('.channel-form button[type=submit]').click()"); await settle();
    assert.equal(await evaluate("'fullAccessConfirmation' in window.channelSaved"), false);
    assert.deepEqual(errors, []);
    console.log("Channel UI passed: masked credentials, save, keyboard, full access confirmation and 4 viewport sizes");
  } catch (error) { exitCode = 1; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(exitCode); }
});

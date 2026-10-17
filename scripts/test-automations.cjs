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
    for (let i = 0; i < 100 && !await evaluate("Boolean(document.querySelector('.automation-task-item'))"); i++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.automation-task-group')].map(group => ({title:group.querySelector('h3').textContent,count:group.querySelectorAll('.automation-task-item').length}))"),
      [{ title: "即将执行", count: 2 }, { title: "已完成与暂停", count: 2 }]);
    await evaluate("document.querySelector('.automation-task-item').click()"); await settle();
    assert.equal(await evaluate("document.querySelector('.automation-detail-view').textContent.includes('替我审批')"), true);
    const firedTask = "[...document.querySelectorAll('.automation-task-item')].find(button=>button.textContent.includes('一次性提醒'))";
    assert.equal(await evaluate(`${firedTask}.textContent.includes('已结束 · 一次性')`), true);
    win.webContents.focus();
    await evaluate(`${firedTask}.focus()`);
    assert.equal(await evaluate("document.activeElement.textContent.includes('一次性提醒')"), true);
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Enter" });
    win.webContents.sendInputEvent({ type: "char", keyCode: "Enter" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Enter" }); await settle();
    assert.equal(await evaluate("document.querySelector('.automation-detail-view h2').textContent"), "一次性提醒");
    if (process.env.EIDOS_AUTOMATION_SCREENSHOT) await writeFile(process.env.EIDOS_AUTOMATION_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    await evaluate("document.querySelector('[aria-label=搜索任务]').click()"); await settle();
    await evaluate(`(() => {
      const input=document.querySelector('input[type=search]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'检查');
      input.dispatchEvent(new Event('input',{bubbles:true}));
    })()`); await settle();
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.automation-task-group')].map(group=>group.querySelectorAll('.automation-task-item').length)"), [1, 1]);
    await evaluate("document.querySelector('[aria-label=搜索任务]').click()"); await settle();
    await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent==='新建任务').click()"); await settle();
    assert.equal(await evaluate("[...document.querySelectorAll('label')].find(label=>label.textContent.startsWith('审批模式')).querySelector('select').value"), "auto_review");
    for (const width of [1440, 1024, 768, 320]) {
      win.setSize(width, 900); await settle();
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

      const dimensions = await evaluate("({width:document.querySelector('.automation-sidebar-col').clientWidth,scroll:document.querySelector('.automation-sidebar-col').scrollWidth})");
      assert.ok(dimensions.scroll <= dimensions.width + 1, `No horizontal overflow at ${width}px: ${JSON.stringify(dimensions)}`);
      assert.equal(await evaluate("document.querySelectorAll('.automation-task-group').length"), 2);
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
    const approvalSelect = "[...document.querySelectorAll('label')].find(label=>label.textContent.startsWith('审批模式')).querySelector('select')";
    const selectFullAccess = async () => {
      await evaluate(`(() => { const select=${approvalSelect}; select.focus();
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,'full_access');
        select.dispatchEvent(new Event('change',{bubbles:true})); })()`);
      await settle();
      await evaluate("Promise.all([...document.querySelectorAll('.modal-backdrop, .modal-dialog')].flatMap(element=>element.getAnimations()).map(animation=>animation.finished))");
    };
    await selectFullAccess();
    assert.equal(await evaluate("document.querySelector('[role=alertdialog] h3').textContent"), "开启完全访问？");
    assert.equal(await evaluate("document.querySelector('[role=alertdialog]').textContent.includes('此计划今后的每次执行')"), true);
    assert.equal(await evaluate("document.activeElement.textContent"), "取消");
    assert.equal(await evaluate(`${approvalSelect}.value`), "auto_review");
    if (process.env.EIDOS_AUTOMATION_FULL_ACCESS_SCREENSHOT) await writeFile(process.env.EIDOS_AUTOMATION_FULL_ACCESS_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    for (const width of [1440, 1024, 768, 320]) {
      win.setSize(width, 900); await settle();
      const bounds = await evaluate("(() => { const dialog=document.querySelector('[role=alertdialog]'); const rect=dialog.getBoundingClientRect(); return {left:rect.left,right:rect.right,width:innerWidth,scroll:dialog.scrollWidth,client:dialog.clientWidth}; })()");
      assert.ok(bounds.left >= 0 && bounds.right <= bounds.width + 1 && bounds.scroll <= bounds.client + 1, `Confirmation fits at ${width}px: ${JSON.stringify(bounds)}`);
    }
    win.setSize(1100, 850); await settle();
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" }); await settle();
    assert.equal(await evaluate("Boolean(document.querySelector('[role=alertdialog]'))"), false);
    assert.equal(await evaluate(`${approvalSelect}.value`), "auto_review");
    assert.equal(await evaluate(`document.activeElement===${approvalSelect}`), true);
    await selectFullAccess();
    await evaluate("document.querySelector('[role=alertdialog] .btn--danger').click()"); await settle();
    assert.equal(await evaluate(`${approvalSelect}.value`), "full_access");
    assert.equal(await evaluate("window.automationTestState.commands.some(command=>command.mode==='create')"), false);
    await evaluate("document.querySelector('button[type=submit]').click()"); await settle();
    const saved = await evaluate("window.automationTestState.commands.find(command=>command.mode==='create')");
    assert.equal(saved.approvalMode, "full_access");
    assert.equal(saved.name, "每日检查");
    assert.equal("sessionId" in saved, false);
    assert.equal(await evaluate("window.automationTestState.requests.find(request=>request.command.mode==='create').fullAccessConfirmation"), "full-access-v1");
    assert.equal(await evaluate("document.querySelector('.automation-detail-view').textContent.includes('完全访问')"), true);
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" }); await settle();
    assert.equal(await evaluate("document.activeElement.tagName==='BUTTON' || document.activeElement.tagName==='SELECT' || document.activeElement.tagName==='INPUT'"), true);
    await win.loadURL(`${server.resolvedUrls.local[0]}test/automations.html?source`);
    await settle();
    const sourceRow = '[data-session-id="66666666-6666-6666-6666-666666666666"]';
    assert.equal(await evaluate(`document.querySelector('${sourceRow} .session-title').textContent`), "检查部署");
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.session-automation-indicator')).opacity"), "0");
    const hoverPoint = await evaluate(`(() => { const rect=document.querySelector('${sourceRow} button').getBoundingClientRect(); return {x:Math.round(rect.left+rect.width/2),y:Math.round(rect.top+rect.height/2)}; })()`);
    win.webContents.sendInputEvent({ type: "mouseMove", ...hoverPoint });
    await new Promise(resolve => setTimeout(resolve, 400)); await settle();
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.session-automation-indicator')).opacity"), "1");
    const iconSize = await evaluate("document.querySelector('.session-automation-indicator svg').getBoundingClientRect().width");
    assert.ok(iconSize >= 10 && iconSize <= 16, `Session automation icon has a compact size: ${iconSize}`);
    assert.equal(await evaluate("document.querySelector('[role=tooltip]').textContent.includes('会话来源')"), true);
    assert.equal(await evaluate("document.querySelector('[role=tooltip]').textContent.includes('manual:')"), false);
    if (process.env.EIDOS_AUTOMATION_SOURCE_SCREENSHOT) await writeFile(process.env.EIDOS_AUTOMATION_SOURCE_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    const sourcePoint = await evaluate("(() => { const rect=document.querySelector('[data-source-automation-id]').getBoundingClientRect(); return {x:Math.round(rect.left+rect.width/2),y:Math.round(rect.top+rect.height/2)}; })()");
    win.webContents.sendInputEvent({ type: "mouseMove", ...sourcePoint });
    win.webContents.sendInputEvent({ type: "mouseDown", button: "left", ...sourcePoint, clickCount: 1 });
    win.webContents.sendInputEvent({ type: "mouseUp", button: "left", ...sourcePoint, clickCount: 1 });
    await settle();
    assert.equal(await evaluate("document.querySelector('.automation-detail-view h2')?.textContent"), "检查部署");
    assert.deepEqual(errors, []);
    console.log("Automation UI passed: task groups, search, full-access confirmation/cancel/save/focus, keyboard, 4 viewport sizes and Session source icon/tooltip/detail navigation");
  } catch (error) { exitCode = 1; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await server?.close(); await rm(temporaryRoot, { recursive: true, force: true }); app.exit(exitCode); }
});

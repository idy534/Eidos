const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-goal-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win, failed = false;
  const timeout = setTimeout(() => { console.error("Goal UI timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false }, logLevel: "error" });
    await server.listen();
    win = new BrowserWindow({ show: false, width: 1100, height: 760, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    await win.loadURL(`${server.resolvedUrls.local[0]}test/goals.html`);
    const evaluate = code => win.webContents.executeJavaScript(code);
    const settle = () => evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    const until = async (expression) => {
      for (let index = 0; index < 100; index++) { if (await evaluate(expression)) return; await new Promise(resolve => setTimeout(resolve, 40)); }
      throw new Error(`Goal UI state did not appear: ${expression}`);
    };
    const directory = process.env.EIDOS_GOAL_SCREENSHOTS;
    if (directory) await mkdir(directory, { recursive: true });
    const capture = async (name) => { await evaluate("Promise.all(document.getAnimations().filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})))"); if (directory) await writeFile(path.join(directory, name), (await win.webContents.capturePage()).toPNG()); };
    await until("Boolean(document.querySelector('.composer-add'))");
    assert.equal(await evaluate("Boolean(document.querySelector('.goal-panel'))"), false);
    await evaluate("document.querySelector('.composer-add').click()"); await settle();
    await until("Boolean(document.querySelector('.input-picker__action-btn--goal'))");
    assert.equal(await evaluate("document.querySelector('.input-picker__action-btn--goal').disabled"), false);
    await capture("menu-1100.png");
    await evaluate("document.querySelector('.input-picker__action-btn--goal').click()"); await settle();
    assert.equal(await evaluate("document.querySelector('.composer-plan-badge').textContent.trim()"), "目标模式");
    assert.equal(await evaluate("document.querySelector('#task-input').value"), "完成登录模块的全部修复，并确认相关测试通过。");
    assert.equal(await evaluate("Boolean(document.querySelector('[role=alertdialog]'))"), false);
    assert.equal(await evaluate("Boolean(document.querySelector('.approval-mode-selector').compareDocumentPosition(document.querySelector('.composer-plan-badge')) & Node.DOCUMENT_POSITION_FOLLOWING)"), true);
    await capture("entry-1100.png");
    if (directory) {
      const rect = await evaluate("(() => { const rect=document.querySelector('.composer').getBoundingClientRect(); return {x:Math.floor(rect.x),y:Math.floor(rect.y),width:Math.ceil(rect.width),height:Math.ceil(rect.height)}; })()");
      await writeFile(path.join(directory, "entry-composer.png"), (await win.webContents.capturePage(rect)).toPNG());
    }
    await evaluate("document.querySelector('.composer-add').click()"); await settle();
    await evaluate("document.querySelector('.input-picker__action-btn--plan').click()"); await settle();
    assert.equal(await evaluate("document.querySelector('.composer-plan-badge').textContent.trim()"), "Plan 模式");
    await evaluate("document.querySelector('#task-input').focus(); document.querySelector('#task-input').select()");
    win.webContents.focus();
    await win.webContents.insertText("/goal"); await settle();
    await until("Boolean(document.querySelector('[role=option]'))");
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Enter" });
    win.webContents.sendInputEvent({ type: "char", keyCode: "Enter" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Enter" }); await settle();
    assert.equal(await evaluate("document.querySelector('.composer-plan-badge').textContent.trim()"), "目标模式");
    assert.equal(await evaluate("document.querySelector('#task-input').value"), "");
    for (const width of [1440, 1100, 1024, 768, 420, 320]) {
      win.setSize(width, 760); await settle();
      assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true, `Composer overflow at ${width}`);
      assert.equal(await evaluate("(() => { const label=document.querySelector('.composer-plan-badge__text'); const rect=label.getBoundingClientRect(); const center=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2); return rect.left>=0 && rect.right<=innerWidth && !!center?.closest('.composer-plan-badge'); })()"), true, `Mode label is clipped or covered at ${width}`);
      if ([1100, 420].includes(width)) await capture(`composer-${width}.png`);
      for (const state of ["active", "waiting_approval", "blocked", "paused", "complete"]) {
        await evaluate(`window.goalTest.state(${JSON.stringify(state)})`); await settle();
        const expanded = await evaluate("document.querySelector('.goal-panel__toggle').getAttribute('aria-expanded') === 'true'");
        if (!expanded) { await evaluate("document.querySelector('.goal-panel__toggle').click()"); await settle(); }
        const dimensions = await evaluate("({width:innerWidth, scroll:document.documentElement.scrollWidth, text:document.querySelector('.goal-panel').textContent})");
        assert.ok(dimensions.scroll <= dimensions.width + 1, `Horizontal overflow at ${width}px: ${JSON.stringify(dimensions)}`);
        assert.equal(await evaluate("Boolean(document.querySelector('[role=alertdialog], .goal-editor, input[type=number]'))"), false);
        if (state === "waiting_approval") assert.ok(dimensions.text.includes("等待审批"));
        if (state === "complete") assert.ok(dimensions.text.includes("相关测试退出码为零"));
        if ([1100, 420].includes(width)) await capture(`${state}-${width}.png`);
      }
    }
    await evaluate("document.querySelector('.composer-plan-badge').click()"); await settle();
    assert.equal(await evaluate("Boolean(document.querySelector('.composer-plan-badge'))"), false);
    assert.deepEqual(errors, []);
    console.log("Goal UI passed: plus menu, /goal keyboard entry, exclusive modes, Composer access mode, no setup dialog, 5 states and 6 widths.");
  } catch (error) { failed = true; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(failed ? 1 : 0); }
});

const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");

const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-sidebar-pinning-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});

app.whenReady().then(async () => {
  let server;
  let win;
  let exitCode = 0;
  const deadline = setTimeout(() => { console.error("Sidebar pinning test timed out"); app.exit(1); }, 45_000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, width: 1100, height: 750, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    await win.loadURL(`${server.resolvedUrls.local[0]}test/sidebar-pinning.html`);
    const evaluate = expression => win.webContents.executeJavaScript(expression);
    const settle = () => evaluate(`(async () => {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await Promise.all(document.getAnimations()
        .filter(animation => animation.effect?.getTiming().iterations !== Infinity)
        .map(animation => animation.finished.catch(() => {})));
    })()`);
    for (let attempt = 0; attempt < 100 && !await evaluate("window.sidebarTestState?.loaded === true"); attempt++) {
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(await evaluate("window.sidebarTestState?.loaded"), true, "Fixture must load the authoritative pins");
    const forkRow = '[data-session-id="44444444-4444-4444-4444-444444444444"]';
    assert.equal(await evaluate(`document.querySelector(${JSON.stringify(forkRow)} + ' .session-title')?.textContent`), "排查模型错误", "A fork appears before its first Run with its source title");
    const forkOpacity = () => evaluate(`getComputedStyle(document.querySelector(${JSON.stringify(forkRow)} + ' .session-fork-indicator')).opacity`);
    assert.equal(await forkOpacity(), "0", "Fork marker is hidden before hover");
    const forkPoint = await evaluate(`(() => { const bounds = document.querySelector(${JSON.stringify(forkRow)} + ' button').getBoundingClientRect(); return { x: Math.round(bounds.left + 10), y: Math.round(bounds.top + bounds.height / 2) }; })()`);
    win.webContents.sendInputEvent({ type: "mouseMove", ...forkPoint });
    await settle();
    assert.equal(await forkOpacity(), "1", "Native hover reveals the fork marker");
    win.webContents.sendInputEvent({ type: "mouseMove", x: 1, y: 1 });
    await settle();
    assert.equal(await forkOpacity(), "0", "Leaving the row hides the marker again");
    const click = async (selector, button = "left") => {
      const point = await evaluate(`(() => {
        const element = document.querySelector(${JSON.stringify(selector)});
        if (!element) throw new Error('Missing selector: ' + ${JSON.stringify(selector)});
        const rect = element.getBoundingClientRect();
        return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
      })()`);
      win.webContents.sendInputEvent({ type: "mouseMove", ...point });
      win.webContents.sendInputEvent({ type: "mouseDown", button, ...point, clickCount: 1 });
      win.webContents.sendInputEvent({ type: "mouseUp", button, ...point, clickCount: 1 });
      await settle();
    };
    const project = '[aria-label="Eidos"] .workspace-toggle';
    await click(project, "right");
    assert.equal(await evaluate("document.querySelector('[role=menu]')?.closest('.sidebar') === null"), true, "Context menu must float outside the sidebar");
    await click('[role="menuitem"]');
    assert.equal(await evaluate("document.querySelector('.sidebar-pinned [aria-label=Eidos]') !== null"), true, "Right click must pin the project");
    const session = '[data-session-id="22222222-2222-2222-2222-222222222222"] button';
    await evaluate(`document.querySelector(${JSON.stringify(session)}).focus()`);
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "F10", modifiers: ["shift"] });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "F10", modifiers: ["shift"] });
    await settle();
    await click('[role="menuitem"]');
    assert.equal(await evaluate("window.sidebarTestState.pins"), 2, "Shift+F10 must also support Session pinning");
    assert.equal(await evaluate("document.activeElement?.closest('[data-session-id]')?.dataset.sessionId"), "22222222-2222-2222-2222-222222222222", "Keyboard focus follows the moved Session");
    assert.equal(await evaluate("document.querySelectorAll('[data-session-id=\"22222222-2222-2222-2222-222222222222\"]').length"), 1, "Session must appear once");
    assert.equal(await evaluate("document.querySelector('.sidebar-pinned [aria-label=\"等待批准\"]') !== null"), true, "Pinned Session keeps its status indicator");
    assert.equal(await evaluate("document.querySelectorAll('button[aria-current=page]').length"), 1, "Selection must remain unique");
    await click(project);
    await click(session, "right");
    await click('[role="menuitem"]');
    assert.equal(await evaluate("document.activeElement?.closest('[data-project-id]')?.dataset.projectId"), "project-0", "Unpinning into a collapsed project focuses its title");
    assert.equal(await evaluate(`document.querySelector(${JSON.stringify(project)}).getAttribute('aria-expanded')`), "false", "Unpinning preserves the collapsed project");
    await click(project);
    await click(session, "right");
    await click('[role="menuitem"]');
    await evaluate("document.querySelector('.sidebar nav').scrollTop = 100");
    await settle();
    const scrollBefore = await evaluate("document.querySelector('.sidebar nav').scrollTop");
    const visibleProject = await evaluate(`(() => {
      const nav = document.querySelector('.sidebar nav').getBoundingClientRect();
      return [...document.querySelectorAll('.workspace-group:not([aria-label=Eidos]) .workspace-toggle')]
        .find(element => { const rect = element.getBoundingClientRect(); return rect.top > nav.top && rect.bottom < nav.bottom; })
        ?.closest('section').getAttribute('aria-label');
    })()`);
    assert.ok(visibleProject);
    await click(`[aria-label="${visibleProject}"] .workspace-toggle`);
    assert.equal(await evaluate("document.querySelector('.sidebar nav').scrollTop"), scrollBefore, "Project browsing must keep scroll position");
    await evaluate("document.querySelector('.sidebar nav').scrollTop = 0");
    win.setSize(420, 750);
    await settle();
    await click(session, "right");
    const bounds = await evaluate("(() => { const r = document.querySelector('[role=menu]').getBoundingClientRect(); return { left: r.left, right: r.right, bottom: r.bottom, width: innerWidth, height: innerHeight }; })()");
    assert.ok(bounds.left >= 0 && bounds.right <= bounds.width && bounds.bottom <= bounds.height, "Menu fits a narrow window");
    await click('[role="menuitem"]');
    assert.equal(await evaluate("window.sidebarTestState.pins"), 1, "Unpin returns the Session to its project");
    await click(project, "right");
    await click('[role="menuitem"]');
    assert.equal(await evaluate("document.querySelector('.sidebar-pinned')"), null, "Last unpin hides the section");
    assert.deepEqual(errors, [], "Fixture must render without CSP or React errors");
    if (process.env.EIDOS_SIDEBAR_SCREENSHOT) {
      win.setSize(1100, 750);
      await settle();
      await click(project, "right");
      await click('[role="menuitem"]');
      await click(session, "right");
      await click('[role="menuitem"]');
      await writeFile(process.env.EIDOS_SIDEBAR_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    }
    console.log("Sidebar native right-click, keyboard, mixed placement, status, scroll and narrow-window checks passed.");
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

// Isolated real-window check of the team entry, roster card and team panel.
// EIDOS_TEAM_SCREENSHOTS=<dir> keeps screenshots for review.
const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-team-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  let server, win, failed = false;
  const timeout = setTimeout(() => { console.error("Team UI timed out"); app.exit(1); }, 60000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false }, logLevel: "error" });
    await server.listen();
    win = new BrowserWindow({ show: false, width: 1280, height: 820, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", (event) => { if (event.level === "error") errors.push(event.message); });
    await win.loadURL(`${server.resolvedUrls.local[0]}test/teams.html`);
    const evaluate = (code) => win.webContents.executeJavaScript(code);
    const settle = () => evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    const until = async (expression) => {
      for (let index = 0; index < 150; index++) { if (await evaluate(expression)) return; await new Promise((resolve) => setTimeout(resolve, 40)); }
      throw new Error(`Team UI state did not appear: ${expression}`);
    };
    const directory = process.env.EIDOS_TEAM_SCREENSHOTS;
    if (directory) await mkdir(directory, { recursive: true });
    const capture = async (name) => { if (directory) await writeFile(path.join(directory, name), (await win.webContents.capturePage()).toPNG()); };
    const noOverflow = async (label) => assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true, `Horizontal overflow: ${label}`);

    // Entry: plus menu and /team.
    await until("Boolean(document.querySelector('.composer-add'))");
    await evaluate("document.querySelector('.composer-add').click()"); await settle();
    await until("Boolean(document.querySelector('.input-picker__action-btn--team'))");
    await capture("entry-menu.png");
    await evaluate("document.querySelector('.input-picker__action-btn--team').click()"); await settle();
    assert.equal(await evaluate("document.querySelector('.composer-plan-badge').textContent.trim()"), "团队模式");
    assert.equal(await evaluate("document.querySelector('#task-input').placeholder"), "描述任务，Eidos 会先提议团队成员与分工");
    await capture("entry-badge.png");

    // Roster card.
    await evaluate("window.teamTest.show('roster')"); await settle();
    await until("Boolean(document.querySelector('.team-roster'))");
    for (const width of [1280, 768, 420, 320]) {
      win.setSize(width, 820); await settle();
      await noOverflow(`roster ${width}`);
      assert.equal(await evaluate("(() => { const b=[...document.querySelectorAll('.team-roster button')].find(x=>x.textContent.includes('组建团队')); const r=b.getBoundingClientRect(); return r.right<=innerWidth && r.bottom<=innerHeight; })()"), true, `Confirm button off screen at ${width}`);
      await capture(`roster-${width}.png`);
    }
    win.setSize(1280, 820); await settle();
    await evaluate("[...document.querySelectorAll('.team-roster button')].find(x=>x.textContent.includes('组建团队')).click()"); await settle();

    // Team panel: three views, member detail, narrow widths.
    await until("Boolean(document.querySelector('.team-panel'))");
    await until("document.querySelectorAll('.team-message').length === 4");
    assert.equal(await evaluate("Boolean(document.querySelector('.input-picker__action-btn--team'))"), false);
    await capture("panel-board.png");
    for (const label of ["成员", "任务"]) {
      await evaluate(`[...document.querySelectorAll('.team-panel__view')].find(x=>x.textContent==='${label}').click()`); await settle();
      await capture(`panel-${label === "成员" ? "members" : "tasks"}.png`);
    }
    assert.equal(await evaluate("Boolean(document.querySelector('.team-task__gate'))"), true);
    await evaluate("[...document.querySelectorAll('.team-panel__view')].find(x=>x.textContent==='成员').click()"); await settle();
    await evaluate("[...document.querySelectorAll('.team-member')].find(x=>x.textContent.includes('builder')).click()"); await settle();
    await until("Boolean(document.querySelector('.team-member-detail'))");
    await capture("panel-member.png");
    await evaluate("document.querySelector('.team-member-detail__header button').click()"); await settle();
    for (const width of [1024, 760, 420, 320]) {
      win.setSize(width, 820); await settle();
      await noOverflow(`panel ${width}`);
      const narrow = await evaluate("getComputedStyle(document.querySelector('.team-panel__view-select')).display !== 'none'");
      const dock = await evaluate("document.querySelector('.team-panel').getBoundingClientRect().width");
      assert.equal(narrow, dock <= 352, `View switcher at dock width ${dock}`);
      await capture(`panel-${width}.png`);
    }
    assert.deepEqual(errors, []);
    console.log("Team UI passed: plus menu and badge, roster card at 4 widths, board/members/tasks views, member detail and 4 panel widths.");
  } catch (error) { failed = true; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(failed ? 1 : 0); }
});

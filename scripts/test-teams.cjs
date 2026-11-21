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
    // Expand one roster member drawer to verify full brief & subtask list layout before confirming.
    await evaluate("document.querySelector('.team-roster__expand-btn').click()"); await settle();
    await until("Boolean(document.querySelector('.team-roster__drawer .team-roster__brief'))");
    await capture("roster-expanded.png");
    await evaluate("[...document.querySelectorAll('.team-roster button')].find(x=>x.textContent.includes('组建团队')).click()"); await settle();

    // Side dock (420px): three views, member detail, narrow widths.
    await until("Boolean(document.querySelector('.team-panel'))");
    await until("document.querySelectorAll('.team-message').length === 3");
    assert.equal(await evaluate("Boolean(document.querySelector('.input-picker__action-btn--team'))"), false);
    assert.equal(await evaluate("Boolean(document.querySelector('.team-rail-body--direct'))"), true);
    await capture("panel-dock-board.png");
    // Toggle system logs on and verify no message gets squashed.
    await evaluate("document.querySelector('.team-filter-chip--system').click()"); await settle();
    await until("document.querySelectorAll('.team-message').length === 4");
    await capture("panel-dock-board-system.png");
    await evaluate("document.querySelector('.team-filter-chip--system').click()"); await settle();
    for (const label of ["成员", "任务"]) {
      await evaluate(`[...document.querySelectorAll('.team-panel__view')].find(x=>x.textContent.includes('${label}')).click()`); await settle();
      await capture(`panel-dock-${label === "成员" ? "members" : "tasks"}.png`);
    }
    assert.equal(await evaluate("Boolean(document.querySelector('[data-task-kind=\"gate\"]'))"), true);
    await evaluate("[...document.querySelectorAll('.team-panel__view')].find(x=>x.textContent.includes('成员')).click()"); await settle();
    await evaluate("[...document.querySelectorAll('.team-member')].find(x=>x.textContent.includes('builder')).click()"); await settle();
    await until("Boolean(document.querySelector('.team-member-detail'))");
    assert.equal(await evaluate("Boolean(document.querySelector('.team-member-detail__brief'))"), false);
    await capture("panel-dock-member.png");
    await evaluate("document.querySelector('.team-member-detail__header button').click()"); await settle();

    // Expanded wide dual-pane command center (1024px).
    await evaluate("window.teamTest.show('team-wide')"); await settle();
    win.setSize(1040, 820); await settle();
    await until("Boolean(document.querySelector('.team-panel--wide .team-panel__split'))");
    assert.equal(await evaluate("Boolean(document.querySelector('.team-panel__rail'))"), true);
    assert.equal(await evaluate("Boolean(document.querySelector('.team-panel__views'))"), false);
    await noOverflow("wide-board 1040");
    await capture("panel-wide-board.png");

    // Hover a member in the right rail and verify the multi-line structured tooltip renders.
    const hoverBox = await evaluate("(() => { const b=[...document.querySelectorAll('.team-member')].find(x=>x.textContent.includes('builder')); const r=b.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()");
    win.webContents.sendInputEvent({ type: "mouseMove", x: hoverBox.x, y: hoverBox.y });
    await until("Boolean(document.querySelector('.team-detail-tooltip .team-tooltip-card__row'))");
    await new Promise((resolve) => setTimeout(resolve, 160));
    await settle();
    await capture("panel-wide-hover.png");
    win.webContents.sendInputEvent({ type: "mouseMove", x: 10, y: 10 });
    await settle();

    // Open inline reject/reopen form in the right rail and verify stacked buttons align beside the 2-row textarea.
    await evaluate("[...document.querySelectorAll('.team-task__actions button')].find(x=>x.textContent.trim()==='退回').click()");
    await until("Boolean(document.querySelector('.team-task__reject textarea'))");
    await settle();
    const rejectLayout = await evaluate("(() => { const ta = document.querySelector('.team-task__reject textarea').getBoundingClientRect(); const btns = [...document.querySelectorAll('.team-task__reject-actions button')].map(b => b.getBoundingClientRect()); return { taRight: ta.right, btn0Left: btns[0].left, btn0Bottom: btns[0].bottom, btn1Top: btns[1].top, btn0X: Math.round(btns[0].left), btn1X: Math.round(btns[1].left) }; })()");
    assert.equal(rejectLayout.btn0Left >= rejectLayout.taRight - 1, true, "Buttons should sit beside textarea");
    assert.equal(rejectLayout.btn1Top >= rejectLayout.btn0Bottom - 1, true, "Cancel button should sit beneath confirm button");
    assert.equal(Math.abs(rejectLayout.btn0X - rejectLayout.btn1X) <= 1, true, "Stacked buttons should align horizontally");
    await capture("panel-wide-task-reason.png");
    await evaluate("[...document.querySelectorAll('.team-task__reject-actions button')].find(x=>x.textContent.trim()==='取消').click()");
    await settle();

    // Clicking a member in the right rail opens embedded detail on the left while keeping the rail.
    await evaluate("[...document.querySelectorAll('.team-member')].find(x=>x.textContent.includes('builder')).click()"); await settle();
    await until("Boolean(document.querySelector('.team-panel__main .team-member-detail'))");
    assert.equal(await evaluate("Boolean(document.querySelector('.team-panel__rail'))"), true);
    await capture("panel-wide-member.png");
    await evaluate("document.querySelector('.team-member-detail__header button').click()"); await settle();

    // Narrow widths fallback check.
    await evaluate("window.teamTest.show('team')"); await settle();
    for (const width of [1024, 760, 420, 320]) {
      win.setSize(width, 820); await settle();
      await noOverflow(`panel ${width}`);
      const dock = await evaluate("document.querySelector('.team-panel').getBoundingClientRect().width");
      const hasSplit = await evaluate("Boolean(document.querySelector('.team-panel__split'))");
      assert.equal(hasSplit, dock >= 760, `Responsive split mismatch at dock width ${dock}`);
      await capture(`panel-${width}.png`);
    }
    assert.deepEqual(errors, []);
    console.log("Team UI passed: entry, roster card, side dock views, wide dual-pane command center, embedded member detail and narrow fallbacks.");
  } catch (error) { failed = true; console.error(error); }
  finally { clearTimeout(timeout); win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(failed ? 1 : 0); }
});

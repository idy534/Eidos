const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");

const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-feed-scroll-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});

// These checks need real Chromium layout, details transitions and scroll clamping.
async function checkScroll() {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  for (let n = 0; !window.startFeed && n < 100; n++) await wait(50);
  check(Boolean(window.startFeed), "Fixture must load");
  window.startFeed();
  await wait(350);
  const feed = document.querySelector(".feed");
  const group = feed.querySelector(".process-group");
  const latest = document.querySelector(".feed-jump-to-bottom");
  const gap = () => feed.scrollHeight - feed.clientHeight - feed.scrollTop;
  const following = () => feed.dataset.followingBottom === "true";
  const atBottom = (label) => {
    check(Math.abs(gap()) <= 2, `${label}: bottom gap ${gap()}`);
    check(latest.hidden && following(), `${label}: bottom state must agree with layout`);
  };
  atBottom("initial empty-to-content");

  // Reopening a long process must follow the full 200ms transition, even if
  // no new Item arrives to trigger the old props-only following effect.
  group.querySelector("summary").click();
  await wait(300);
  atBottom("collapsed");
  group.querySelector("summary").click();
  const expansion = [];
  for (let n = 0; n < 24; n++) {
    await frame();
    expansion.push(feed.scrollTop);
    if (n % 3 === 0) window.appendFeed();
  }
  await wait(80);
  atBottom("expanded during streaming");
  check(expansion.every((top, index) => !index || top >= expansion[index - 1] - 1), "Expansion must not bounce upward");

  // Nested tool details share the same content-height observer.
  feed.querySelector(".tool-item > summary").click();
  await wait(300);
  atBottom("nested tool expanded");
  feed.querySelector(".tool-item > summary").click();
  await wait(300);
  atBottom("nested tool collapsed");

  feed.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: -150 }));
  feed.scrollTop -= 250;
  await wait(60);
  const historyTop = feed.scrollTop;
  window.appendFeed();
  await wait(100);
  check(!following() && !latest.hidden, "Upward reading must pause following and expose Latest");
  check(Math.abs(feed.scrollTop - historyTop) <= 1, "Streaming must preserve history position");

  // The browser clamps scrollTop on collapse; this must not silently change
  // the user's paused intent before they reopen the process.
  group.querySelector("summary").click();
  await wait(300);
  group.querySelector("summary").click();
  await wait(300);
  check(!following(), "History must remain paused across collapse and expansion");

  feed.scrollTop = 200;
  await wait(60);
  const downward = [];
  for (let n = 0; n < 12; n++) {
    feed.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: 24 }));
    feed.scrollTop += 24;
    window.appendFeed();
    await frame();
    downward.push(feed.scrollTop);
  }
  check(downward.every((top, index) => !index || top >= downward[index - 1] - 1), "Downward reading must not bounce upward");
  check(!following(), "Reading downward away from bottom must not force following");

  latest.click();
  window.appendFeed();
  await wait(120);
  atBottom("Latest with incoming content");

  window.addLateImage();
  await wait(100);
  const beforeImage = feed.scrollHeight;
  window.finishLateImage();
  for (let n = 0; n < 100 && !feed.querySelector("img")?.naturalHeight; n++) await wait(30);
  await wait(100);
  check(feed.querySelector("img")?.naturalHeight > 0, "Delayed image must load");
  check(feed.scrollHeight > beforeImage, "Delayed image must change content height");
  atBottom("late image layout");

  window.finishFeed();
  await wait(350);
  check(!group.open, "Terminal run must still auto-collapse its process");
  atBottom("terminal auto-collapse");
  group.querySelector("summary").click();
  await wait(300);
  atBottom("terminal process reopened");
  return { width: innerWidth, finalGap: gap(), expansionFrames: expansion.length, downwardFrames: downward.length };
}

async function checkNativeWheel(win) {
  const before = await win.webContents.executeJavaScript(`(() => {
    const feed = document.querySelector('.feed');
    const rect = feed.getBoundingClientRect();
    return { top: feed.scrollTop, x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
  })()`);
  win.webContents.focus();
  win.webContents.sendInputEvent({ type: "mouseWheel", x: before.x, y: before.y, deltaX: 0, deltaY: 180 });
  const after = await win.webContents.executeJavaScript(`(async () => {
    await new Promise(resolve => setTimeout(resolve, 150));
    const feed = document.querySelector('.feed');
    const top = feed.scrollTop;
    window.appendFeed();
    await new Promise(resolve => setTimeout(resolve, 100));
    return { top, afterUpdate: feed.scrollTop, following: feed.dataset.followingBottom };
  })()`);
  assert.ok(after.top < before.top, "Native upward wheel must move the viewport");
  assert.equal(after.following, "false", "Native upward wheel must pause following");
  assert.ok(Math.abs(after.afterUpdate - after.top) <= 1, "Native wheel reading must survive incoming content");
  return { scrollDelta: after.top - before.top, updateDelta: after.afterUpdate - after.top };
}

app.whenReady().then(async () => {
  let server;
  let win;
  let exitCode = 0;
  const deadline = setTimeout(() => { console.error("Feed scroll test timed out"); app.exit(1); }, 60_000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", (event) => { if (event.level === "error") errors.push(event.message); });
    const results = [];
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 600);
      await win.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/test/feed-scroll.html`);
      const layout = await win.webContents.executeJavaScript(`(${checkScroll.toString()})()`);
      results.push({ ...layout, nativeWheel: await checkNativeWheel(win) });
    }
    assert.deepEqual(errors, [], "Feed fixture must have no renderer errors");
    console.log(JSON.stringify(results));
    console.log("Feed scroll native layout regression checks passed.");
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

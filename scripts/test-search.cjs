const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-search-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});

app.whenReady().then(async () => {
  let server; let win; let code = 0;
  const deadline = setTimeout(() => { console.error("Search verification timed out"); app.exit(1); }, 55000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } }); await server.listen();
    win = new BrowserWindow({ show: false, width: 1440, height: 900, webPreferences: { backgroundThrottling: false } });
    const errors = []; win.webContents.on("console-message", (event) => { if (event.level === "error" && !event.message.startsWith("Accessing element.ref was removed in React 19")) errors.push(event.message); });
    const evaluate = (expression) => win.webContents.executeJavaScript(expression);
    const settle = () => evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    const wait = async (expression) => {
      for (let i = 0; i < 120; i++) { if (await evaluate(expression)) return; await new Promise((resolve) => setTimeout(resolve, 40)); }
      throw new Error(`Missing UI state: ${expression}`);
    };
    const key = (keyCode, modifiers = []) => { win.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers }); win.webContents.sendInputEvent({ type: "keyUp", keyCode, modifiers }); };
    const click = async (selector) => {
      await settle();
      await evaluate("Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})))");
      const point = await evaluate(`(() => { const box = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: Math.round(box.x+box.width/2), y: Math.round(box.y+box.height/2) }; })()`);
      win.webContents.sendInputEvent({ type: "mouseMove", ...point });
      win.webContents.sendInputEvent({ type: "mouseDown", button: "left", ...point, clickCount: 1 }); win.webContents.sendInputEvent({ type: "mouseUp", button: "left", ...point, clickCount: 1 });
    };
    const layouts = [];
    const checkCloseCenter = async (selector) => {
      const center = await evaluate(`(() => {
        const button=document.querySelector(${JSON.stringify(selector)});const b=button.getBoundingClientRect();const icon=button.querySelector('svg path').getBoundingClientRect();
        return {x:Math.abs(b.x+b.width/2-icon.x-icon.width/2),y:Math.abs(b.y+b.height/2-icon.y-icon.height/2)};
      })()`);
      assert.ok(center.x < .5 && center.y < .5, `Close icon is centered inside ${selector}: ${JSON.stringify(center)}`);
    };
    const checkFindLayout = async () => {
      const layout = await evaluate(`(() => {
        const bar=document.querySelector('.find-bar'); const rect=bar.getBoundingClientRect();
        const elements=[bar.querySelector('input'),bar.querySelector('[role=status]'),bar.querySelector('[aria-label="上一处"]'),bar.querySelector('[aria-label="下一处"]')];
        const header=document.querySelector('.session-header').getBoundingClientRect();
        return {height:rect.height,left:rect.left,right:rect.right,width:innerWidth,top:rect.top,headerBottom:header.bottom,file:bar.classList.contains('find-bar--file'),
          rem:parseFloat(getComputedStyle(document.documentElement).fontSize),focusOutline:getComputedStyle(bar.querySelector('.find-bar-input')).outlineStyle,inputOutline:getComputedStyle(bar.querySelector('input')).outlineStyle,
          centers:elements.map(e=>{const r=e.getBoundingClientRect();return r.y+r.height/2;})};
      })()`);
      assert.ok(layout.height < 60, 'Find bar stays a single compact line');
      assert.ok(Math.max(...layout.centers)-Math.min(...layout.centers)<2, 'Input, count and arrows share one row');
      assert.ok(layout.left>=0 && layout.right<=layout.width, 'Find overlay fits the panel');
      assert.ok(layout.right-layout.left<=25*layout.rem+1, 'Find overlay uses the narrower width');
      if (!layout.file) assert.ok(layout.top>=layout.headerBottom+4, 'Chat find sits below the title bar');
      assert.equal(layout.focusOutline, 'none', 'Focused input group has no blue outer ring');
      assert.equal(layout.inputOutline, 'none', 'Focused input has no outer ring');
      await checkCloseCenter('.find-bar-close');
      const close = await evaluate(`(() => {
        const bar=document.querySelector('.find-bar');const button=bar.querySelector('.find-bar-close');const rect=button.getBoundingClientRect();
        return {x:Math.round(rect.x+rect.width/2),y:Math.round(rect.y+rect.height/2),normal:getComputedStyle(bar.querySelector('[role=status]')).color,hover:getComputedStyle(bar.querySelector('[aria-label="上一处"]')).color,text:getComputedStyle(bar).backgroundColor};
      })()`);
      const closeColor = "getComputedStyle(document.querySelector('.find-bar-close')).backgroundColor";
      win.webContents.sendInputEvent({ type: 'mouseMove', x: 1, y: 1 });
      await wait(`${closeColor} === ${JSON.stringify(close.normal)}`);
      win.webContents.sendInputEvent({ type: 'mouseMove', x: close.x, y: close.y });
      await wait(`${closeColor} === ${JSON.stringify(close.hover)}`);
      await checkCloseCenter('.find-bar-close');
      assert.equal(await evaluate("getComputedStyle(document.querySelector('.find-bar-close')).color"), close.text, 'Close glyph keeps its light color on hover');
      win.webContents.sendInputEvent({ type: 'mouseMove', x: 1, y: 1 });
      await wait(`${closeColor} === ${JSON.stringify(close.normal)}`);
    };
    for (const width of [1440, 1024, 768, 320]) {
      win.setContentSize(width, 900);
      await win.loadURL(`${server.resolvedUrls.local[0]}test/search.html`);
      await wait("Boolean(document.querySelector('.brand-search'))");
      await wait("Boolean(document.querySelector('#task-input'))");
      await click('#task-input');
      key("k", ["meta"]); await wait("Boolean(document.querySelector('dialog[open] input'))");
      await wait("document.querySelectorAll('.chat-search-result--recent').length === 10");
      assert.equal(await evaluate("document.querySelector('.chat-search-result--recent .chat-search-result-title').textContent"), '搜索与历史定位');
      assert.equal(await evaluate("document.querySelector('.chat-search-result--recent time').textContent"), '30 分钟前');
      assert.equal(await evaluate("[...document.querySelectorAll('.chat-search-result--recent')].every(e=>e.offsetHeight<60)"), true, 'Recent conversations use single-line rows');
      await evaluate("Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})))");
      await settle();
      if (width === 1440 && process.env.EIDOS_SEARCH_SCREENSHOT) await writeFile(process.env.EIDOS_SEARCH_SCREENSHOT.replace(/\.png$/, '-recent.png'), (await win.webContents.capturePage()).toPNG());
      for (let index=0;index<9;index++) key('Down');
      await wait("document.querySelector('.chat-search-result--recent.is-selected .chat-search-result-title')?.textContent === '最近会话 9'");
      assert.equal(await evaluate("(() => {const row=document.querySelector('.chat-search-result--recent.is-selected').getBoundingClientRect();const list=document.querySelector('.chat-search-results').getBoundingClientRect();return row.top>=list.top && row.bottom<=list.bottom;})()"), true, 'Keyboard selection keeps the last recent conversation visible');
      for (let index=0;index<9;index++) key('Up');
      await wait("document.querySelector('.chat-search-result--recent.is-selected .chat-search-result-title')?.textContent === '搜索与历史定位'");
      key('Return'); await wait("!document.querySelector('dialog[open]') && Boolean(document.querySelector('.feed'))");
      key('k', ['meta']); await wait("Boolean(document.querySelector('dialog[open] input'))");
      await win.webContents.insertText("needle"); await wait("Boolean(document.querySelector('.chat-search-result:not(.chat-search-result--recent)'))");
      const searchHeight = await evaluate("document.querySelector('dialog').getBoundingClientRect().height");
      await wait("window.searchFixture.pending");
      assert.ok(await evaluate("Boolean(document.querySelector('.chat-search-result'))"), 'Background indexing keeps results visible');
      assert.equal(await evaluate("document.querySelector('dialog').getBoundingClientRect().height"), searchHeight, 'Index refresh does not resize the dialog');
      assert.equal(await evaluate("[...document.querySelectorAll('dialog p,dialog span')].some(e=>/建立索引|标题与聊天正文/.test(e.textContent))"), false);
      const header = await evaluate("(() => {const input=document.querySelector('dialog input').getBoundingClientRect(); const scope=document.querySelector('dialog select').getBoundingClientRect();return Math.abs(input.y+input.height/2-scope.y-scope.height/2);})()");
      assert.ok(header < 2, 'Search input and project selector share one row');
      await wait("!window.searchFixture.pending");
      const box = await evaluate("(() => { const b=document.querySelector('dialog').getBoundingClientRect(); return {left:b.left,right:b.right,width:innerWidth}; })()");
      assert.ok(box.left >= 0 && box.right <= box.width); layouts.push({ width, dialogFits: true });
      if (width === 1440 && process.env.EIDOS_SEARCH_SCREENSHOT) await writeFile(process.env.EIDOS_SEARCH_SCREENSHOT.replace(/\.png$/, '-global.png'), (await win.webContents.capturePage()).toPNG());
      key("Return"); await wait("!document.querySelector('dialog[open]')");
      await wait("CSS.highlights.get('eidos-chat-find')?.size === 2");
      const bodyBefore = await evaluate("document.querySelector('.feed').getBoundingClientRect().toJSON()");
      key("f", ["meta"]); await wait("Boolean(document.querySelector('.find-bar input'))");
      await checkFindLayout();
      assert.deepEqual(await evaluate("document.querySelector('.feed').getBoundingClientRect().toJSON()"), bodyBefore, 'Chat find does not occupy body layout');
      await win.webContents.insertText("old");
      await wait("Boolean(document.getElementById('item-55555555-5555-4555-8555-555555555555'))");
      await wait("document.querySelector('.find-bar-status')?.textContent === '1/1'");
      await settle();
      assert.equal(await evaluate("CSS.highlights.get('eidos-chat-find')?.size"), 1);
      assert.equal(await evaluate("(() => { const item=document.getElementById('item-55555555-5555-4555-8555-555555555555').getBoundingClientRect();const feed=document.querySelector('.feed').getBoundingClientRect();return item.top>=feed.top && item.bottom<=feed.bottom;})()"), true, 'Old message remains visible after layout settles');
      if (width === 1440 && process.env.EIDOS_SEARCH_SCREENSHOT) await writeFile(process.env.EIDOS_SEARCH_SCREENSHOT.replace(/\.png$/, '-chat.png'), (await win.webContents.capturePage()).toPNG());
      key("Escape"); await wait("!document.querySelector('.find-bar')");
      if (width < 1024) continue; // Existing workspace auto-collapse applies at narrow widths.
      await click('.markdown-file-link'); await wait("Boolean(document.querySelector('.workspace-preview pre'))");
      await click('.workspace-preview pre');
      const fileBefore = await evaluate("document.querySelector('.workspace-preview pre').getBoundingClientRect().toJSON()");
      key("f", ["meta"]);
      await wait("Boolean(document.querySelector('.find-bar--file input'))");
      await checkFindLayout();
      assert.deepEqual(await evaluate("document.querySelector('.workspace-preview pre').getBoundingClientRect().toJSON()"), fileBefore, 'File find does not move source text');
      await win.webContents.insertText("needle"); await wait("CSS.highlights.get('eidos-file-find')?.size === 2");
      key("Return"); assert.equal(await evaluate("CSS.highlights.get('eidos-file-find-active')?.size"), 1);
      await settle();
      if (width === 1440 && process.env.EIDOS_SEARCH_SCREENSHOT) await writeFile(process.env.EIDOS_SEARCH_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
      key("Escape"); await wait("!document.querySelector('.find-bar--file')");
    }
    for (const width of [1440, 320]) {
      win.setContentSize(width, 900);
      for (const surface of ['reference', 'output', 'tool']) {
        await win.loadURL(`${server.resolvedUrls.local[0]}test/search.html?image-close=${surface}`);
        if (surface === 'tool') { await wait("Boolean(document.querySelector('details'))"); await evaluate("document.querySelectorAll('details').forEach(e=>e.open=true)"); }
        const selector = surface === 'reference' ? '.input-reference__btn' : surface === 'output' ? '.artifact-result-card__main' : '.tool-image-thumbnail-btn';
        await wait(`Boolean(document.querySelector(${JSON.stringify(selector)}))`); await click(selector);
        await wait("Boolean(document.querySelector('.artifact-image-preview__close'))"); await settle();
        await checkCloseCenter('.artifact-image-preview__close');
        await click('.artifact-image-preview__close'); await wait("!document.querySelector('.artifact-image-preview')");
      }
    }
    assert.deepEqual(errors, []); assert.equal(await evaluate("Boolean(document.querySelector('.toast--error'))"), false); console.log(JSON.stringify({ layouts, globalSearch: true, oldHistoryAnchor: true, fileFindWorker: true, highlights: true, singleLineOverlay: true, noBodyShift: true, stableIndexRefresh: true, centeredSearchAndImageCloseIcons: true }));
  } catch (error) { console.error(error); if (win) { console.error(await win.webContents.executeJavaScript("({focused:document.activeElement?.outerHTML.slice(0,500), bars:[...document.querySelectorAll('.find-bar')].map(x=>x.outerHTML.slice(0,300)),preview:[...document.querySelectorAll('.workspace-preview')].map(x=>({rect:x.getBoundingClientRect().toJSON(),html:x.outerHTML.slice(0,600)}))})")); await writeFile("/private/tmp/eidos-search-failure.png", (await win.webContents.capturePage()).toPNG()); } code = 1; }
  finally { clearTimeout(deadline); win?.destroy(); await server?.close(); await rm(temporary, { recursive: true, force: true }); app.exit(code); }
});

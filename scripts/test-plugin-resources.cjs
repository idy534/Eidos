const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { mkdir, writeFile, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const temporary = mkdtempSync(path.join(tmpdir(), "eidos-plugin-resources-ui-"));
app.setPath("userData", path.join(temporary, "user-data"));
app.on("window-all-closed", () => {});

app.whenReady().then(async () => {
  let server, win;
  let code = 0;
  const deadline = setTimeout(() => { console.error("Plugin resources UI test timed out"); app.exit(1); }, 45000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } });
    const errors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") errors.push(event.message); });
    const run = value => win.webContents.executeJavaScript(value);
    const wait = condition => run(`new Promise((resolve,reject) => { let count=0; const timer=setInterval(() => { if (${condition}) { clearInterval(timer); resolve(); } else if (++count>100) { clearInterval(timer); reject(new Error('UI condition timed out: ' + ${JSON.stringify(condition)})); } },20); })`);
    const settle = () => run("Promise.all(Array.from(document.querySelectorAll('.modal-backdrop')).flatMap(element => element.getAnimations({subtree:true})).map(animation => animation.finished.catch(() => {})))");
    for (const width of [320, 768, 1024, 1440]) {
      win.setContentSize(width, 900);
      await win.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/test/plugin-resources.html`);
      win.webContents.focus();
      await wait("document.querySelector('[aria-label=\"查看 代码质量检查 的资源\"]')");
      assert.equal(await run("Array.from(document.querySelectorAll('[role=tab]')).find(tab => tab.textContent.startsWith('技能')).textContent"), "技能0");
      assert.equal(await run("Array.from(document.querySelectorAll('[role=tab]')).find(tab => tab.textContent.startsWith('MCP Servers')).textContent"), "MCP Servers0");
      await run("const trigger=document.querySelector('[aria-label=\"查看 代码质量检查 的资源\"]'); trigger.focus(); trigger.click()");
      await wait("document.activeElement?.getAttribute('aria-label')==='关闭插件详情'");
      await settle();
      const layout = await run(`(() => { const dialog=document.querySelector('.plugin-resource-dialog'); const bounds=dialog.getBoundingClientRect(); const body=dialog.querySelector('.plugin-resource-body'); return { left:bounds.left, right:bounds.right, top:bounds.top, bottom:bounds.bottom, width:innerWidth, height:innerHeight, overflow:body.scrollWidth-body.clientWidth, named:!!document.getElementById(dialog.getAttribute('aria-labelledby')) }; })()`);
      assert.ok(layout.left >= -1 && layout.right <= layout.width + 1 && layout.top >= -1 && layout.bottom <= layout.height + 1, JSON.stringify(layout));
      assert.ok(layout.overflow <= 1 && layout.named, JSON.stringify(layout));
      const controls = await run(`Array.from(document.querySelectorAll('.plugin-resource-item button')).map(button => { const rect=button.getBoundingClientRect(); const body=button.closest('.plugin-resource-body').getBoundingClientRect(); return { text:button.textContent, left:rect.left, right:rect.right, width:rect.width, bodyLeft:body.left, bodyRight:body.right }; })`);
      assert.ok(controls.every(button => button.width > 0 && button.left >= button.bodyLeft && button.right <= button.bodyRight), JSON.stringify(controls));
      if (process.env.EIDOS_PLUGIN_SCREENSHOT_DIR) {
        await mkdir(process.env.EIDOS_PLUGIN_SCREENSHOT_DIR, { recursive: true });
        await writeFile(path.join(process.env.EIDOS_PLUGIN_SCREENSHOT_DIR, `plugin-resources-${width}.png`), (await win.webContents.capturePage()).toPNG());
      }
      await run("document.querySelector('.plugin-resource-hook summary').focus(); window.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true}))");
      assert.equal(await run("document.activeElement.getAttribute('aria-label')"), "关闭插件详情");
      await run("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true}))");
      assert.equal(await run("document.activeElement.tagName"), "SUMMARY");
      await run("const skill=document.querySelector('button.plugin-resource-item'); skill.focus(); skill.click()");
      await wait("document.querySelector('.skill-detail-dialog') && document.activeElement?.getAttribute('aria-label')==='关闭技能详情'");
      await run("document.querySelector('.skill-detail-dialog [role=switch]').click()");
      await wait("document.querySelector('.skill-detail-dialog [role=switch]').getAttribute('aria-checked')==='false'");
      await run("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
      await wait("!document.querySelector('.skill-detail-dialog') && document.activeElement?.classList.contains('plugin-resource-item')");
      await run("const review=document.querySelector('.plugin-resource-section-heading button'); review.focus(); review.click()");
      await wait("document.querySelector('[role=alertdialog]')");
      await run("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
      await wait("!document.querySelector('[role=alertdialog]') && document.activeElement?.textContent==='审阅 Hook'");
      await run("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
      await wait("!document.querySelector('.plugin-resource-dialog') && document.activeElement?.classList.contains('plugin-name-button')");
      await wait("Array.from(document.querySelectorAll('button')).some(button => button.textContent==='安装')");
      await run("Array.from(document.querySelectorAll('button')).find(button => button.textContent==='安装').click()");
      await wait("document.querySelector('[aria-label=\"查看 GitHub 的资源\"]')");
      await run("document.querySelector('[aria-label=\"查看 GitHub 的资源\"]').click()");
      await wait("document.querySelector('#mcp-token-github')");
      await settle();
      await run("document.querySelectorAll('.app-toast-close').forEach(button => button.click())");
      await wait("!document.querySelector('.app-toast-close')");
      const remoteLayout = await run(`(() => { const body=document.querySelector('.plugin-resource-body'); const input=document.querySelector('#mcp-token-github'); return { overflow:body.scrollWidth-body.clientWidth, type:input.type, text:body.textContent }; })()`);
      assert.equal(remoteLayout.type, "password");
      assert.ok(remoteLayout.overflow <= 1 && remoteLayout.text.includes("当前版本尚未配置 GitHub 浏览器登录"));
      if (process.env.EIDOS_PLUGIN_SCREENSHOT_DIR) {
        await writeFile(path.join(process.env.EIDOS_PLUGIN_SCREENSHOT_DIR, `github-connect-${width}.png`), (await win.webContents.capturePage()).toPNG());
      }
      await run("document.querySelector('#mcp-token-github').focus()");
      await win.webContents.insertText("fixture-token");
      await run("Array.from(document.querySelectorAll('.mcp-connection-panel button')).find(button => button.textContent==='使用令牌连接').click()");
      await wait("document.querySelector('.mcp-connection-panel').textContent.includes('账号已连接 · octocat')");
      await run("Array.from(document.querySelectorAll('.mcp-connection-panel button')).find(button => button.textContent==='断开账号连接').click()");
      await wait("document.querySelector('#mcp-token-github')?.value===''");
      await run("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
      await wait("!document.querySelector('.plugin-resource-dialog')");
    }
    assert.deepEqual(errors, []);
    console.log("Plugin resources passed 320/768/1024/1440 layout, nested dialogs, keyboard, GitHub install, PAT connection and disconnect checks.");
  } catch (error) { console.error(error); code = 1; }
  finally {
    clearTimeout(deadline);
    win?.destroy();
    await server?.close();
    await rm(temporary, { recursive: true, force: true });
    app.exit(code);
  }
});

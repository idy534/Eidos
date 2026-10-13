const assert = require("node:assert/strict");
const { mkdtempSync, readFileSync } = require("node:fs");
const { rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { app, BrowserWindow } = require("electron");

const root = path.resolve(__dirname, "..");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "eidos-renderer-styles-"));
app.setPath("userData", path.join(temporaryRoot, "user-data"));
app.on("window-all-closed", () => {});

app.whenReady().then(async () => {
  let server;
  let win;
  let exitCode = 0;
  const deadline = setTimeout(() => { console.error("Renderer style test timed out"); app.exit(1); }, 45_000);
  try {
    const { createServer } = await import(pathToFileURL(path.join(root, "node_modules/vite/dist/node/index.js")).href);
    server = await createServer({ configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, open: false } });
    await server.listen();
    win = new BrowserWindow({ show: false, width: 1100, height: 800, webPreferences: { backgroundThrottling: false } });
    const pageErrors = [];
    win.webContents.on("console-message", event => { if (event.level === "error") pageErrors.push(event.message); });
    await win.loadURL(`${server.resolvedUrls.local[0]}test/styles.html`);
    const evaluate = expression => win.webContents.executeJavaScript(expression);
    const settle = () => evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    for (let attempt = 0; attempt < 100 && !await evaluate("Boolean(window.showStyleSample)"); attempt++) {
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(await evaluate("Boolean(window.showStyleSample)"), true, "Fixture must render");
    const show = async sample => { await evaluate(`window.showStyleSample(${JSON.stringify(sample)})`); await settle(); };
    const read = (selector, properties) => evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element) throw new Error('Missing element: ' + ${JSON.stringify(selector)});
      const styles = getComputedStyle(element);
      return Object.fromEntries(${JSON.stringify(properties)}.map(property => [property, styles[property]]));
    })()`);
    const pointer = async (selector, pressed = false) => {
      const point = await evaluate(`(() => {
        const element = document.querySelector(${JSON.stringify(selector)});
        element.scrollIntoView({ block: 'nearest' });
        const rect = element.getBoundingClientRect();
        return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
      })()`);
      win.webContents.sendInputEvent({ type: "mouseMove", ...point });
      if (pressed) win.webContents.sendInputEvent({ type: "mouseDown", button: "left", ...point });
      await settle();
      return point;
    };
    const visual = ["backgroundColor", "color", "borderColor", "boxShadow", "fontWeight"];
    const ordinaryApproval = ".approval-composer:not(.approval-card--danger) .btn-approval-approve";
    const dangerApproval = ".approval-card--danger .btn-approval-approve";
    for (const [ordinary, approvalSelector] of [["#primary", ordinaryApproval], ["#danger", dangerApproval], ["#primary", "#plan-submit"], ["#primary", "#clarification-submit"]]) {
      const initial = await read(ordinary, visual);
      assert.deepEqual(await read(approvalSelector, visual), initial, "Approval must use its public Button variant");
      for (const pressed of [false, true]) {
        const firstPoint = await pointer(ordinary, pressed);
        const first = await read(ordinary, visual);
        if (pressed) win.webContents.sendInputEvent({ type: "mouseUp", button: "left", ...firstPoint });
        const secondPoint = await pointer(approvalSelector, pressed);
        assert.deepEqual(await read(approvalSelector, visual), first, "Approval hover/active must match Button");
        assert.equal(first.color, "rgb(255, 255, 255)", "Action text must remain white");
        assert.notEqual(first.backgroundColor, initial.backgroundColor, "Actions must visibly respond to pointer interaction");
        if (pressed) win.webContents.sendInputEvent({ type: "mouseUp", button: "left", ...secondPoint });
      }
      win.webContents.sendInputEvent({ type: "mouseMove", x: 1, y: 1 });
      await settle();
    }
    const disabled = await read("#disabled", visual);
    await pointer("#disabled", true);
    assert.deepEqual(await read("#disabled", visual), disabled, "Disabled controls must ignore pointer states");
    win.webContents.sendInputEvent({ type: "mouseUp", button: "left", x: 1, y: 1 });
    await pointer("#model-trigger");
    const modelTrigger = await read("#model-trigger", visual);
    await pointer("#mode-trigger");
    assert.deepEqual(await read("#mode-trigger", visual), modelTrigger, "Composer triggers share hover styling");
    await pointer("#icon");
    assert.equal((await read("#icon", ["boxShadow"])).boxShadow, "none", "Icon hover must not inherit the global action shadow");
    win.webContents.focus();
    win.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    win.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" });
    await settle();
    await evaluate("document.querySelector('#model-trigger').focus()");
    assert.equal((await read("#model-trigger", ["outlineWidth", "outlineStyle"])).outlineWidth, "2px", "Keyboard focus remains visible");

    await show("selected");
    const selected = ["session", "git", "project", "tree", "model", "mode", "skill", "handoff", "clarification", "mcp"];
    const selectedBackground = (await read("#selected-session", ["backgroundColor"])).backgroundColor;
    for (const name of selected) {
      const selector = `#selected-${name}`;
      assert.equal((await read(selector, ["backgroundColor"])).backgroundColor, selectedBackground, `${name} shares selected color`);
      await pointer(selector);
      assert.equal((await read(selector, ["backgroundColor"])).backgroundColor, selectedBackground, `${name} remains selected on hover`);
    }
    const menuSurfaces = [];
    await show("menu");
    for (const menu of ["session-menu-popover", "task-context-menu", "dropdown-menu"]) {
      await evaluate(`window.setStyleMenu(${JSON.stringify(menu)})`); await settle();
      menuSurfaces.push(await read(".fixture-menu", ["backgroundColor", "borderColor", "borderRadius", "boxShadow", "backdropFilter"]));
    }
    assert.deepEqual(menuSurfaces[1], menuSurfaces[0], "Context menu shares the menu surface");
    assert.deepEqual(menuSurfaces[2], menuSurfaces[0], "Dropdown shares the menu surface");

    await show("scrollbars");
    const scrollbarCount = await evaluate("document.querySelectorAll('.scrollbar-sample').length");
    for (let index = 0; index < scrollbarCount; index++) {
      const selector = `#scrollbar-${index}`;
      assert.deepEqual(await read(selector, ["scrollbarWidth", "scrollbarColor"]), { scrollbarWidth: "auto", scrollbarColor: "auto" }, "Native scrollbar properties must not bypass the custom skin");
      const parts = await evaluate(`(() => {
        const element = document.querySelector(${JSON.stringify(selector)});
        return ['::-webkit-scrollbar', '::-webkit-scrollbar-track', '::-webkit-scrollbar-track-piece', '::-webkit-scrollbar-corner', '::-webkit-scrollbar-thumb'].map(part => {
          const styles = getComputedStyle(element, part);
          return { part, width: styles.width, height: styles.height, background: styles.backgroundColor, borders: ['Top', 'Right', 'Bottom', 'Left'].map(side => styles['border' + side + 'Width']), shadow: styles.boxShadow };
        });
      })()`);
      assert.equal(parts[0].width, "6px", "Vertical scrollbars retain their shared thin size");
      assert.equal(parts[0].height, "6px", "Horizontal scrollbars retain their shared thin size");
      for (const part of parts) {
        assert.deepEqual(part.borders, ["0px", "0px", "0px", "0px"], `${part.part} has no border`);
        assert.equal(part.shadow, "none", `${part.part} has no frame shadow`);
        if (!part.part.endsWith("-thumb")) assert.equal(part.background, "rgba(0, 0, 0, 0)", `${part.part} has no background`);
      }
      const bounds = await evaluate(`(() => { const rect = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) }; })()`);
      const image = await win.webContents.capturePage(bounds);
      const { width, height } = image.getSize(1);
      const pixels = image.toBitmap({ scaleFactor: 1 });
      const pixel = (x, y) => Array.from(pixels.subarray((y * width + x) * 4, (y * width + x) * 4 + 4));
      const background = pixel(width - 12, height - 12);
      assert.deepEqual(pixel(width - 2, height - 12), background, "Painted vertical track is transparent");
      assert.deepEqual(pixel(width - 12, height - 2), background, "Painted horizontal track is transparent");
      assert.deepEqual(pixel(width - 2, height - 2), background, "Painted scrollbar corner is transparent");
    }
    const thumb = await evaluate("getComputedStyle(document.querySelector('#scrollbar-0'), '::-webkit-scrollbar-thumb').backgroundColor");
    assert.equal(thumb, "rgba(106, 111, 103, 0.28)", "Scrollbar thumb retains a valid visible color");
    const hoverThumb = await evaluate(`(() => {
      const probe = document.createElement('div'); probe.style.backgroundColor = 'var(--scrollbar-thumb-hover)'; document.body.append(probe);
      const color = getComputedStyle(probe).backgroundColor; probe.remove(); return color;
    })()`);
    assert.equal(hoverThumb, "rgba(106, 111, 103, 0.45)", "Scrollbar hover color must remain valid");

    const snapshots = [];
    const contentTargets = [
      [".eidos-chat-prose p", ["fontFamily", "fontSize", "lineHeight", "marginTop", "marginBottom", "color"]],
      [".eidos-chat-prose h1", ["fontSize", "lineHeight", "fontWeight", "marginTop", "marginBottom"]],
      [".code-block-card", ["maxHeight", "backgroundColor", "borderRadius"]],
      [".code-block-content pre", ["fontFamily", "fontSize", "lineHeight", "whiteSpace", "padding"]],
      [".markdown-body:not(.eidos-chat-prose) p", ["fontFamily", "fontSize", "lineHeight", "marginBottom"]],
      [".workspace-text-preview pre", ["fontFamily", "fontSize", "lineHeight", "padding"]],
      [".mermaid-svg-wrapper foreignObject p", ["fontSize", "lineHeight", "margin", "padding"]],
    ];
    const layoutTargets = [
      [".workspace-main-column", ["width", "gridColumnStart"]],
      [".session-header", ["height", "padding", "webkitAppRegion"]],
      [".feed", ["width", "padding", "overflowAnchor", "scrollbarGutter"]],
      [".feed-item--assistant", ["width", "paddingLeft"]],
      [".composer", ["width", "margin", "borderRadius"]],
      [".composer textarea", ["height", "minHeight", "maxHeight", "fieldSizing", "fontSize", "lineHeight"]],
    ];
    const baselineFile = process.env.EIDOS_STYLE_BASELINE_CSS;
    const compareBaseline = async (targets, current) => {
      if (!baselineFile) return;
      const css = readFileSync(baselineFile, "utf8");
      await evaluate(`(() => {
        window.disabledStyleSheets = Array.from(document.styleSheets)
          .filter(sheet => sheet.ownerNode?.id !== 'fixture-layout')
          .map(sheet => ({ sheet, disabled: sheet.disabled }));
        window.disabledStyleSheets.forEach(({ sheet }) => { sheet.disabled = true; });
        const style = document.createElement('style'); style.id = 'baseline-css'; style.textContent = ${JSON.stringify(css)}; document.head.append(style);
      })()`);
      await settle();
      const previous = await Promise.all(targets.map(([selector, properties]) => read(selector, properties)));
      await evaluate("document.querySelector('#baseline-css').remove(); window.disabledStyleSheets.forEach(({ sheet, disabled }) => { sheet.disabled = disabled; })");
      await settle();
      assert.deepEqual(current, previous, "Content typography and layout must match the previous production bundle");
    };
    for (const width of [1100, 420]) {
      win.setContentSize(width, 800); await settle();
      await show("content");
      for (let attempt = 0; attempt < 100 && !await evaluate("Boolean(document.querySelector('.shiki-code-body'))"); attempt++) await new Promise(resolve => setTimeout(resolve, 50));
      const content = await Promise.all(contentTargets.map(([selector, properties]) => read(selector, properties)));
      assert.equal(content[0].fontSize, "14px"); assert.equal(content[0].lineHeight, "23px");
      assert.equal(content[3].fontSize, "12.5px"); assert.equal(content[3].whiteSpace, "pre");
      await compareBaseline(contentTargets, content);
      await show("layout");
      for (const collapsed of [false, true]) {
        await evaluate(`window.setStyleSidebarCollapsed(${collapsed})`); await settle();
        for (const dock of [false, true]) {
          await evaluate(`window.setStyleDock(${dock})`); await settle();
          const layout = await Promise.all(layoutTargets.map(([selector, properties]) => read(selector, properties)));
          assert.equal(layout[2].scrollbarGutter, "stable");
          assert.equal(layout[5].minHeight, "56px"); assert.equal(layout[5].maxHeight, "168px");
          assert.ok(parseFloat(layout[5].height) >= 56 && parseFloat(layout[5].height) <= 168, "Content sizing stays within its bounds");
          assert.equal(await evaluate("window.scrollY"), 0);
          await compareBaseline(layoutTargets, layout);
          snapshots.push({ width, collapsed, dock, contentFont: content[0].fontSize, textareaHeight: layout[5].height });
        }
      }
    }
    assert.deepEqual(pageErrors, [], "Fixture must not report renderer errors");
    console.log(JSON.stringify({ status: "passed", controlStates: true, selectedStates: selected.length, menuSurfaces: menuSurfaces.length, scrollbars: scrollbarCount, baselineCompared: Boolean(baselineFile), layouts: snapshots }));
  } catch (error) {
    exitCode = 1; console.error(error);
  } finally {
    clearTimeout(deadline);
    if (win && !win.isDestroyed()) win.destroy();
    if (server) await server.close();
    await rm(temporaryRoot, { recursive: true, force: true });
    app.exit(exitCode);
  }
});

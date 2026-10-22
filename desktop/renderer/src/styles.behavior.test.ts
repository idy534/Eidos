import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { readRendererStyleModules, readRendererStyles } from "../test/read-styles.js";

const styles = readRendererStyles();
const dockStyles = readFileSync(
  path.resolve(process.cwd(), "desktop/renderer/src/components/WorkspaceDock.css"),
  "utf8",
);

describe("renderer style ownership", () => {
  it("loads every local module once through the ordered CSS entry", () => {
    const modules = readRendererStyleModules();
    const rendererRoot = path.resolve(process.cwd(), "desktop/renderer/src");
    const localFiles = readdirSync(rendererRoot, { recursive: true })
      .filter((file) => typeof file === "string" && file.endsWith(".css") && file !== "styles.css")
      .map((file) => path.join(rendererRoot, file as string));
    expect(new Set(modules.map(({ file }) => file)).size).toBe(modules.length);
    expect(modules.map(({ file }) => file).sort()).toEqual(localFiles.sort());
    expect(modules[0]?.file).toBe(path.join(rendererRoot, "styles/tokens.css"));
    expect(modules[1]?.file).toBe(path.join(rendererRoot, "styles/base.css"));
    for (const { styles: moduleStyles } of modules) expect(moduleStyles).not.toMatch(/^@import/m);
  });
});

describe("focus ring", () => {
  it("paints no ring for keyboard or pointer focus", () => {
    expect(styles).toMatch(/--focus-ring:\s*none;/);
    expect(styles).not.toMatch(/--focus-ring:\s*[^;]*\d+px\s+solid/);
  });
});

describe("interactive color tokens", () => {
  it("does not use the dark green hover token", () => {
    expect(styles).not.toContain("--accent-hover");
    expect(styles).not.toContain("#244b39");
    expect(styles).toMatch(/button:hover:not\(:disabled\)\s*\{[^}]*background: var\(--surface-hover\);/);
  });

  it("maintains high contrast and dark hover background on unified primary and danger buttons", () => {
    // Action variants retain their contrast throughout pointer interaction.
    expect(styles).not.toMatch(/\.btn--primary:hover[^{]*\{[^}]*background:\s*var\(--primary-action-bg-hover\);/s);
    expect(styles).toMatch(/--action-primary-hover:\s*#274e3c;/);
    expect(styles).toMatch(/--action-primary-active:\s*#1e3d2f;/);
    expect(styles).toMatch(/\.btn--primary:hover:not\(:disabled\)\s*\{[^}]*color:\s*var\(--text-on-accent\);[^}]*background:\s*var\(--action-primary-hover\);/s);
    expect(styles).toMatch(/\.btn--primary:active:not\(:disabled\)\s*\{[^}]*color:\s*var\(--text-on-accent\);[^}]*background:\s*var\(--action-primary-active\);/s);

    // Danger button must keep pure white text on hover and active so labels like "遗忘" remain crisp and visible
    expect(styles).toMatch(/--action-danger-hover:\s*#7a3028;/);
    expect(styles).toMatch(/--action-danger-active:\s*#65251e;/);
    expect(styles).toMatch(/\.btn--danger:hover:not\(:disabled\)\s*\{[^}]*color:\s*var\(--text-on-accent\);[^}]*background:\s*var\(--action-danger-hover\);/s);
    expect(styles).toMatch(/\.btn--danger:active:not\(:disabled\)\s*\{[^}]*color:\s*var\(--text-on-accent\);[^}]*background:\s*var\(--action-danger-active\);/s);
  });

  it("does not wash out the selected session item on hover", () => {
    expect(styles).toMatch(
      /\.session-list\s*>\s*\.session-item\s*>\s*button:not\(\.selected\):hover:not\(:disabled\)\s*\{[^}]*background:\s*rgba\(255,\s*255,\s*255,\s*0\.65\);/s,
    );
    expect(styles).toMatch(
      /\.session-list\s*>\s*\.session-item\s*>\s*button\.selected:hover[^{]*\{[^}]*background:\s*var\(--surface-selected\);/s,
    );
  });

  it("unifies project row hover background and avoids dual hover backgrounds on toggle and add button", () => {
    expect(styles).toMatch(
      /\.workspace-title-row:not\(\.workspace-title-row--recent\):hover,\s*\.workspace-title-row:not\(\.workspace-title-row--recent\):focus-within\s*\{[^}]*background:\s*rgba\(255,\s*255,\s*255,\s*0\.65\);/s,
    );
    expect(styles).toMatch(
      /\.workspace-toggle:not\(\.workspace-toggle--recent\):hover:not\(:disabled\),\s*\.workspace-toggle:not\(\.workspace-toggle--recent\):active:not\(:disabled\)\s*\{[^}]*background:\s*transparent;/s,
    );
    expect(styles).toMatch(
      /\.workspace-add:hover:not\(:disabled\),\s*\.workspace-add:focus-visible\s*\{[^}]*background:\s*rgba\(0,\s*0,\s*0,\s*0\.08\);/s,
    );
  });

  it("keeps session content centered and reserves one shared action rail", () => {
    expect(dockStyles).toMatch(/\.workspace-body--session-centered\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\);/s);
    expect(dockStyles).toMatch(/\.workspace-body--session-centered \.workspace-main-column\s*\{[^}]*grid-column: 1;[^}]*width: 100%;[^}]*margin: 0;/s);
    expect(dockStyles).toMatch(/\.workspace-main\s*\{[^}]*flex: 1 1 auto;/s);
    expect(dockStyles).toMatch(/\.workspace-body--session-centered \.workspace-main-column > \.session-header\s*\{[^}]*padding-right: 1\.5rem;/s);
  });

  it("keeps the feed and composer on one fixed centered content frame", () => {
      expect(dockStyles).toMatch(/\.workspace-main\s*\{[^}]*--session-content-width: 49rem;/s);
    expect(styles).toMatch(/\.feed\s*\{[^}]*padding: 1\.25rem max\(1\.75rem, calc\(\(100% - var\(--session-content-width\)\) \/ 2\)\);/s);
    expect(styles).toMatch(/\.feed-item--assistant\s*\{[^}]*width: min\(100%, var\(--session-content-width\)\);[^}]*max-width: var\(--session-content-width\);[^}]*margin-inline: auto;[^}]*padding-left: 0\.75rem;/s);
    expect(styles).toMatch(/\.composer\s*\{[^}]*width: min\(calc\(100% - 3\.5rem\), var\(--session-content-width\)\);/s);
    expect(styles).toMatch(/\.composer\s*\{[^}]*border:\s*1px solid rgba\(0,\s*0,\s*0,\s*0\.09\);/s);
    expect(styles).not.toMatch(/\.composer:focus-within/);
  });

  it("keeps a narrow dock beside the session and aligns its header controls", () => {
    expect(dockStyles).toMatch(/\.workspace-body--with-dock\s*\{[^}]*--workspace-dock-width: 20rem;/s);
    expect(dockStyles).toMatch(/\.workspace-body--with-dock\s*\{[^}]*grid-template-columns: minmax\(16rem, 1fr\) 0\.5rem var\(--workspace-dock-width\);/s);
    expect(dockStyles).not.toMatch(/\.workspace-body--with-dock:not\(\.workspace-body--expanded\)\s*\.workspace-dock\s*\{[^}]*position: absolute;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__add \.dropdown-trigger,\s*\.workspace-dock__actions \.icon-button\s*\{[^}]*width: 2\.25rem;[^}]*height: 2\.25rem;[^}]*min-height: 2\.25rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__add \.dropdown-trigger > span\[aria-hidden\]\s*\{[^}]*font-size: 1\.25rem;[^}]*line-height: 1;/s);
  });

  it("flows open dock controls in one fixed-size header group", () => {
    expect(dockStyles).toMatch(/\.workspace-dock__header\s*\{[^}]*padding: 0\.25rem 0\.65rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__actions\s*\{[^}]*flex: none;[^}]*gap: 0\.35rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock-toggle svg,\s*\.workspace-header-tools svg,\s*\.workspace-dock__actions svg,\s*\.workspace-dock__add svg\s*\{[^}]*width: 1\.25rem;[^}]*height: 1\.25rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__add \.dropdown-trigger:hover,[^}]*\.workspace-dock__actions \.icon-button:hover\s*\{[^}]*background: var\(--surface-selected\);/s);
    expect(dockStyles).toMatch(/\.workspace-body \.session-header,\s*\.workspace-dock__header\s*\{[^}]*height:\s*2\.75rem;[^}]*min-height:\s*2\.75rem;/s);
  });

  it("styles workspace dock empty state as elevated two-line action cards with expanded grid layout", () => {
    expect(dockStyles).not.toContain(".workspace-dock__empty-hero");
    expect(dockStyles).toMatch(/\.workspace-dock__empty-option\s*\{[^}]*border:\s*1px solid var\(--line\);[^}]*background:\s*var\(--surface-raised\);/s);
    expect(dockStyles).toMatch(/\.workspace-dock__empty-option-label\s*\{[^}]*font-size:\s*var\(--font-size-sidebar\);/s);
    expect(dockStyles).toMatch(/\.workspace-dock--expanded\s+\.workspace-dock__empty-list\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s);
    expect(dockStyles).toMatch(/\.workspace-dock--expanded\s+\.workspace-dock__empty-list--single[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\);/);
  });

  it("renders compact single-column diff colors", () => {
    expect(styles).toMatch(/\.git-diff-unified col\.diff-gutter-col:nth-of-type\(2\)/);
    expect(styles).toMatch(/\.git-diff-unified \.diff-code-insert\s*\{[^}]*color: var\(--status-success\);/s);
    expect(styles).toMatch(/\.git-diff-unified \.diff-code-delete\s*\{[^}]*color: var\(--status-danger\);/s);
  });

  it("adapts Git review controls to the dock width instead of the window width", () => {
    expect(styles).toMatch(/\.git-changes-panel\s*\{[^}]*container-type: inline-size;[^}]*container-name: git-review;/s);
    expect(styles).toMatch(/@container git-review \(max-width: 30rem\)\s*\{[\s\S]*\.git-changes-toolbar\s*\{[^}]*display: flex;/s);
    expect(styles).toMatch(/@container git-review \(max-width: 30rem\)\s*\{[\s\S]*\.git-scope-tabs\s*\{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/s);
    expect(styles).toMatch(/\.git-scope-tab\s*\{[^}]*white-space: nowrap;/s);
    expect(styles).toMatch(/\.git-scope-dropdown-label\s*\{[^}]*text-overflow:\s*ellipsis;/s);
    expect(styles).toMatch(/\.git-scope-dropdown-label\s*\{[^}]*max-width:\s*clamp\(/s);
  });

  it("supports collapsible sidebar and top-level navigation controls", () => {
    expect(styles).toMatch(/\.workbench--sidebar-collapsed\s*\{[^}]*grid-template-columns:\s*0rem\s*minmax\(0,\s*1fr\);/s);
    expect(styles).toMatch(/\.workbench--sidebar-collapsed\s*\.sidebar\s*\{[^}]*transform:\s*translateX\(-100%\);[^}]*visibility:\s*hidden;/s);
    expect(styles).toMatch(/\.sidebar-top-bar\s*\{[^}]*height:\s*2\.75rem;/s);
    expect(styles).toMatch(/\.collapsed-navigation-bar\s*\{[^}]*position:\s*absolute;/s);
    expect(styles).toMatch(/\.window-traffic-spacer\s*\{[^}]*width:\s*86px;/s);
    expect(styles).toMatch(/\.nav-icon-btn\s*\{[^}]*-webkit-app-region:\s*no-drag\s*!important;/s);
    expect(styles).toMatch(/\.workbench--sidebar-collapsed\s*\.workspace-header::before\s*\{[^}]*-webkit-app-region:\s*no-drag\s*!important;/s);
    expect(dockStyles).toMatch(/\.workspace-body--session-centered\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/s);
    expect(styles).toMatch(/\.workspace-header--empty\s*\{[^}]*position:\s*absolute;[^}]*-webkit-app-region:\s*drag;/s);
    expect(styles).toMatch(/\.settings-page-header\s*\{[^}]*-webkit-app-region:\s*drag;/s);
    expect(dockStyles).toMatch(/\.workbench--sidebar-collapsed\s+\.workspace-dock--expanded\s+\.workspace-dock__header,\s*\.workbench--sidebar-collapsed\s+\.workspace-body--expanded\s+\.workspace-dock__header\s*\{[^}]*padding-left:\s*calc\(86px\s*\+\s*8rem\);/s);
  });

  it("keeps the workspace dock header draggable while keeping tabs clickable", () => {
    expect(dockStyles).toMatch(/\.workspace-dock__header\s*\{[^}]*-webkit-app-region:\s*drag;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__header button,/s);
    expect(dockStyles).toMatch(/\.workspace-dock__header \[role="tab"\]/s);
  });

  it("keeps the startup gate draggable while keeping its button clickable", () => {
    expect(styles).toMatch(/\.runtime-gate\s*\{[^}]*-webkit-app-region:\s*drag;/s);
    expect(styles).toMatch(/\.runtime-gate button,/s);
  });
  it("places the sidebar scrollbar against the divider", () => {
    expect(styles).toMatch(/\.sidebar nav\s*\{[^}]*margin-right:\s*-0\.875rem;[^}]*padding-right:\s*0\.875rem;/s);
  });

  it("maintains typography consistency between session and workspace", () => {
    expect(styles).toMatch(/--font-code:[^;]*"PingFang SC"[^;]*monospace;/);
    expect(styles).toMatch(/--diff-font-family:\s*var\(--font-code\);/);
    expect(styles).toMatch(/code,\s*kbd,\s*samp,\s*pre\s*\{[^}]*font-family:\s*var\(--font-code\);/s);
    expect(styles).toMatch(/\.workspace-text-preview,\s*\.workspace-code-preview pre\s*\{[^}]*font-family:\s*var\(--font-code\);/s);
    expect(dockStyles).toMatch(/\.workspace-dock\s*\{[^}]*font-family:\s*var\(--font-ui\);/s);
    expect(styles).toMatch(/\.git-file-button code\s*\{[^}]*font-family:\s*var\(--font-ui\);/s);
    expect(styles).toMatch(/\.git-file-diff-scroll \.diff-gutter\s*\{[^}]*font-variant-numeric:\s*tabular-nums;/s);
    expect(styles).toMatch(/\.git-file-diff-scroll \.diff-code\s*\{[^}]*tab-size:\s*2;/s);
    expect(styles).toMatch(/\.git-review-stat\s*\{[^}]*font-size:\s*var\(--font-size-sidebar\);[^}]*font-variant-numeric:\s*tabular-nums;/s);
  });

  it("aligns sidebar project and session name typography with prose font while preserving sizes and weights", () => {
    expect(styles).toMatch(/--font-prose:\s*-apple-system,\s*BlinkMacSystemFont,\s*"Segoe UI",\s*"PingFang SC",\s*"Microsoft YaHei"/);
    expect(styles).toMatch(/\.workspace-name\s*\{[^}]*font-family:\s*var\(--font-prose\);[^}]*font-size:\s*var\(--font-size-body\);[^}]*font-weight:\s*500;/s);
    expect(styles).toMatch(/\.session-title\s*\{[^}]*font-family:\s*var\(--font-prose\);[^}]*font-size:\s*var\(--font-size-body\);[^}]*font-weight:\s*400;/s);
    expect(styles).toMatch(/\.sidebar-hover-card__title\s*\{[^}]*font-family:\s*var\(--font-prose\);/s);
  });

  it("anchors streaming scroll to avoid viewport jumps", () => {
    expect(styles).toMatch(/\.feed\s*\{[^}]*overflow-anchor:\s*auto;[^}]*scrollbar-gutter:\s*stable;/s);
    expect(styles).toMatch(/\.feed-item--assistant\s*\{[^}]*overflow-anchor:\s*none;/s);
  });

  it("aligns run_shell background with the markdown code block surface", () => {
    expect(styles).toMatch(/\.markdown-body pre\s*\{[^}]*background:\s*var\(--surface-muted\);/s);
    expect(styles).toMatch(/\.shell-result\s*\{[^}]*background:\s*var\(--surface-muted\);/s);
    expect(styles).toMatch(/\.shell-result\s*\{[^}]*border:\s*1px solid var\(--line\);/s);
  });

  it("centers git review toolbar icon buttons and avoids dual hover backgrounds", () => {
    expect(styles).toMatch(/\.git-more-dropdown \.dropdown-trigger\s*\{[^}]*width:\s*1\.85rem;[^}]*height:\s*1\.85rem;[^}]*display:\s*inline-flex;[^}]*align-items:\s*center;[^}]*justify-content:\s*center;/s);
    expect(styles).toMatch(/\.git-more-dropdown \.dropdown-trigger:hover:not\(:disabled\)[^}]*background:\s*var\(--surface-hover\);/s);
    expect(styles).toMatch(/\.git-changes-toolbar-right \.git-icon-button\s*\{[^}]*width:\s*1\.85rem;[^}]*height:\s*1\.85rem;[^}]*gap:\s*0;[^}]*align-items:\s*center;[^}]*justify-content:\s*center;/s);
    expect(styles).toMatch(/\.git-changes-toolbar-right \.git-icon-button \.btn-label\s*\{[^}]*display:\s*contents;/s);
    expect(styles).toMatch(/\.btn-label:has\(>\s*\.sr-only:only-child\)\s*\{[^}]*display:\s*contents;/s);
  });

  it("isolates single diff scrolling and provides group scrolling for multi-file patches", () => {
    expect(styles).toMatch(/\.diff-viewer-group\s*\{[^}]*--diff-single-max-height:\s*16rem;[^}]*max-height:\s*calc\(var\(--diff-single-max-height\)\s*\*\s*1\.5\);[^}]*overflow-y:\s*auto;/s);
    expect(styles).toMatch(/\.diff-file-card\s*\{[^}]*overflow:\s*hidden;[^}]*flex-shrink:\s*0;[^}]*max-height:\s*var\(--diff-single-max-height,\s*16rem\);/s);
    expect(styles).toMatch(/\.diff-file-body\s*\{[^}]*max-height:\s*calc\(var\(--diff-single-max-height,\s*16rem\)\s*-\s*2\.1rem\);[^}]*overflow:\s*auto;/s);
    expect(styles).toMatch(/\.diff-file-card\.diff-view\s*\{[^}]*max-height:\s*var\(--diff-single-max-height,\s*16rem\);[^}]*overflow:\s*hidden;/s);
  });

  it("applies low-saturation links and distinct inline code pill background in markdown", () => {
    expect(styles).toMatch(/--link-color:\s*#2b5da7;/);
    expect(styles).toMatch(/--code-inline-bg:\s*#eef0f2;/);
    expect(styles).toMatch(/\.markdown-body code\s*\{[^}]*background:\s*var\(--code-inline-bg/s);
    expect(styles).toMatch(/\.markdown-body code\s*\{[^}]*border:\s*1px solid var\(--code-inline-border/s);
    expect(styles).toMatch(/\.markdown-file-link\s*\{[^}]*color:\s*var\(--link-color/s);
    expect(styles).not.toMatch(/\.markdown-file-link\s*\{[^}]*color:\s*#1d4ed8;/s);
    expect(styles).toMatch(/\.markdown-file-link\s*\{[^}]*vertical-align:\s*-0\.15em;/s);
    expect(styles).toMatch(/\.markdown-body a\s*\{[^}]*text-decoration:\s*none;/s);
    expect(styles).toMatch(/\.markdown-body a:hover[^{]*\{[^}]*text-decoration:\s*underline;/s);
    expect(styles).toMatch(/\.markdown-body a\.markdown-file-link[^{]*\{[^}]*text-decoration:\s*none !important;/s);
  });

  it("scales code block fonts appropriately to match body text proportions", () => {
    expect(styles).toMatch(/--font-size-code:\s*12\.5px;/);
    expect(styles).toMatch(/\.code-block-card pre\s*\{[^}]*font:\s*var\(--font-size-code/s);
    expect(styles).toMatch(/\.shiki-code-body\s*\{[^}]*font:\s*var\(--font-size-code/s);
    expect(styles).toMatch(/\.code-block-card pre \.line\s*\{[^}]*display:\s*inline;/s);
    expect(styles).toMatch(/\.shiki-code-body pre \.line\s*\{[^}]*display:\s*inline;/s);
  });

  it("locks root viewport to prevent unexpected window scroll", () => {
    expect(styles).toMatch(/html,\s*body,\s*#root\s*\{[^}]*overflow:\s*hidden;[^}]*overscroll-behavior:\s*none;/s);
    expect(styles).toMatch(/body\s*\{[^}]*overflow:\s*hidden;[^}]*overscroll-behavior:\s*none;/s);
    expect(styles).toMatch(/\.workbench\s*\{[^}]*overflow:\s*hidden;[^}]*overscroll-behavior:\s*none;/s);
    expect(styles).toMatch(/\.workspace\s*\{[^}]*overflow:\s*hidden;[^}]*overscroll-behavior:\s*none;/s);
    expect(dockStyles).toMatch(/\.workspace-dock\s*\{[^}]*overflow:\s*hidden;[^}]*overflow:\s*clip;/s);
    expect(styles).toMatch(/\.workspace-explorer\s*\{[^}]*overflow:\s*hidden;[^}]*overflow:\s*clip;/s);
  });

  it("keeps toast notifications interactive and non-draggable over window header drag regions", () => {
    expect(styles).toMatch(/\.app-toast\s*\{[^}]*-webkit-app-region:\s*no-drag\s*!important;/s);
    expect(styles).toMatch(/\.app-toast-close\s*\{[^}]*-webkit-app-region:\s*no-drag\s*!important;/s);
  });

  it("isolates Mermaid nodes from markdown prose styles and preserves natural fixed dimensions", () => {
    expect(styles).toMatch(/\.mermaid-svg-wrapper foreignObject p,\s*\.mermaid-modal-svg-wrapper foreignObject p\s*\{[^}]*margin:\s*0\s*!important;[^}]*line-height:\s*1\.5\s*!important;/s);
    expect(styles).toMatch(/\.mermaid-svg-wrapper svg\s*\{[^}]*max-width:\s*none;/s);
    expect(styles).not.toMatch(/\.mermaid-svg-wrapper svg\s*\{[^}]*max-width:\s*100%\s*!important;/s);
  });

  it("hides code block and diff viewer scrollbar thumbs at rest and smoothly transitions them on hover", () => {
    expect(styles).toMatch(
      /@property\s+--scrollbar-thumb\s*\{[^}]*syntax:\s*"<color>";[^}]*inherits:\s*true;/s,
    );
    expect(styles).toMatch(
      /\.code-block-content,\s*\.diff-viewer-group,\s*\.diff-file-body\s*\{[^}]*--scrollbar-thumb:\s*transparent;[^}]*transition:\s*--scrollbar-thumb 180ms ease;/s,
    );
    expect(styles).toMatch(
      /\.code-block-card:hover \.code-block-content,\s*\.code-block-content:hover,\s*\.diff-viewer-group:hover,\s*\.diff-file-card:hover \.diff-file-body,\s*\.diff-file-body:hover\s*\{[^}]*--scrollbar-thumb:\s*rgba\(106,\s*111,\s*103,\s*0\.38\);/s,
    );
    expect(styles).toMatch(
      /\.code-block-content::-webkit-scrollbar-thumb,\s*\.diff-viewer-group::-webkit-scrollbar-thumb,\s*\.diff-file-body::-webkit-scrollbar-thumb\s*\{[^}]*background:\s*var\(--scrollbar-thumb\);/s,
    );
    expect(styles).toMatch(
      /\.code-block-card:hover \.code-block-content::-webkit-scrollbar-thumb:hover,\s*\.code-block-content::-webkit-scrollbar-thumb:hover\s*\{[^}]*background:\s*rgba\(106,\s*111,\s*103,\s*0\.65\);/s,
    );
    expect(styles).toMatch(
      /\.code-block-card:hover \.code-block-content::-webkit-scrollbar-thumb:active,\s*\.code-block-content::-webkit-scrollbar-thumb:active\s*\{[^}]*background:\s*rgba\(106,\s*111,\s*103,\s*0\.85\);/s,
    );
    expect(styles).toMatch(
      /\.approval-command::-webkit-scrollbar-thumb:hover\s*\{[^}]*background:\s*var\(--scrollbar-thumb-hover\);/s,
    );
    expect(styles).toMatch(
      /\.environment-popover__body::-webkit-scrollbar-thumb:hover\s*\{[^}]*background:\s*var\(--scrollbar-thumb-hover\);/s,
    );
  });
});

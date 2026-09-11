import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const styles = readFileSync(path.resolve(process.cwd(), "desktop/renderer/src/styles.css"), "utf8");
const dockStyles = readFileSync(
  path.resolve(process.cwd(), "desktop/renderer/src/components/WorkspaceDock.css"),
  "utf8",
);

describe("interactive color tokens", () => {
  it("does not use the dark green hover token", () => {
    expect(styles).not.toContain("--accent-hover");
    expect(styles).not.toContain("#244b39");
    expect(styles).toMatch(/button:hover:not\(:disabled\)\s*\{[^}]*background: var\(--surface-selected\);/);
  });

  it("keeps session content centered and reserves one shared action rail", () => {
    expect(dockStyles).toMatch(/\.workspace-body--session-centered\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\) min\(72rem, 100%\) minmax\(0, 1fr\);/s);
    expect(dockStyles).toMatch(/\.workspace-body--session-centered \.workspace-main-column\s*\{[^}]*grid-column: 2;[^}]*width: 100%;[^}]*margin: 0;/s);
    expect(dockStyles).toMatch(/\.workspace-main\s*\{[^}]*flex: 1 1 auto;/s);
    expect(dockStyles).toMatch(/\.workspace-body__actions\s*\{[^}]*display: flex;[^}]*align-items: center;/s);
    expect(dockStyles).toMatch(/\.workspace-body--session-centered \.workspace-main-column > \.session-header\s*\{[^}]*padding-right: 1\.5rem;/s);
  });

  it("keeps the feed and composer on one fixed centered content frame", () => {
      expect(dockStyles).toMatch(/\.workspace-main\s*\{[^}]*--session-content-width: 49rem;/s);
    expect(styles).toMatch(/\.feed\s*\{[^}]*padding: 1\.25rem max\(1\.75rem, calc\(\(100% - var\(--session-content-width\)\) \/ 2\)\);/s);
    expect(styles).toMatch(/\.feed-item--assistant\s*\{[^}]*width: min\(100%, var\(--session-content-width\)\);[^}]*max-width: var\(--session-content-width\);[^}]*margin-inline: auto;[^}]*padding-left: 0\.75rem;/s);
    expect(styles).toMatch(/\.composer\s*\{[^}]*width: min\(calc\(100% - 3\.5rem\), var\(--session-content-width\)\);/s);
  });

  it("keeps a narrow dock beside the session and aligns its header controls", () => {
    expect(dockStyles).toMatch(/\.workspace-body--with-dock\s*\{[^}]*--workspace-dock-width: 20rem;/s);
    expect(dockStyles).toMatch(/\.workspace-body--with-dock\s*\{[^}]*grid-template-columns: minmax\(16rem, 1fr\) 0\.5rem var\(--workspace-dock-width\);/s);
    expect(dockStyles).not.toMatch(/\.workspace-body--with-dock:not\(\.workspace-body--expanded\)\s*\.workspace-dock\s*\{[^}]*position: absolute;/s);
    expect(dockStyles).toMatch(/\.workspace-body__actions\s*\{[^}]*top: 0\.25rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__add \.dropdown-trigger,\s*\.workspace-dock__actions \.icon-button\s*\{[^}]*width: 2\.25rem;[^}]*height: 2\.25rem;[^}]*min-height: 2\.25rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__add \.dropdown-trigger > span\[aria-hidden\]\s*\{[^}]*font-size: 1\.25rem;[^}]*line-height: 1;/s);
  });

  it("flows open dock controls in one fixed-size header group", () => {
    expect(dockStyles).toMatch(/\.workspace-body\s*\{[^}]*--workspace-action-rail-width: 5rem;/s);
    expect(dockStyles).toMatch(/\.workspace-body__actions\s*\{[^}]*width: var\(--workspace-action-rail-width\);/s);
    expect(dockStyles).toMatch(/\.workspace-dock__header\s*\{[^}]*padding: 0\.25rem 0\.65rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__actions\s*\{[^}]*flex: none;[^}]*gap: 0\.35rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock-toggle svg,\s*\.workspace-header-tools svg,\s*\.workspace-dock__actions svg,\s*\.workspace-dock__add svg\s*\{[^}]*width: 1\.25rem;[^}]*height: 1\.25rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__add \.dropdown-trigger:hover,[^}]*\.workspace-dock__actions \.icon-button:hover\s*\{[^}]*background: var\(--surface-selected\);/s);
  });

  it("renders compact single-column diff colors", () => {
    expect(styles).toMatch(/\.git-diff-unified col\.diff-gutter-col:nth-of-type\(2\)/);
    expect(styles).toMatch(/\.git-diff-unified \.diff-code-insert\s*\{[^}]*color: var\(--status-success\);/s);
    expect(styles).toMatch(/\.git-diff-unified \.diff-code-delete\s*\{[^}]*color: var\(--status-danger\);/s);
  });

  it("adapts Git review controls to the dock width instead of the window width", () => {
    expect(styles).toMatch(/\.git-changes-panel\s*\{[^}]*container-type: inline-size;[^}]*container-name: git-review;/s);
    expect(styles).toMatch(/@container git-review \(max-width: 30rem\)\s*\{[\s\S]*\.git-changes-toolbar\s*\{[^}]*display: grid;/s);
    expect(styles).toMatch(/@container git-review \(max-width: 30rem\)\s*\{[\s\S]*\.git-scope-tabs\s*\{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/s);
    expect(styles).toMatch(/\.git-scope-tab\s*\{[^}]*white-space: nowrap;/s);
  });

  it("supports collapsible sidebar and top-level navigation controls", () => {
    expect(styles).toMatch(/\.workbench--sidebar-collapsed\s*\{[^}]*grid-template-columns:\s*1fr;/s);
    expect(styles).toMatch(/\.workbench--sidebar-collapsed\s*\.sidebar\s*\{[^}]*display:\s*none;/s);
    expect(styles).toMatch(/\.sidebar-top-bar\s*\{[^}]*height:\s*2\.75rem;/s);
    expect(styles).toMatch(/\.collapsed-navigation-bar\s*\{[^}]*position:\s*absolute;/s);
    expect(styles).toMatch(/\.window-traffic-spacer\s*\{[^}]*width:\s*86px;/s);
    expect(styles).toMatch(/\.nav-icon-btn\s*\{[^}]*-webkit-app-region:\s*no-drag\s*!important;/s);
    expect(styles).toMatch(/\.workbench--sidebar-collapsed\s*\.workspace-header::before\s*\{[^}]*-webkit-app-region:\s*no-drag\s*!important;/s);
    expect(styles).toMatch(/\.workspace-body--session-centered,\s*\.workbench--sidebar-collapsed\s*\.workspace-body--session-centered\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/s);
    expect(styles).toMatch(/\.workspace-header--empty\s*\{[^}]*position:\s*absolute;[^}]*-webkit-app-region:\s*drag;/s);
    expect(styles).toMatch(/\.settings-page-header\s*\{[^}]*-webkit-app-region:\s*drag;/s);
    expect(dockStyles).toMatch(/\.workspace-body__actions\s*\{[^}]*-webkit-app-region:\s*no-drag\s*!important;/s);
  });
});

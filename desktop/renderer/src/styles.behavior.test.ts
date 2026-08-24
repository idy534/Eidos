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
    expect(dockStyles).toMatch(/\.workspace-body--session-centered \.workspace-main-column > \.session-header\s*\{[^}]*padding-right: calc\(var\(--workspace-action-rail-width\) \+ 1\.5rem\);/s);
  });

  it("keeps a narrow dock beside the session and aligns its header controls", () => {
    expect(dockStyles).toMatch(/\.workspace-body--with-dock\s*\{[^}]*grid-template-columns: minmax\(16rem, 1fr\) 0\.5rem var\(--workspace-dock-width\);/s);
    expect(dockStyles).not.toMatch(/\.workspace-body--with-dock:not\(\.workspace-body--expanded\)\s*\.workspace-dock\s*\{[^}]*position: absolute;/s);
    expect(dockStyles).toMatch(/\.workspace-body__actions\s*\{[^}]*top: 0\.5rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__add \.dropdown-trigger,\s*\.workspace-dock__actions \.icon-button\s*\{[^}]*width: 2\.25rem;[^}]*height: 2\.25rem;[^}]*min-height: 2\.25rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__add \.dropdown-trigger > span\[aria-hidden\]\s*\{[^}]*font-size: 1\.25rem;[^}]*line-height: 1;/s);
  });

  it("flows open dock controls in one fixed-size header group", () => {
    expect(dockStyles).toMatch(/\.workspace-body\s*\{[^}]*--workspace-action-rail-width: 5rem;/s);
    expect(dockStyles).toMatch(/\.workspace-body__actions\s*\{[^}]*width: var\(--workspace-action-rail-width\);/s);
    expect(dockStyles).toMatch(/\.workspace-dock__header\s*\{[^}]*padding: 0\.45rem 0\.65rem;/s);
    expect(dockStyles).toMatch(/\.workspace-dock__actions\s*\{[^}]*flex: none;[^}]*gap: 0\.35rem;/s);
  });

  it("renders compact single-column diff colors", () => {
    expect(styles).toMatch(/\.git-diff-unified col\.diff-gutter-col:nth-of-type\(2\)/);
    expect(styles).toMatch(/\.git-diff-unified \.diff-code-insert\s*\{[^}]*color: var\(--status-success\);/s);
    expect(styles).toMatch(/\.git-diff-unified \.diff-code-delete\s*\{[^}]*color: var\(--status-danger\);/s);
  });
});

import { readFileSync } from "node:fs";
import path from "node:path";

const rendererRoot = path.resolve(process.cwd(), "desktop/renderer/src");

/** Read the same ordered local stylesheets as the renderer entry point. */
export function readRendererStyleModules() {
  const entry = readFileSync(path.join(rendererRoot, "styles.css"), "utf8");
  return Array.from(entry.matchAll(/^@import "(\.\/[^\"]+\.css)";$/gm), ([, relative]) => {
    const file = path.resolve(rendererRoot, relative!);
    return { file, styles: readFileSync(file, "utf8") };
  });
}

export function readRendererStyles(): string {
  return readRendererStyleModules().map(({ styles }) => styles).join("\n");
}

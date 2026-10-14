import { rmSync } from "node:fs";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

export function cleanRendererAssets(rendererOutputDirectory) {
  rmSync(join(rendererOutputDirectory, "assets"), { force: true, recursive: true });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const rendererOutputDirectory = fileURLToPath(new URL("../dist/renderer", import.meta.url));
  cleanRendererAssets(rendererOutputDirectory);
}

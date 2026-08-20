import { chmod } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

if (process.platform === "darwin") {
  const require = createRequire(import.meta.url);
  const packageRoot = path.dirname(require.resolve("node-pty/package.json"));
  await chmod(
    path.join(packageRoot, "prebuilds", `darwin-${process.arch}`, "spawn-helper"),
    0o755,
  );
}

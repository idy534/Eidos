import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const loader = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
  "resources/runtime-dependencies/node/runtime-loader.mjs",
);

function writePackage(root, name, files) {
  const packageRoot = path.join(root, "node_modules", name);
  mkdirSync(packageRoot, { recursive: true });
  for (const [relative, contents] of Object.entries(files)) {
    const file = path.join(packageRoot, relative);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, contents);
  }
}

const root = mkdtempSync(path.join(os.tmpdir(), "eidos-loader-test-"));
const workspace = path.join(root, "workspace");
const runtime = path.join(root, "runtime");
mkdirSync(workspace, { recursive: true });
mkdirSync(runtime, { recursive: true });

writePackage(root, "host-only-esm", {
  "package.json": JSON.stringify({
    name: "host-only-esm",
    type: "module",
    exports: "./index.js",
  }),
  "index.js": "export default 'host';",
});
writePackage(root, "host-only-cjs", {
  "package.json": JSON.stringify({
    name: "host-only-cjs",
    exports: "./index.cjs",
  }),
  "index.cjs": "module.exports = 'host';",
});

writePackage(workspace, "conflicting-package", {
  "package.json": JSON.stringify({
    name: "conflicting-package",
    type: "module",
    exports: ".",
  }),
  "index.js": "import { value } from './nested.js'; export default `workspace:${value}`;",
  "nested.js": "export const value = 'nested';",
});
writePackage(workspace, "conflicting-package/node_modules/nested-dependency", {
  "package.json": JSON.stringify({
    name: "nested-dependency",
    type: "module",
    exports: "./index.js",
  }),
  "index.js": "export default 'workspace-nested';",
});
writePackage(runtime, "conflicting-package", {
  "package.json": JSON.stringify({
    name: "conflicting-package",
    type: "module",
    exports: {
      ".": "./index.js",
      "./self": "./self.mjs",
    },
  }),
  "index.js":
    "import { value } from './nested.js'; import nested from 'nested-dependency'; import self from 'conflicting-package/self'; export default `runtime:${value}:${nested}:${self}`;",
  "nested.js": "export const value = 'relative';",
  "self.mjs": "export default 'self';",
});
writePackage(runtime, "conflicting-package/node_modules/nested-dependency", {
  "package.json": JSON.stringify({
    name: "nested-dependency",
    type: "module",
    exports: {
      ".": {
        import: "./import.mjs",
        default: "./default.mjs",
      },
    },
  }),
  "import.mjs": "export default 'runtime-nested';",
  "default.mjs": "export default 'runtime-nested-default';",
});
writePackage(runtime, "nested-dependency", {
  "package.json": JSON.stringify({
    name: "nested-dependency",
    type: "module",
    exports: "./index.js",
  }),
  "index.js": "export default 'runtime-root';",
});

writePackage(workspace, "cjs-conflicting-package", {
  "package.json": JSON.stringify({
    name: "cjs-conflicting-package",
    main: "default.cjs",
    exports: {
      require: "./require.cjs",
      default: "./default.cjs",
    },
  }),
  "default.cjs": "module.exports = 'workspace-default';",
  "require.cjs": "module.exports = `workspace-cjs:${require('./nested.cjs')}`;",
  "nested.cjs": "module.exports = 'nested';",
});
writePackage(workspace, "cjs-conflicting-package/node_modules/nested-cjs-dependency", {
  "package.json": JSON.stringify({
    name: "nested-cjs-dependency",
    exports: "./index.cjs",
  }),
  "index.cjs": "module.exports = 'workspace-nested-cjs';",
});
writePackage(runtime, "cjs-conflicting-package", {
  "package.json": JSON.stringify({
    name: "cjs-conflicting-package",
    main: "default.cjs",
    exports: {
      ".": {
        require: "./require.cjs",
        default: "./default.cjs",
      },
      "./self": "./self.cjs",
    },
  }),
  "default.cjs": "module.exports = 'runtime-default';",
  "require.cjs":
    "module.exports = `runtime-cjs:${require('./nested.cjs')}:${require('nested-cjs-dependency')}:${require('cjs-conflicting-package/self')}`;",
  "nested.cjs": "module.exports = 'relative';",
  "self.cjs": "module.exports = 'self-cjs';",
});
writePackage(runtime, "cjs-conflicting-package/node_modules/nested-cjs-dependency", {
  "package.json": JSON.stringify({
    name: "nested-cjs-dependency",
    exports: "./index.cjs",
  }),
  "index.cjs": "module.exports = 'runtime-nested-cjs';",
});
writePackage(runtime, "nested-cjs-dependency", {
  "package.json": JSON.stringify({
    name: "nested-cjs-dependency",
    exports: "./index.cjs",
  }),
  "index.cjs": "module.exports = 'runtime-root-cjs';",
});

const escapedPackageRoot = path.join(root, "escaped-package");
mkdirSync(escapedPackageRoot, { recursive: true });
writeFileSync(
  path.join(escapedPackageRoot, "package.json"),
  JSON.stringify({ name: "escaped-package", exports: "./index.js" }),
);
writeFileSync(path.join(escapedPackageRoot, "index.js"), "export default 'escaped';");
symlinkSync(
  escapedPackageRoot,
  path.join(runtime, "node_modules", "escaped-package"),
  "dir",
);
const linkedRuntimeNodeModules = path.join(root, "runtime-node-modules-link");
symlinkSync(path.join(runtime, "node_modules"), linkedRuntimeNodeModules, "dir");

const esmEntry = path.join(workspace, "entry.mjs");
writeFileSync(
  esmEntry,
  "import value from 'conflicting-package'; import fs from 'node:fs'; console.log(`${value}|${typeof fs.readFile}`);",
);
const cjsEntry = path.join(workspace, "entry.cjs");
writeFileSync(
  cjsEntry,
  "const value = require('cjs-conflicting-package'); const path = require('node:path'); console.log(`${value}|${typeof path.join}`);",
);
const hostEsmEntry = path.join(workspace, "host-entry.mjs");
writeFileSync(hostEsmEntry, "import value from 'host-only-esm'; console.log(value);");
const hostCjsEntry = path.join(workspace, "host-entry.cjs");
writeFileSync(hostCjsEntry, "console.log(require('host-only-cjs'));");
const escapedEntry = path.join(workspace, "escaped-entry.mjs");
writeFileSync(escapedEntry, "import value from 'escaped-package'; console.log(value);");

const environment = {
  ...process.env,
  NODE_OPTIONS: `--import=${pathToFileURL(loader).href}`,
  RUNTIME_NODE_MODULES: path.join(runtime, "node_modules"),
};
delete environment.NODE_PATH;

assert.equal(
  execFileSync(process.execPath, [esmEntry], { env: environment, encoding: "utf8" }).trim(),
  "runtime:relative:runtime-nested:self|function",
);
assert.equal(
  execFileSync(process.execPath, [cjsEntry], { env: environment, encoding: "utf8" }).trim(),
  "runtime-cjs:relative:runtime-nested-cjs:self-cjs|function",
);

const linkedEnvironment = {
  ...environment,
  RUNTIME_NODE_MODULES: linkedRuntimeNodeModules,
};
assert.equal(
  execFileSync(process.execPath, [esmEntry], {
    env: linkedEnvironment,
    encoding: "utf8",
  }).trim(),
  "runtime:relative:runtime-nested:self|function",
);

for (const entry of [hostEsmEntry, hostCjsEntry]) {
  const result = spawnSync(process.execPath, [entry], {
    env: environment,
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0);
  assert.match(
    `${result.stderr}\n${result.stdout}`,
    /outside RUNTIME_NODE_MODULES/,
  );
}

const escapedResult = spawnSync(process.execPath, [escapedEntry], {
  env: environment,
  encoding: "utf8",
});
assert.notEqual(escapedResult.status, 0);
assert.match(
  `${escapedResult.stderr}\n${escapedResult.stdout}`,
  /outside RUNTIME_NODE_MODULES/,
);

const missingRoot = { ...environment };
delete missingRoot.RUNTIME_NODE_MODULES;
const missing = spawnSync(process.execPath, ["-e", ""], {
  env: missingRoot,
  encoding: "utf8",
});
assert.notEqual(missing.status, 0);
assert.match(`${missing.stderr}\n${missing.stdout}`, /RUNTIME_NODE_MODULES/);

const missingDirectory = {
  ...environment,
  RUNTIME_NODE_MODULES: path.join(runtime, "missing-node-modules"),
};
const missingDirectoryResult = spawnSync(process.execPath, ["-e", ""], {
  env: missingDirectory,
  encoding: "utf8",
});
assert.notEqual(missingDirectoryResult.status, 0);
assert.match(
  `${missingDirectoryResult.stderr}\n${missingDirectoryResult.stdout}`,
  /RUNTIME_NODE_MODULES/,
);

console.log("dependency runtime loader checks passed");

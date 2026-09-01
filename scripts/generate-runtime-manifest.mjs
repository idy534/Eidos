#!/usr/bin/env node

import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync,
  lstatSync,
  openSync,
  readFileSync,
  readSync,
  readdirSync,
  realpathSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";


const MANIFEST_NAME = "runtime.json";
const TARGET = "darwin-arm64";
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const INVENTORY_ROOTS = [
  "python/bin",
  "python/lib",
  "app",
  "dependencies/node",
  "dependencies/python",
];
const PREFERRED_INVENTORY_PATHS = new Set([
  "python/bin/python3",
  "dependencies/node/bin/node",
  "app/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg",
]);
const MAX_MANIFEST_BYTES = 2 * 1024 * 1024;
const MAX_MANIFEST_FILES = 32 * 1024;
const MAX_RUNTIME_FILE_BYTES = 512 * 1024 * 1024;
const MAX_RUNTIME_INVENTORY_BYTES = 1024 * 1024 * 1024;
const MAX_CONFIG_BYTES = 2 * 1024 * 1024;
const HASH_CHUNK_BYTES = 1024 * 1024;


function fail(message) {
  throw new Error(`Runtime manifest error: ${message}`);
}


function asAbsolutePath(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    fail(`${label} must be a non-empty path`);
  }
  const absolute = path.resolve(value);
  if (!path.isAbsolute(absolute)) {
    fail(`${label} must be absolute`);
  }
  try {
    return realpathSync(absolute);
  } catch {
    return absolute;
  }
}


function assertRelativePath(value, label) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.startsWith("/") ||
    value.includes("\\") ||
    value.split("/").some((part) => part === "." || part === ".." || part.length === 0)
  ) {
    fail(`${label} must be a normalized relative POSIX path`);
  }
}


function resolveInside(bundleRoot, relativePath, label) {
  assertRelativePath(relativePath, label);
  const absolute = path.resolve(bundleRoot, ...relativePath.split("/"));
  const relative = path.relative(bundleRoot, absolute);
  if (relative === "" || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    fail(`${label} escapes the Runtime bundle: ${relativePath}`);
  }
  return absolute;
}


function toManifestPath(bundleRoot, absolutePath) {
  const relative = path.relative(bundleRoot, absolutePath);
  if (relative === "" || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    fail(`path escapes the Runtime bundle: ${absolutePath}`);
  }
  return relative.split(path.sep).join("/");
}


function resolvedFileForHash(filePath, bundleRoot, label) {
  const metadata = lstatSync(filePath, { throwIfNoEntry: false });
  if (metadata == null) {
    fail(`${label} is missing: ${filePath}`);
  }
  let actualPath;
  try {
    actualPath = realpathSync(filePath);
  } catch (error) {
    fail(`${label} cannot be resolved: ${filePath}: ${error instanceof Error ? error.message : String(error)}`);
  }
  const actualRelative = path.relative(bundleRoot, actualPath);
  if (actualRelative === ".." || actualRelative.startsWith(`..${path.sep}`) || path.isAbsolute(actualRelative)) {
    fail(`${label} symlink escapes the Runtime bundle: ${filePath}`);
  }
  const actualMetadata = lstatSync(actualPath);
  if (!actualMetadata.isFile()) {
    fail(`${label} is not a regular file: ${filePath}`);
  }
  return actualPath;
}


function compareBytes(left, right) {
  return Buffer.from(left).compare(Buffer.from(right));
}


function boundedReadFile(filePath, label, limit = MAX_CONFIG_BYTES) {
  const metadata = lstatSync(filePath, { throwIfNoEntry: false });
  if (metadata == null || !metadata.isFile()) {
    fail(`${label} is not a regular file: ${filePath}`);
  }
  if (metadata.size > limit) {
    fail(`${label} exceeds the bounded ${limit}-byte limit`);
  }
  return readFileSync(filePath);
}


function sha256File(filePath, bundleRoot, label) {
  const actualPath = resolvedFileForHash(filePath, bundleRoot, label);
  const metadata = statSync(actualPath);
  if (metadata.size > MAX_RUNTIME_FILE_BYTES) {
    fail(`${label} exceeds the bounded ${MAX_RUNTIME_FILE_BYTES}-byte file limit`);
  }
  const digest = createHash("sha256");
  const descriptor = openSync(actualPath, "r");
  const buffer = Buffer.allocUnsafe(HASH_CHUNK_BYTES);
  try {
    let offset = 0;
    while (offset < metadata.size) {
      const read = readSync(
        descriptor,
        buffer,
        0,
        Math.min(buffer.length, metadata.size - offset),
        offset,
      );
      if (read === 0) fail(`${label} ended before its declared size: ${filePath}`);
      digest.update(buffer.subarray(0, read));
      offset += read;
    }
  } finally {
    closeSync(descriptor);
  }
  return digest.digest("hex");
}


function walkInventory(bundleRoot, rootRelativePath) {
  const rootPath = resolveInside(bundleRoot, rootRelativePath, "inventory root");
  if (!existsSync(rootPath)) {
    fail(`inventory root is missing: ${rootRelativePath}`);
  }
  const files = [];
  const visit = (directoryPath) => {
    const entries = readdirSync(directoryPath, { withFileTypes: true })
      .sort((left, right) => compareBytes(left.name, right.name));
    for (const entry of entries) {
      const entryPath = path.join(directoryPath, entry.name);
      const relativePath = toManifestPath(bundleRoot, entryPath);
      if (entry.isDirectory()) {
        visit(entryPath);
        continue;
      }
      if (!entry.isFile() && !entry.isSymbolicLink()) {
        fail(`inventory contains a non-regular entry: ${relativePath}`);
      }
      const canonicalPath = resolvedFileForHash(entryPath, bundleRoot, "inventory file");
      files.push({
        path: relativePath,
        canonicalPath,
      });
    }
  };
  visit(rootPath);
  return files;
}


function inventoryFiles(bundleRoot) {
  const candidates = INVENTORY_ROOTS.flatMap((rootRelativePath) =>
    walkInventory(bundleRoot, rootRelativePath));
  candidates.sort((left, right) => {
    const leftPriority = PREFERRED_INVENTORY_PATHS.has(left.path) ? 0 : 1;
    const rightPriority = PREFERRED_INVENTORY_PATHS.has(right.path) ? 0 : 1;
    return leftPriority - rightPriority || compareBytes(left.path, right.path);
  });
  const canonicalPaths = new Set();
  const files = candidates
    .filter(({ canonicalPath }) => {
      if (canonicalPaths.has(canonicalPath)) return false;
      canonicalPaths.add(canonicalPath);
      return true;
    })
    .map(({ path: relativePath }) => ({
      path: relativePath,
      sha256: sha256File(
        resolveInside(bundleRoot, relativePath, "inventory file"),
        bundleRoot,
        "inventory file",
      ),
    }))
    .sort((left, right) => compareBytes(left.path, right.path));
  if (files.length > MAX_MANIFEST_FILES) {
    fail(`files inventory exceeds the bounded ${MAX_MANIFEST_FILES}-entry limit`);
  }
  let totalBytes = 0;
  for (const file of files) {
    const actualPath = resolvedFileForHash(
      resolveInside(bundleRoot, file.path, "inventory file"),
      bundleRoot,
      "inventory file",
    );
    totalBytes += statSync(actualPath).size;
    if (totalBytes > MAX_RUNTIME_INVENTORY_BYTES) {
      fail(`files inventory exceeds the bounded ${MAX_RUNTIME_INVENTORY_BYTES}-byte limit`);
    }
  }
  return files;
}


function parsePythonLock(lockPath) {
  const entries = [];
  const lines = boundedReadFile(lockPath, "Python dependency lock").toString("utf8").split(/\r?\n/);
  for (const [lineNumber, rawLine] of lines.entries()) {
    const line = rawLine.trim();
    if (line.length === 0 || line.startsWith("#")) {
      continue;
    }
    const match = line.match(
      /^([A-Za-z0-9_.-]+)==([^\s#]+)\s+((?:--hash=sha256:[0-9a-f]{64}\s*)+)#\s*import-name=([A-Za-z0-9_.-]+)\s*$/i,
    );
    if (!match) {
      fail(`invalid Python dependency lock line ${lineNumber + 1}: ${rawLine}`);
    }
    entries.push({
      name: match[1],
      version: match[2],
      importName: match[4],
    });
  }
  if (entries.length === 0) {
    fail("Python dependency lock contains no packages");
  }
  return entries;
}


function collectNodePackages(nodeModulesPath, bundleRoot) {
  const packages = new Map();
  const visit = (directoryPath) => {
    const entries = readdirSync(directoryPath, { withFileTypes: true })
      .sort((left, right) => compareBytes(left.name, right.name));
    for (const entry of entries) {
      if (entry.name === ".pnpm" || entry.name === ".modules.yaml") {
        continue;
      }
      const entryPath = path.join(directoryPath, entry.name);
      if (entry.isSymbolicLink()) {
        fail(`Node dependency output contains a symlink: ${toManifestPath(bundleRoot, entryPath)}`);
      }
      if (!entry.isDirectory()) {
        continue;
      }
      const packageJsonPath = path.join(entryPath, "package.json");
      if (existsSync(packageJsonPath)) {
        const packageJson = JSON.parse(boundedReadFile(packageJsonPath, "Node package metadata").toString("utf8"));
        if (typeof packageJson.name !== "string" || typeof packageJson.version !== "string") {
          fail(`Node package metadata is incomplete: ${packageJsonPath}`);
        }
        const existing = packages.get(packageJson.name);
        if (existing != null && existing.version !== packageJson.version) {
          fail(`Node package has conflicting versions: ${packageJson.name}`);
        }
        packages.set(packageJson.name, {
          name: packageJson.name,
          version: packageJson.version,
        });
        continue;
      }
      visit(entryPath);
    }
  };
  if (!existsSync(nodeModulesPath)) {
    fail(`Node modules root is missing: ${toManifestPath(bundleRoot, nodeModulesPath)}`);
  }
  visit(nodeModulesPath);
  if (packages.size === 0) {
    fail("Node dependency output contains no package metadata");
  }
  return [...packages.values()].sort((left, right) => compareBytes(left.name, right.name));
}


function generatedExecutableName(relativePath, usedNames) {
  const safePath = relativePath.replaceAll(/[^A-Za-z0-9._-]+/g, "-");
  let name = `runtime-${safePath}`;
  if (name.length > 128) {
    const digest = createHash("sha256").update(relativePath).digest("hex").slice(0, 16);
    name = `${name.slice(0, 111)}-${digest}`;
  }
  let suffix = 1;
  const baseName = name;
  while (usedNames.has(name)) {
    name = `${baseName.slice(0, 126 - String(suffix).length)}-${suffix}`;
    suffix += 1;
  }
  usedNames.add(name);
  return name;
}


function executableEntries(bundleRoot, options, files) {
  const fixedEntries = new Map([
    ["python/bin/python3", { name: "python3", version: options.pythonVersion }],
    ["dependencies/node/bin/node", { name: "node", version: options.nodeVersion }],
    [
      "app/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg",
      { name: "rg", version: options.ripgrepVersion },
    ],
  ]);
  const usedNames = new Set([...fixedEntries.values()].map(({ name }) => name));
  const entries = [];
  for (const file of files) {
    const absolutePath = resolveInside(bundleRoot, file.path, "executable inventory file");
    const metadata = statSync(absolutePath);
    if ((metadata.mode & 0o111) === 0) continue;
    const fixed = fixedEntries.get(file.path);
    const entry = fixed ?? {
      name: generatedExecutableName(file.path, usedNames),
      version: options.pythonVersion,
    };
    entries.push({
      ...entry,
      path: file.path,
      sha256: sha256File(absolutePath, bundleRoot, `executable ${entry.name}`),
    });
  }
  for (const [relativePath] of fixedEntries) {
    if (!entries.some((entry) => entry.path === relativePath)) {
      fail(`required executable is missing from files inventory: ${relativePath}`);
    }
  }
  return entries.sort((left, right) => compareBytes(left.path, right.path));
}


function writeManifest(manifestPath, manifest) {
  const temporaryPath = `${manifestPath}.${process.pid}.tmp`;
  const serialized = `${JSON.stringify(manifest, null, 2)}\n`;
  if (Buffer.byteLength(serialized, "utf8") > MAX_MANIFEST_BYTES) {
    fail(`manifest exceeds the bounded ${MAX_MANIFEST_BYTES}-byte limit`);
  }
  if (manifest.files.length > MAX_MANIFEST_FILES) {
    fail(`files inventory exceeds the bounded ${MAX_MANIFEST_FILES}-entry limit`);
  }
  writeFileSync(temporaryPath, serialized, { mode: 0o644 });
  renameSync(temporaryPath, manifestPath);
}


function readManifest(bundleRoot, manifestPath) {
  const candidateManifestPath = manifestPath ?? path.join(bundleRoot, MANIFEST_NAME);
  let resolvedManifestPath;
  try {
    resolvedManifestPath = realpathSync(candidateManifestPath);
  } catch {
    resolvedManifestPath = path.resolve(candidateManifestPath);
  }
  if (toManifestPath(bundleRoot, resolvedManifestPath) !== MANIFEST_NAME) {
    fail("manifest must be the Runtime root runtime.json");
  }
  if (!existsSync(resolvedManifestPath)) {
    fail(`manifest is missing: ${resolvedManifestPath}`);
  }
  let manifest;
  try {
    manifest = JSON.parse(
      boundedReadFile(resolvedManifestPath, "Runtime manifest", MAX_MANIFEST_BYTES).toString("utf8"),
    );
  } catch (error) {
    fail(`manifest is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (manifest == null || typeof manifest !== "object" || Array.isArray(manifest)) {
    fail("manifest must be a JSON object");
  }
  return { manifest, manifestPath: resolvedManifestPath };
}


function validateManifestShape(bundleRoot, manifest) {
  if (manifest.schemaVersion !== 1) fail("schemaVersion must be 1");
  if (typeof manifest.bundleId !== "string" || manifest.bundleId.length === 0) fail("bundleId is required");
  if (typeof manifest.bundleVersion !== "string" || manifest.bundleVersion.length === 0) fail("bundleVersion is required");
  if (manifest.target !== TARGET) fail(`target must be ${TARGET}`);
  if (!Array.isArray(manifest.executables) || manifest.executables.length === 0) fail("executables must be non-empty");
  if (!Array.isArray(manifest.pythonPath)) fail("pythonPath must be an array");
  if (manifest.pythonPath.length !== 1 || manifest.pythonPath[0] !== "dependencies/python") {
    fail("pythonPath must be exactly dependencies/python");
  }
  if (!Array.isArray(manifest.pythonPackages)) fail("pythonPackages must be an array");
  if (typeof manifest.nodeModules !== "string") fail("nodeModules is required");
  if (!Array.isArray(manifest.nodePackages)) fail("nodePackages must be an array");
  if (!Array.isArray(manifest.nativeBinPaths) || manifest.nativeBinPaths.length !== 0) fail("nativeBinPaths must be an empty array");
  if (!Array.isArray(manifest.files)) fail("files must be an array");

  for (const relativePath of manifest.pythonPath) {
    resolveInside(bundleRoot, relativePath, "pythonPath");
  }
  resolveInside(bundleRoot, manifest.nodeModules, "nodeModules");
  if (manifest.nodeLoader != null) {
    resolveInside(bundleRoot, manifest.nodeLoader, "nodeLoader");
  }
  for (const executable of manifest.executables) {
    if (typeof executable !== "object" || executable == null) fail("executable entry must be an object");
    assertRelativePath(executable.path, "executable path");
    if (typeof executable.name !== "string" || typeof executable.version !== "string") fail("executable metadata is incomplete");
    if (!SHA256_PATTERN.test(executable.sha256)) fail(`invalid executable SHA256: ${executable.path}`);
  }
  for (const file of manifest.files) {
    if (typeof file !== "object" || file == null) fail("file entry must be an object");
    assertRelativePath(file.path, "inventory file path");
    if (file.path === MANIFEST_NAME) fail("runtime.json cannot be included in its own inventory");
    if (!SHA256_PATTERN.test(file.sha256)) fail(`invalid inventory SHA256: ${file.path}`);
  }
  const inventoryPaths = new Set(manifest.files.map(({ path: relativePath }) => relativePath));
  for (const executable of manifest.executables) {
    if (!inventoryPaths.has(executable.path)) {
      fail(`executable is missing from files inventory: ${executable.path}`);
    }
  }
}


function manifestOptions(options) {
  const bundleRoot = asAbsolutePath(options.bundleRoot, "bundleRoot");
  const pythonLock = asAbsolutePath(options.pythonLock, "pythonLock");
  const nodeModules = options.nodeModules ?? "dependencies/node/node_modules";
  const nodeLoader = options.nodeLoader ?? "dependencies/node/runtime-loader.mjs";
  const pythonPath = options.pythonPath ?? ["dependencies/python"];
  if (!Array.isArray(pythonPath) || pythonPath.length === 0) {
    fail("pythonPath must contain the isolated dependency root");
  }
  if (pythonPath.length !== 1 || pythonPath[0] !== "dependencies/python") {
    fail("pythonPath must be exactly dependencies/python");
  }
  return {
    ...options,
    bundleRoot,
    pythonLock,
    nodeModules,
    nodeLoader,
    pythonPath,
  };
}


export function generateRuntimeManifestSync(options) {
  const normalized = manifestOptions(options);
  const { bundleRoot } = normalized;
  const pythonPackages = parsePythonLock(normalized.pythonLock);
  const nodeModulesPath = resolveInside(bundleRoot, normalized.nodeModules, "nodeModules");
  for (const relativePath of normalized.pythonPath) {
    resolveInside(bundleRoot, relativePath, "pythonPath");
  }
  resolveInside(bundleRoot, normalized.nodeLoader, "nodeLoader");
  const files = inventoryFiles(bundleRoot);
  const manifest = {
    schemaVersion: 1,
    bundleId: normalized.bundleId,
    bundleVersion: normalized.bundleVersion,
    target: TARGET,
    executables: executableEntries(bundleRoot, normalized, files),
    pythonPath: [...normalized.pythonPath],
    pythonPackages,
    nodeModules: normalized.nodeModules,
    nodePackages: collectNodePackages(nodeModulesPath, bundleRoot),
    nodeLoader: normalized.nodeLoader,
    nativeBinPaths: [],
    files,
  };
  const manifestPath = path.join(bundleRoot, MANIFEST_NAME);
  writeManifest(manifestPath, manifest);
  return manifest;
}


export function refreshManifestHashesSync({ bundleRoot, manifestPath } = {}) {
  const normalizedBundleRoot = asAbsolutePath(bundleRoot, "bundleRoot");
  const loaded = readManifest(normalizedBundleRoot, manifestPath);
  validateManifestShape(normalizedBundleRoot, loaded.manifest);
  const executables = loaded.manifest.executables.map((executable) => ({
    ...executable,
    sha256: sha256File(
      resolveInside(normalizedBundleRoot, executable.path, `executable ${executable.name}`),
      normalizedBundleRoot,
      `executable ${executable.name}`,
    ),
  }));
  const files = inventoryFiles(normalizedBundleRoot);
  const refreshed = {
    ...loaded.manifest,
    executables,
    files,
  };
  writeManifest(loaded.manifestPath, refreshed);
  return refreshed;
}


export function verifyRuntimeManifestSync({ bundleRoot, manifestPath } = {}) {
  const normalizedBundleRoot = asAbsolutePath(bundleRoot, "bundleRoot");
  const loaded = readManifest(normalizedBundleRoot, manifestPath);
  validateManifestShape(normalizedBundleRoot, loaded.manifest);
  for (const executable of loaded.manifest.executables) {
    const actualHash = sha256File(
      resolveInside(normalizedBundleRoot, executable.path, `executable ${executable.name}`),
      normalizedBundleRoot,
      `executable ${executable.name}`,
    );
    if (actualHash !== executable.sha256) {
      fail(`executable hash mismatch for ${executable.path}`);
    }
  }
  const actualFiles = inventoryFiles(normalizedBundleRoot);
  const expectedFiles = [...loaded.manifest.files].sort((left, right) => compareBytes(left.path, right.path));
  if (JSON.stringify(actualFiles) !== JSON.stringify(expectedFiles)) {
    fail("inventory hash or file set mismatch; the Runtime bundle may be tampered");
  }
  return true;
}


function parseArguments(argv) {
  const options = { pythonPath: [] };
  const optionKey = (argument) => argument
    .slice(2)
    .split("-")
    .map((part, index) => index === 0 ? part : `${part[0].toUpperCase()}${part.slice(1)}`)
    .join("");
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--verify") {
      options.verify = true;
      continue;
    }
    const valueArguments = new Set([
      "--bundle-root",
      "--bundle-id",
      "--bundle-version",
      "--python-version",
      "--node-version",
      "--ripgrep-version",
      "--python-lock",
      "--python-path",
      "--node-modules",
      "--node-loader",
    ]);
    if (!valueArguments.has(argument)) {
      fail(`unknown argument: ${argument}`);
    }
    const value = argv[index + 1];
    if (value == null || value.startsWith("--")) {
      fail(`missing value for ${argument}`);
    }
    index += 1;
    if (argument === "--python-path") {
      options.pythonPath.push(value);
    } else {
      options[optionKey(argument)] = value;
    }
  }
  if (typeof options.bundleRoot !== "string") fail("--bundle-root is required");
  if (options.verify) return options;
  for (const required of ["bundleId", "bundleVersion", "pythonVersion", "nodeVersion", "ripgrepVersion", "pythonLock"]) {
    if (typeof options[required] !== "string") fail(`--${required.replaceAll(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)} is required`);
  }
  return options;
}


async function main() {
  try {
    const options = parseArguments(process.argv.slice(2));
    if (options.verify) {
      verifyRuntimeManifestSync(options);
      console.log(`Verified Runtime manifest at ${path.join(path.resolve(options.bundleRoot), MANIFEST_NAME)}`);
      return;
    }
    const manifest = generateRuntimeManifestSync(options);
    console.log(JSON.stringify({
      manifest: path.join(path.resolve(options.bundleRoot), MANIFEST_NAME),
      executables: manifest.executables,
      pythonPackages: manifest.pythonPackages,
      nodePackages: manifest.nodePackages,
      fileCount: manifest.files.length,
    }));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}


if (process.argv[1] != null && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}

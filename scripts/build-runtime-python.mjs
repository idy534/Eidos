#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";


const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const controlledBuildRoot = path.join(repositoryRoot, "build", "macos-runtime");
const defaultRequirements = path.join(
  repositoryRoot,
  "resources",
  "runtime-dependencies",
  "python",
  "requirements.lock",
);
const defaultTopLevel = path.join(
  repositoryRoot,
  "resources",
  "runtime-dependencies",
  "python",
  "top_level.txt",
);
const MAX_CONFIG_BYTES = 2 * 1024 * 1024;


function fail(message) {
  throw new Error(`Python Runtime dependency build error: ${message}`);
}


function absolutePath(value, label) {
  if (typeof value !== "string" || value.length === 0) fail(`${label} is required`);
  return path.resolve(value);
}


function isContained(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === "" || (
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}


function assertControlledTarget(targetRoot) {
  if (!isContained(targetRoot, controlledBuildRoot) || targetRoot === controlledBuildRoot) {
    fail(
      `target must be a child of the controlled build tree ${controlledBuildRoot}: ${targetRoot}`,
    );
  }
  const parent = path.dirname(targetRoot);
  if (!isContained(parent, controlledBuildRoot)) {
    fail(`target parent is outside the controlled build tree: ${parent}`);
  }
  let current = parent;
  while (isContained(current, controlledBuildRoot)) {
    if (existsSync(current) && lstatSync(current).isSymbolicLink()) {
      fail(`target path contains a symlinked directory: ${current}`);
    }
    if (current === controlledBuildRoot) break;
    current = path.dirname(current);
  }
  if (existsSync(targetRoot)) {
    const metadata = lstatSync(targetRoot);
    if (metadata.isSymbolicLink()) fail(`target must not be a symlink: ${targetRoot}`);
    if (!metadata.isDirectory()) fail(`target must be a directory: ${targetRoot}`);
  }
  mkdirSync(parent, { recursive: true });
}


function removeControlledTree(target, parent, label) {
  if (target == null || !isContained(target, parent) || target === parent) {
    fail(`${label} is outside the controlled staging tree: ${target}`);
  }
  if (existsSync(target) && lstatSync(target).isSymbolicLink()) {
    fail(`${label} must not be a symlink: ${target}`);
  }
  rmSync(target, { recursive: true, force: true });
}


function allocateSibling(parent, prefix) {
  const created = mkdtempSync(path.join(parent, prefix));
  removeControlledTree(created, parent, "temporary marker");
  return created;
}


function compareBytes(left, right) {
  return Buffer.from(left).compare(Buffer.from(right));
}


function parseArguments(argv) {
  const options = {
    requirements: defaultRequirements,
    topLevel: defaultTopLevel,
    python: null,
  };
  const keys = new Map([
    ["--target", "target"],
    ["--requirements", "requirements"],
    ["--top-level", "topLevel"],
    ["--python", "python"],
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const key = keys.get(argv[index]);
    if (key == null) fail(`unknown argument: ${argv[index]}`);
    const value = argv[index + 1];
    if (value == null || value.startsWith("--")) fail(`missing value for ${argv[index]}`);
    options[key] = value;
    index += 1;
  }
  options.target = absolutePath(options.target, "--target");
  options.requirements = absolutePath(options.requirements, "--requirements");
  options.topLevel = absolutePath(options.topLevel, "--top-level");
  if (options.python != null) options.python = absolutePath(options.python, "--python");
  return options;
}


function boundedReadFile(filePath, label) {
  if (!existsSync(filePath)) fail(`${label} is missing: ${filePath}`);
  const metadata = lstatSync(filePath);
  if (!metadata.isFile()) fail(`${label} must be a regular file: ${filePath}`);
  if (metadata.size > MAX_CONFIG_BYTES) {
    fail(`${label} exceeds the bounded ${MAX_CONFIG_BYTES}-byte limit`);
  }
  return readFileSync(filePath);
}


function normalizedPackageName(name) {
  return name.toLowerCase().replaceAll(/[_.]+/g, "-");
}


function parseRequirements(requirementsPath) {
  const entries = [];
  const seen = new Set();
  for (const [lineNumber, rawLine] of boundedReadFile(
    requirementsPath,
    "Python dependency lock",
  ).toString("utf8").split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    if (line.length === 0 || line.startsWith("#")) continue;
    const match = line.match(
      /^([A-Za-z0-9_.-]+)==([^\s#]+)\s+((?:--hash=sha256:[0-9a-f]{64}\s*)+)#\s*import-name=([A-Za-z0-9_.-]+)\s*$/i,
    );
    if (!match) fail(`invalid lock line ${lineNumber + 1}: ${rawLine}`);
    const normalizedName = normalizedPackageName(match[1]);
    if (seen.has(normalizedName)) fail(`duplicate package in lock: ${match[1]}`);
    seen.add(normalizedName);
    entries.push({
      name: match[1],
      normalizedName,
      version: match[2],
      importName: match[4],
    });
  }
  if (entries.length === 0) fail("the dependency lock is empty");
  return entries;
}


function parseTopLevel(topLevelPath) {
  const entries = new Map();
  for (const [lineNumber, rawLine] of boundedReadFile(
    topLevelPath,
    "Python top_level mapping",
  ).toString("utf8").split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    if (line.length === 0 || line.startsWith("#")) continue;
    const match = line.match(/^([A-Za-z0-9_.-]+)==([^:]+):\s*([A-Za-z_][A-Za-z0-9_.-]*)$/);
    if (!match) fail(`invalid top_level line ${lineNumber + 1}: ${rawLine}`);
    const key = `${normalizedPackageName(match[1])}==${match[2].trim()}`;
    if (entries.has(key)) fail(`duplicate top_level mapping: ${rawLine}`);
    entries.set(key, match[3]);
  }
  return entries;
}


function readMetadata(metadataRoot) {
  const metadata = boundedReadFile(
    path.join(metadataRoot, "METADATA"),
    "Python package METADATA",
  ).toString("utf8");
  const name = metadata.match(/^Name:\s*(\S.*)$/m)?.[1]?.trim();
  const version = metadata.match(/^Version:\s*(\S.*)$/m)?.[1]?.trim();
  if (name == null || version == null) fail(`package metadata is incomplete: ${metadataRoot}`);
  return { name, version };
}


function findDistributionMetadata(sourceRoot, packageEntry) {
  const candidates = readdirSync(sourceRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && entry.name.endsWith(".dist-info"))
    .sort((left, right) => compareBytes(left.name, right.name));
  const matches = [];
  for (const candidate of candidates) {
    const metadataPath = path.join(sourceRoot, candidate.name);
    const metadata = readMetadata(metadataPath);
    if (
      normalizedPackageName(metadata.name) === packageEntry.normalizedName &&
      metadata.version === packageEntry.version
    ) {
      matches.push(metadataPath);
    }
  }
  if (matches.length === 0) {
    fail(`locked metadata was not found for ${packageEntry.name}==${packageEntry.version}`);
  }
  if (matches.length !== 1) {
    fail(`locked metadata is ambiguous for ${packageEntry.name}==${packageEntry.version}`);
  }
  return matches[0];
}


function assertNoSymlinks(root) {
  const visit = (directoryPath) => {
    for (const entry of readdirSync(directoryPath, { withFileTypes: true })) {
      const entryPath = path.join(directoryPath, entry.name);
      if (entry.isSymbolicLink()) fail(`built dependency output contains a symlink: ${entryPath}`);
      if (entry.isDirectory()) visit(entryPath);
    }
  };
  visit(root);
}


function assertImportRoot(sourceRoot, importName) {
  const relativeImport = importName.replaceAll(".", path.sep);
  const candidates = [
    path.join(sourceRoot, relativeImport),
    path.join(sourceRoot, `${relativeImport}.py`),
  ];
  const matches = candidates.filter((candidate) => existsSync(candidate));
  if (matches.length !== 1) {
    fail(`locked import root is missing or ambiguous: ${importName}`);
  }
  const metadata = lstatSync(matches[0]);
  if (metadata.isSymbolicLink() || (!metadata.isDirectory() && !metadata.isFile())) {
    fail(`locked import root is not a regular directory or file: ${importName}`);
  }
}


function verifyInstalledDependencies(stageRoot, requirements, topLevel) {
  assertNoSymlinks(stageRoot);
  const metadataDirectories = readdirSync(stageRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && entry.name.endsWith(".dist-info"))
    .sort((left, right) => compareBytes(left.name, right.name));
  if (metadataDirectories.length !== requirements.length) {
    fail(
      `installed metadata count ${metadataDirectories.length} does not match the lock count ${requirements.length}`,
    );
  }
  const expectedMetadata = new Set();
  const packages = [];
  for (const packageEntry of requirements) {
    const topLevelImport = topLevel.get(`${packageEntry.normalizedName}==${packageEntry.version}`);
    if (topLevelImport !== packageEntry.importName) {
      fail(`top_level mapping does not match the lock for ${packageEntry.name}`);
    }
    assertImportRoot(stageRoot, topLevelImport);
    const metadataRoot = findDistributionMetadata(stageRoot, packageEntry);
    expectedMetadata.add(path.basename(metadataRoot));
    const metadata = readMetadata(metadataRoot);
    if (
      normalizedPackageName(metadata.name) !== packageEntry.normalizedName ||
      metadata.version !== packageEntry.version
    ) {
      fail(`installed METADATA disagrees with the lock for ${packageEntry.name}`);
    }
    packages.push({
      name: packageEntry.name,
      version: packageEntry.version,
      importName: packageEntry.importName,
    });
  }
  for (const entry of metadataDirectories) {
    if (!expectedMetadata.has(entry.name)) {
      fail(`installed package is not declared in the lock: ${entry.name}`);
    }
  }
  return packages;
}


function installIntoStaging(options, stageRoot) {
  const argumentsList = [
    "pip",
    "install",
    ...(options.python == null ? [] : ["--python", options.python]),
    "--target",
    stageRoot,
    "--requirements",
    options.requirements,
    "--require-hashes",
    "--no-deps",
    "--only-binary",
    ":all:",
    "--link-mode",
    "copy",
  ];
  const environment = { ...process.env };
  delete environment.PYTHONHOME;
  delete environment.PYTHONPATH;
  delete environment.PYTHONNOUSERSITE;
  delete environment.NODE_OPTIONS;
  environment.PYTHONDONTWRITEBYTECODE = "1";
  try {
    execFileSync("uv", argumentsList, {
      cwd: repositoryRoot,
      env: environment,
      stdio: "inherit",
    });
    const uvTargetLock = path.join(stageRoot, ".lock");
    if (existsSync(uvTargetLock)) {
      const metadata = lstatSync(uvTargetLock);
      if (!metadata.isFile() || metadata.isSymbolicLink()) {
        fail("uv target lock must be a regular file");
      }
      rmSync(uvTargetLock);
    }
  } catch (error) {
    fail(`uv pip install failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}


function atomicallyInstall(stageRoot, targetRoot) {
  const parent = path.dirname(targetRoot);
  let backupRoot = null;
  let targetMoved = false;
  let stageMoved = false;
  try {
    if (existsSync(targetRoot)) {
      backupRoot = allocateSibling(parent, ".eidos-python-backup-");
      renameSync(targetRoot, backupRoot);
      targetMoved = true;
    }
    renameSync(stageRoot, targetRoot);
    stageMoved = true;
    if (backupRoot != null) {
      removeControlledTree(backupRoot, parent, "Python dependency backup");
      backupRoot = null;
    }
  } catch (error) {
    if (stageMoved && existsSync(targetRoot)) {
      removeControlledTree(targetRoot, parent, "Python dependency target");
    }
    if (targetMoved && backupRoot != null && existsSync(backupRoot)) {
      renameSync(backupRoot, targetRoot);
      backupRoot = null;
    }
    throw error;
  } finally {
    if (!stageMoved && existsSync(stageRoot)) {
      removeControlledTree(stageRoot, parent, "Python dependency staging");
    }
    if (backupRoot != null && existsSync(backupRoot)) {
      removeControlledTree(backupRoot, parent, "Python dependency backup");
    }
  }
}


function build(options) {
  assertControlledTarget(options.target);
  const requirements = parseRequirements(options.requirements);
  const topLevel = parseTopLevel(options.topLevel);
  const parent = path.dirname(options.target);
  const stageRoot = mkdtempSync(path.join(parent, ".eidos-python-staging-"));
  try {
    installIntoStaging(options, stageRoot);
    const packages = verifyInstalledDependencies(stageRoot, requirements, topLevel);
    atomicallyInstall(stageRoot, options.target);
    return packages;
  } catch (error) {
    if (existsSync(stageRoot)) removeControlledTree(stageRoot, parent, "Python dependency staging");
    throw error;
  }
}


function main() {
  try {
    const options = parseArguments(process.argv.slice(2));
    const packages = build(options);
    console.log(JSON.stringify({ target: options.target, packages }));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}


if (process.argv[1] != null && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}

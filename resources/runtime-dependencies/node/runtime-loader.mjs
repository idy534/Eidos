import { realpathSync, statSync } from "node:fs";
import { createRequire, isBuiltin, registerHooks } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const primary = process.env.RUNTIME_NODE_MODULES;
const declared = process.env.RUNTIME_NODE_MODULES_PATHS
  ? JSON.parse(process.env.RUNTIME_NODE_MODULES_PATHS) : [primary];
if (!Array.isArray(declared) || declared.length === 0 || declared.length > 16) {
  throw new Error("Eidos RUNTIME_NODE_MODULES requires a bounded list of managed roots");
}
const roots = [...new Set(declared.map((root) => {
  if (typeof root !== "string" || !path.isAbsolute(root) || !statSync(root, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error("Eidos RUNTIME_NODE_MODULES requires an existing absolute directory");
  }
  return realpathSync(root);
}))];
const anchors = roots.map((root) => {
  const url = pathToFileURL(path.join(root, "..", ".eidos-runtime-anchor.mjs")).href;
  return { url, require: createRequire(url) };
});
let resolvingRuntimePackage = false;

function insideRoots(filePath) {
  return roots.some((root) => filePath === root || filePath.startsWith(`${root}${path.sep}`));
}

function isPackage(specifier) {
  return typeof specifier === "string" && specifier.length > 0
    && !specifier.startsWith(".") && !specifier.startsWith("/")
    && !specifier.startsWith("#") && !specifier.includes(":") && !isBuiltin(specifier);
}

function insideParent(parentURL) {
  if (!parentURL?.startsWith("file:")) return false;
  try { return insideRoots(realpathSync(fileURLToPath(parentURL))); }
  catch { return false; }
}

function trustedResult(result) {
  if (result?.url?.startsWith("node:")) return result;
  if (!result?.url?.startsWith("file:") || !insideRoots(realpathSync(fileURLToPath(result.url)))) {
    throw new Error("Eidos runtime package resolved outside its managed Node roots");
  }
  return result;
}

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (!isPackage(specifier) || resolvingRuntimePackage) return nextResolve(specifier, context);
    // Keep nearest dependencies inside a trusted pack. Dependencies in another
    // pack use Node's resolver from that pack's anchor, never host/project npm.
    if (insideParent(context.parentURL)) {
      try { return trustedResult(nextResolve(specifier, context)); }
      catch (error) {
        if (!["MODULE_NOT_FOUND", "ERR_MODULE_NOT_FOUND"].includes(error.code)
          && !error.message.includes("outside its managed Node roots")) throw error;
      }
    }
    let missing;
    for (const anchor of anchors) {
      try {
        if (context.conditions?.includes("require")) {
          resolvingRuntimePackage = true;
          let resolved;
          try { resolved = anchor.require.resolve(specifier); }
          finally { resolvingRuntimePackage = false; }
          return trustedResult(nextResolve(resolved, context));
        }
        return trustedResult(nextResolve(specifier, { ...context, parentURL: anchor.url }));
      } catch (error) {
        resolvingRuntimePackage = false;
        if (!["MODULE_NOT_FOUND", "ERR_MODULE_NOT_FOUND"].includes(error.code)) throw error;
        missing = error;
      }
    }
    throw missing ?? new Error(`Eidos runtime package ${specifier} is unavailable`);
  },
});

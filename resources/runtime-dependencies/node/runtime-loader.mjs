import { realpathSync, statSync } from "node:fs";
import { createRequire, isBuiltin, registerHooks } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const runtimeNodeModules = process.env.RUNTIME_NODE_MODULES;
if (
  typeof runtimeNodeModules !== "string" ||
  !runtimeNodeModules ||
  !path.isAbsolute(runtimeNodeModules)
) {
  throw new Error(
    "Eidos runtime Node modules are unavailable: RUNTIME_NODE_MODULES must be an absolute path",
  );
}
try {
  if (!statSync(runtimeNodeModules).isDirectory()) {
    throw new Error("not a directory");
  }
} catch (error) {
  throw new Error(
    "Eidos runtime Node modules are unavailable: RUNTIME_NODE_MODULES must " +
      "name an existing directory",
    { cause: error },
  );
}

const runtimeNodeModulesPath = path.resolve(runtimeNodeModules);
const runtimeNodeModulesCanonicalPath = realpathSync(runtimeNodeModulesPath);
const runtimeNodeModulesCanonicalPrefix =
  `${runtimeNodeModulesCanonicalPath}${path.sep}`;

// The anchor keeps Node's own package resolver rooted at the trusted runtime
// modules directory.  The anchor does not need to exist on disk.
const runtimeRootAnchor = pathToFileURL(
  path.join(
    runtimeNodeModulesCanonicalPath,
    "..",
    ".eidos-runtime-anchor.mjs",
  ),
).href;
const runtimeRequire = createRequire(runtimeRootAnchor);
let resolvingRuntimePackage = false;

function isRuntimePackageSpecifier(specifier) {
  return (
    typeof specifier === "string" &&
    specifier.length > 0 &&
    !specifier.startsWith(".") &&
    !specifier.startsWith("/") &&
    !specifier.startsWith("#") &&
    !specifier.startsWith("node:") &&
    !isBuiltin(specifier) &&
    !specifier.includes(":")
  );
}

function isInsideRuntimeNodeModules(parentURL) {
  if (typeof parentURL !== "string" || !parentURL.startsWith("file:")) {
    return false;
  }
  try {
    const parentPath = realpathSync(fileURLToPath(parentURL));
    return (
      parentPath === runtimeNodeModulesCanonicalPath ||
      parentPath.startsWith(runtimeNodeModulesCanonicalPrefix)
    );
  } catch {
    return false;
  }
}

function assertRuntimePackageResolution(specifier, result) {
  const resolvedURL = result?.url;
  if (typeof resolvedURL !== "string") {
    throw new Error(
      `Eidos runtime package ${specifier} did not resolve to a URL`,
    );
  }
  if (resolvedURL.startsWith("node:")) {
    return result;
  }
  if (!resolvedURL.startsWith("file:")) {
    throw new Error(
      `Eidos runtime package ${specifier} resolved outside RUNTIME_NODE_MODULES`,
    );
  }

  let canonicalPath;
  try {
    canonicalPath = realpathSync(fileURLToPath(resolvedURL));
  } catch (error) {
    throw new Error(
      `Eidos runtime package ${specifier} resolved to an unavailable file`,
      { cause: error },
    );
  }
  if (
    canonicalPath !== runtimeNodeModulesCanonicalPath &&
    !canonicalPath.startsWith(runtimeNodeModulesCanonicalPrefix)
  ) {
    throw new Error(
      `Eidos runtime package ${specifier} resolved outside RUNTIME_NODE_MODULES`,
    );
  }
  return result;
}

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (!isRuntimePackageSpecifier(specifier)) {
      return nextResolve(specifier, context);
    }
    if (resolvingRuntimePackage) {
      return nextResolve(specifier, context);
    }
    if (isInsideRuntimeNodeModules(context.parentURL)) {
      return assertRuntimePackageResolution(
        specifier,
        nextResolve(specifier, context),
      );
    }
    if (context.conditions?.includes("require")) {
      // Node's synchronous CJS default resolver closes over the original
      // parent module and ignores a replacement context.parentURL.  Resolve
      // the package with Node's native require resolver rooted at the anchor,
      // then delegate the resulting path through the hook chain.
      resolvingRuntimePackage = true;
      let runtimePath;
      try {
        runtimePath = runtimeRequire.resolve(specifier);
      } finally {
        resolvingRuntimePackage = false;
      }
      return assertRuntimePackageResolution(
        specifier,
        nextResolve(runtimePath, context),
      );
    }
    return assertRuntimePackageResolution(
      specifier,
      nextResolve(specifier, {
        ...context,
        parentURL: runtimeRootAnchor,
      }),
    );
  },
});

import { signAsync as defaultSignAsync } from "@electron/osx-sign";
import path from "node:path";

import { refreshManifestHashesSync } from "./generate-runtime-manifest.mjs";


function isOuterApplication(appPath, filePath) {
  return path.resolve(appPath) === path.resolve(filePath);
}


export function createMacSign({
  signAsync = defaultSignAsync,
  refreshManifest = refreshManifestHashesSync,
} = {}) {
  return async function macSign(options, packager) {
    const originalOptionsForFile = options?.optionsForFile;
    let manifestRefreshed = false;
    const wrappedOptions = {
      ...options,
      optionsForFile(filePath) {
        if (!manifestRefreshed && isOuterApplication(options.app, filePath)) {
          refreshManifest({
            bundleRoot: path.join(options.app, "Contents", "Resources", "runtime"),
          });
          manifestRefreshed = true;
        }
        if (typeof originalOptionsForFile === "function") {
          return originalOptionsForFile(filePath);
        }
        return undefined;
      },
    };
    return signAsync(wrappedOptions, packager);
  };
}


export default createMacSign();

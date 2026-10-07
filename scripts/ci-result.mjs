import { pathToFileURL } from "node:url";

export function failedCiChecks(checks) {
  const failed = new Set();
  for (const [name, check] of Object.entries(checks))
    if (!["success", "skipped"].includes(check?.result)) failed.add(name);
  for (const name of ["changes", "quality", "security"])
    if (checks[name]?.result !== "success") failed.add(name);
  for (const name of ["runtime", "desktop", "native"]) {
    const selected = checks.changes?.outputs?.[name];
    const result = checks[name]?.result;
    if (!["true", "false"].includes(selected)
      || !["success", "skipped"].includes(result)
      || (selected === "true" && result !== "success")) failed.add(name);
  }
  return [...failed].sort();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const failed = failedCiChecks(JSON.parse(process.env.RESULTS ?? "{}"));
  if (failed.length) {
    process.stderr.write(`Failed or missing required checks: ${failed.join(", ")}\n`);
    process.exitCode = 1;
  }
}

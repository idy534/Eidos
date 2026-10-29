import { useCallback, useEffect, useState } from "react";
import type { Project } from "../../contracts.js";
import type { MemoryScopeChoice } from "./useMemoryDocuments.js";

const STORAGE_KEY = "eidos.memory.location.v1";
type Selection = { projectId: string | null; scope: MemoryScopeChoice };

function readSelection(): Selection {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (value && typeof value === "object" && "projectId" in value && "scope" in value
        && typeof value.projectId === "string" && value.projectId
        && (value.scope === "current" || value.scope === "global")) {
      return { projectId: value.projectId, scope: value.scope };
    }
  } catch { /* The page can work without saved preferences. */ }
  return { projectId: null, scope: "global" };
}

export function useMemoryLocation() {
  const [selection, setSelection] = useState(readSelection);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    void window.eidosRuntime.listProjects().then((value) => {
      if (active) { setProjects(value.items); setLoaded(true); setError(""); }
    }).catch(() => { if (active) setError("项目列表无法读取，请重试。"); });
    return () => { active = false; };
  }, [version]);

  useEffect(() => {
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refresh]);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(selection)); }
    catch { /* Saving a browsing preference is optional. */ }
  }, [selection]);

  const valid = selection.projectId === null
    || (loaded && projects.some((project) => project.id === selection.projectId));
  return {
    ...selection, projects, loaded, error, valid, refresh,
    selectProject: (projectId: string | null) => setSelection({ projectId, scope: projectId ? "current" : "global" }),
    selectScope: (scope: MemoryScopeChoice) => setSelection((value) => ({ ...value, scope })),
  };
}

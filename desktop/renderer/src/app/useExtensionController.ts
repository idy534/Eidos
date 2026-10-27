import { useCallback, useRef, useState } from "react";
import type {
  McpCreateInput,
  McpServerRecord,
  McpUpdateInput,
  PluginRecord,
  SkillMetadata,
  SkillRemoval,
} from "../contracts.js";
import type { SettingsPendingAction } from "../components/settings/settings-types.js";
import { userFacingError } from "../session-state.js";

export interface ExtensionControllerState {
  plugins: PluginRecord[];
  skills: SkillMetadata[];
  pendingSkillIds: ReadonlySet<string>;
  mcpServers: McpServerRecord[];
  loading: boolean;
  error: string | undefined;
  pendingAction: SettingsPendingAction;
}

export interface ExtensionControllerActions {
  load: () => Promise<void>;
  importPlugin: () => Promise<PluginRecord | null>;
  installBuiltinPlugin: (pluginId: string) => Promise<void>;
  setPluginEnabled: (pluginId: string, enabled: boolean) => Promise<void>;
  setPluginHookTrust: (pluginId: string, contentHash: string, trusted: boolean) => Promise<void>;
  removePlugin: (pluginId: string) => Promise<void>;
  setMcpEnabled: (pluginId: string, serverId: string, enabled: boolean) => Promise<void>;
  createMcpServer: (input: McpCreateInput) => Promise<void>;
  updateMcpServer: (input: McpUpdateInput) => Promise<void>;
  removeMcpServer: (serverId: string) => Promise<void>;
  setSkillEnabled: (qualifiedId: string, enabled: boolean) => Promise<void>;
  removeSkill: (qualifiedId: string) => Promise<SkillRemoval>;
  clearError: () => void;
}

export function useExtensionController(): [ExtensionControllerState, ExtensionControllerActions] {
  const [plugins, setPlugins] = useState<PluginRecord[]>([]);
  const [skills, setSkills] = useState<SkillMetadata[]>([]);
  const [pendingSkillIds, setPendingSkillIds] = useState<ReadonlySet<string>>(new Set());
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const [pendingAction, setPendingAction] = useState<SettingsPendingAction>(undefined);

  const skillMutationRevision = useRef(0);
  const pendingSkillIdsRef = useRef(new Set<string>());

  const beginSkillMutation = useCallback((qualifiedId: string): boolean => {
    if (pendingSkillIdsRef.current.has(qualifiedId)) return false;
    const next = new Set(pendingSkillIdsRef.current);
    next.add(qualifiedId);
    pendingSkillIdsRef.current = next;
    setPendingSkillIds(next);
    return true;
  }, []);

  const finishSkillMutation = useCallback((qualifiedId: string): void => {
    const next = new Set(pendingSkillIdsRef.current);
    next.delete(qualifiedId);
    pendingSkillIdsRef.current = next;
    setPendingSkillIds(next);
  }, []);

  const fetchAndApplySnapshot = async (): Promise<void> => {
    const revision = skillMutationRevision.current;
    let snap = await window.eidosRuntime.readExtensions();
    const events = await window.eidosRuntime.readExtensionEvents(snap.throughEventId);
    if (events.items.length > 0) {
      snap = await window.eidosRuntime.readExtensions();
    }
    setPlugins(snap.plugins);
    if (revision === skillMutationRevision.current) setSkills(snap.skills);
    setMcpServers(snap.servers);
  };

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(undefined);
    try {
      await fetchAndApplySnapshot();
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  const importPlugin = useCallback(async (): Promise<PluginRecord | null> => {
    setPendingAction({ type: "import_plugin" });
    setError(undefined);
    try {
      const imported = await window.eidosRuntime.importPlugin();
      if (imported) {
        await fetchAndApplySnapshot();
      }
      return imported;
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
  }, []);

  const installBuiltinPlugin = useCallback(async (pluginId: string): Promise<void> => {
    await window.eidosRuntime.builtinPlugins({ action: "install", pluginId });
    await fetchAndApplySnapshot();
  }, []);

  const setPluginEnabled = useCallback(async (pluginId: string, enabled: boolean): Promise<void> => {
    setPendingAction({ type: "toggle_plugin", pluginId });
    setError(undefined);
    try {
      await window.eidosRuntime.setPluginEnabled(pluginId, enabled);
      await fetchAndApplySnapshot();
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
  }, []);

  const setPluginHookTrust = useCallback(async (pluginId: string, contentHash: string, trusted: boolean): Promise<void> => {
    setPendingAction({ type: "toggle_plugin", pluginId });
    setError(undefined);
    try {
      await window.eidosRuntime.setPluginHookTrust(pluginId, contentHash, trusted);
      await fetchAndApplySnapshot();
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
  }, []);

  const removePlugin = useCallback(async (pluginId: string): Promise<void> => {
    setPendingAction({ type: "remove_plugin", pluginId });
    setError(undefined);
    try {
      await window.eidosRuntime.removePlugin(pluginId);
      await fetchAndApplySnapshot();
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
  }, []);

  const setMcpEnabled = useCallback(async (pluginId: string, serverId: string, enabled: boolean): Promise<void> => {
    setPendingAction({ type: "toggle_mcp", pluginId, serverId });
    setError(undefined);
    try {
      await window.eidosRuntime.setMcpEnabled(pluginId, serverId, enabled);
      await fetchAndApplySnapshot();
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
  }, []);

  const createMcpServer = useCallback(async (input: McpCreateInput): Promise<void> => {
    setPendingAction({ type: "create_mcp" });
    setError(undefined);
    try {
      await window.eidosRuntime.createMcpServer(input);
      await fetchAndApplySnapshot();
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
  }, []);

  const updateMcpServer = useCallback(async (input: McpUpdateInput): Promise<void> => {
    setPendingAction({ type: "update_mcp", serverId: input.serverId });
    setError(undefined);
    try {
      await window.eidosRuntime.updateMcpServer(input);
      await fetchAndApplySnapshot();
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
  }, []);

  const removeMcpServer = useCallback(async (serverId: string): Promise<void> => {
    setPendingAction({ type: "remove_mcp", serverId });
    setError(undefined);
    try {
      await window.eidosRuntime.removeMcpServer(serverId);
      await fetchAndApplySnapshot();
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
  }, []);

  const setSkillEnabled = useCallback(async (qualifiedId: string, enabled: boolean): Promise<void> => {
    if (!beginSkillMutation(qualifiedId)) throw new Error("该技能正在更新。");
    const previousEnabled = skills.find((item) => item.qualifiedId === qualifiedId)?.enabled;
    skillMutationRevision.current += 1;
    setError(undefined);
    setSkills((previous) => previous.map((item) => (item.qualifiedId === qualifiedId ? { ...item, enabled } : item)));
    try {
      const skill = await window.eidosRuntime.setSkillEnabled(qualifiedId, enabled);
      setSkills((previous) => previous.map((item) => (item.qualifiedId === qualifiedId ? skill : item)));
    } catch (cause) {
      if (previousEnabled !== undefined) {
        setSkills((previous) => previous.map((item) => (
          item.qualifiedId === qualifiedId ? { ...item, enabled: previousEnabled } : item
        )));
      }
      const msg = userFacingError(cause);
      setError(msg);
      throw cause;
    } finally {
      skillMutationRevision.current += 1;
      finishSkillMutation(qualifiedId);
    }
  }, [beginSkillMutation, finishSkillMutation, skills]);

  const removeSkill = useCallback(async (qualifiedId: string): Promise<SkillRemoval> => {
    if (!beginSkillMutation(qualifiedId)) throw new Error("该技能正在更新。");
    skillMutationRevision.current += 1;
    setPendingAction({ type: "remove_skill", qualifiedId });
    setError(undefined);
    try {
      const result = await window.eidosRuntime.removeSkill(qualifiedId);
      setSkills((previous) => previous.filter((item) => item.qualifiedId !== qualifiedId));
      return result;
    } finally {
      skillMutationRevision.current += 1;
      setPendingAction(undefined);
      finishSkillMutation(qualifiedId);
    }
  }, [beginSkillMutation, finishSkillMutation]);

  const clearError = useCallback((): void => {
    setError(undefined);
  }, []);

  const state: ExtensionControllerState = {
    plugins,
    skills,
    pendingSkillIds,
    mcpServers,
    loading,
    error,
    pendingAction,
  };

  const actions: ExtensionControllerActions = {
    load,
    importPlugin,
    installBuiltinPlugin,
    setPluginEnabled,
    setPluginHookTrust,
    removePlugin,
    setMcpEnabled,
    createMcpServer,
    updateMcpServer,
    removeMcpServer,
    clearError,
    setSkillEnabled,
    removeSkill,
  };

  return [state, actions];
}

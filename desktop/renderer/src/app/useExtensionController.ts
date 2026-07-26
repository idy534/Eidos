import { useCallback, useState } from "react";
import type { McpServerRecord, PluginRecord, SkillMetadata } from "../contracts.js";
import type { SettingsPendingAction } from "../components/settings/settings-types.js";
import { userFacingError } from "../session-state.js";

export interface ExtensionControllerState {
  plugins: PluginRecord[];
  skills: SkillMetadata[];
  mcpServers: McpServerRecord[];
  loading: boolean;
  error: string | undefined;
  pendingAction: SettingsPendingAction;
}

export interface ExtensionControllerActions {
  load: () => Promise<void>;
  importPlugin: () => Promise<void>;
  setPluginEnabled: (pluginId: string, enabled: boolean) => Promise<void>;
  removePlugin: (pluginId: string) => Promise<void>;
  setMcpEnabled: (pluginId: string, serverId: string, enabled: boolean) => Promise<void>;
  clearError: () => void;
}

export function useExtensionController(): [ExtensionControllerState, ExtensionControllerActions] {
  const [plugins, setPlugins] = useState<PluginRecord[]>([]);
  const [skills, setSkills] = useState<SkillMetadata[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const [pendingAction, setPendingAction] = useState<SettingsPendingAction>(undefined);

  const fetchAndApplySnapshot = async (): Promise<void> => {
    let snap = await window.eidosRuntime.readExtensions();
    const events = await window.eidosRuntime.readExtensionEvents(snap.throughEventId);
    if (events.items.length > 0) {
      snap = await window.eidosRuntime.readExtensions();
    }
    setPlugins(snap.plugins);
    setSkills(snap.skills);
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

  const importPlugin = useCallback(async (): Promise<void> => {
    setPendingAction({ type: "import_plugin" });
    setError(undefined);
    try {
      const imported = await window.eidosRuntime.importPlugin();
      if (imported) {
        await fetchAndApplySnapshot();
      }
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw new Error(msg);
    } finally {
      setPendingAction(undefined);
    }
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

  const clearError = useCallback((): void => {
    setError(undefined);
  }, []);

  const state: ExtensionControllerState = {
    plugins,
    skills,
    mcpServers,
    loading,
    error,
    pendingAction,
  };

  const actions: ExtensionControllerActions = {
    load,
    importPlugin,
    setPluginEnabled,
    removePlugin,
    setMcpEnabled,
    clearError,
  };

  return [state, actions];
}

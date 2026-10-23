import { useEffect, useState } from "react";
import type { BuiltinPlugin } from "../../../../shared/mcp-connections.js";
import { Button } from "../Button.js";
import { FieldError } from "../FieldError.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";

export function BuiltinPlugins({ onInstall, onShowToast, catalogRevision }: {
  onInstall: (pluginId: string) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
  catalogRevision?: string | undefined;
}) {
  const [plugins, setPlugins] = useState<BuiltinPlugin[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  useEffect(() => {
    let active = true;
    window.eidosRuntime.builtinPlugins({ action: "list" }).then(result => {
      if (active) setPlugins(result.plugins);
    }).catch(() => { if (active) setError("内置插件暂时无法加载，请稍后重试。"); });
    return () => { active = false; };
  }, [catalogRevision]);
  async function install(plugin: BuiltinPlugin) {
    setBusy(true); setError(undefined);
    try {
      await onInstall(plugin.id);
      setPlugins((await window.eidosRuntime.builtinPlugins({ action: "list" })).plugins);
      onShowToast(`${plugin.name} 已${plugin.updateAvailable ? "更新" : "安装"}，请打开插件资源连接账号。`, "success");
    } catch { setError(`安装 ${plugin.name} 插件失败，请稍后重试。`); }
    finally { setBusy(false); }
  }
  return <SettingSection title="内置插件">
    {error && <FieldError error={error} />}
    {plugins.map(plugin => <SettingRow key={plugin.id} title={plugin.name} description={plugin.description}
      meta="随 Eidos 提供"
      action={<Button variant="secondary" size="small" disabled={busy || (plugin.installed && !plugin.updateAvailable)} loading={busy}
        onClick={() => void install(plugin)}>{plugin.updateAvailable ? "更新" : plugin.installed ? "已安装" : "安装"}</Button>} />)}
  </SettingSection>;
}

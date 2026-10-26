import { useEffect, useRef, useState } from "react";
import type { BuiltinPlugin } from "../../../../shared/mcp-connections.js";
import { Button } from "../Button.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { githubIcon } from "./SettingsIcons.js";

export function BuiltinPlugins({ onInstall, onShowToast, catalogRevision }: {
  onInstall: (pluginId: string) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
  catalogRevision?: string | undefined;
}) {
  const [plugins, setPlugins] = useState<BuiltinPlugin[]>([]);
  const [busy, setBusy] = useState(false);
  const onShowToastRef = useRef(onShowToast);
  onShowToastRef.current = onShowToast;

  useEffect(() => {
    let active = true;
    window.eidosRuntime.builtinPlugins({ action: "list" }).then(result => {
      if (active) setPlugins(result.plugins);
    }).catch(() => {
      if (active) onShowToastRef.current("内置插件暂时无法加载，请稍后重试。", "error");
    });
    return () => { active = false; };
  }, [catalogRevision]);

  async function install(plugin: BuiltinPlugin) {
    setBusy(true);
    try {
      await onInstall(plugin.id);
      setPlugins((await window.eidosRuntime.builtinPlugins({ action: "list" })).plugins);
      onShowToast(`${plugin.name} 已${plugin.updateAvailable ? "更新" : "安装"}，请打开插件资源连接账号。`, "success");
    } catch {
      onShowToast(`安装 ${plugin.name} 插件失败，请稍后重试。`, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingSection title="内置插件">
      {plugins.map((plugin) => {
        const isGitHub = plugin.id === "eidos-github" || plugin.name.toLowerCase().includes("github");
        return (
          <SettingRow
            key={plugin.id}
            title={
              <div className="plugin-row-header">
                {isGitHub && <img className="plugin-icon" src={githubIcon} alt="GitHub" />}
                <span className="plugin-name">{plugin.name}</span>
              </div>
            }
            description={plugin.description}
            action={
              <Button
                variant="secondary"
                size="small"
                disabled={busy || (plugin.installed && !plugin.updateAvailable)}
                loading={busy}
                onClick={() => void install(plugin)}
              >
                {plugin.updateAvailable ? "更新" : plugin.installed ? "已安装" : "安装"}
              </Button>
            }
          />
        );
      })}
    </SettingSection>
  );
}

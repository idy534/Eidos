import { useEffect, useRef, useState } from "react";
import type { BuiltinPlugin } from "../../../../shared/mcp-connections.js";
import { Button } from "../Button.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { CloseIcon, FinderIcon } from "../MenuIcons.js";
import { PluginsSettingsIcon, githubIcon } from "./SettingsIcons.js";
import "./PluginMarketplaceDialog.css";

export interface PluginMarketplaceDialogProps {
  open: boolean;
  onClose: () => void;
  onInstall?: ((pluginId: string) => Promise<void>) | undefined;
  onImportLocal?: (() => void) | undefined;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
  catalogRevision?: string | undefined;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
}

export function PluginMarketplaceDialog({
  open,
  onClose,
  onInstall,
  onImportLocal,
  onShowToast,
  catalogRevision,
  getFallbackFocus,
}: PluginMarketplaceDialogProps) {
  const [plugins, setPlugins] = useState<BuiltinPlugin[]>([]);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<"all" | "builtin">("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const onShowToastRef = useRef(onShowToast);
  onShowToastRef.current = onShowToast;

  useDialogFocusLifecycle({ open, initialFocusRef: closeRef, getFallbackFocus });

  useEffect(() => {
    if (!open) return;
    let active = true;
    window.eidosRuntime?.builtinPlugins({ action: "list" }).then(result => {
      if (active) setPlugins(result.plugins);
    }).catch(() => {
      if (active) onShowToastRef.current("插件市场暂时无法加载，请稍后重试。", "error");
    });
    return () => { active = false; };
  }, [open, catalogRevision]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const targets = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), summary, [tabindex='0']",
        ) ?? [],
      ).filter(
        (element) =>
          (element.checkVisibility?.({ visibilityProperty: true }) ?? true) &&
          (!element.closest("details:not([open])") || element.tagName === "SUMMARY"),
      );
      const first = targets[0];
      const last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  async function handleInstall(plugin: BuiltinPlugin) {
    if (!onInstall) return;
    setBusyId(plugin.id);
    try {
      await onInstall(plugin.id);
      const res = await window.eidosRuntime?.builtinPlugins({ action: "list" });
      if (res?.plugins) setPlugins(res.plugins);
      onShowToast(`${plugin.name} 已${plugin.updateAvailable ? "更新" : "安装"}，请在已安装列表中查看并配置。`, "success");
    } catch {
      onShowToast(`安装 ${plugin.name} 插件失败，请稍后重试。`, "error");
    } finally {
      setBusyId(null);
    }
  }

  if (!open) return null;

  const filteredPlugins = plugins.filter((p) => {
    if (category === "builtin" && !p.id.startsWith("eidos-")) return false;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      return p.name.toLowerCase().includes(q) || (p.description && p.description.toLowerCase().includes(q));
    }
    return true;
  });

  return (
    <div className="modal-backdrop plugin-marketplace-backdrop" onClick={onClose}>
      <div
        ref={dialogRef}
        className="modal-dialog plugin-marketplace-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="plugin-marketplace-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="plugin-marketplace-header">
          <div className="plugin-marketplace-header-info">
            <div className="plugin-marketplace-title-row">
              <PluginsSettingsIcon width={22} height={22} className="plugin-marketplace-header-icon" />
              <h2 id="plugin-marketplace-title">插件市场</h2>
            </div>
            <p className="plugin-marketplace-subtitle">
              浏览随应用提供的官方内置插件。安装后可在“已安装的 Plugins”中查看、启用与配置。
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-button modal-close-button plugin-marketplace-close"
            aria-label="关闭插件市场"
            onClick={onClose}
          >
            <CloseIcon width={16} height={16} />
          </button>
        </header>

        <div className="plugin-marketplace-toolbar">
          <div className="plugin-marketplace-tabs" role="tablist" aria-label="插件分类">
            <button
              type="button"
              role="tab"
              aria-selected={category === "all"}
              className={`plugin-marketplace-tab ${category === "all" ? "is-active" : ""}`}
              onClick={() => setCategory("all")}
            >
              全部 ({plugins.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={category === "builtin"}
              className={`plugin-marketplace-tab ${category === "builtin" ? "is-active" : ""}`}
              onClick={() => setCategory("builtin")}
            >
              官方内置
            </button>
          </div>
          <div className="plugin-marketplace-search">
            <input
              type="search"
              placeholder="搜索插件..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="搜索插件市场"
            />
          </div>
        </div>

        <div className="plugin-marketplace-body" tabIndex={0} aria-label="插件市场列表">
          {filteredPlugins.length === 0 ? (
            <div className="plugin-marketplace-empty">
              <p>未找到符合条件的插件</p>
            </div>
          ) : (
            <div className="plugin-marketplace-grid">
              {filteredPlugins.map((plugin) => {
                const isBusy = busyId === plugin.id;
                const isGitHub = plugin.id === "eidos-github" || plugin.name.toLowerCase().includes("github");
                return (
                  <div key={plugin.id} className="plugin-marketplace-card">
                    <div className="plugin-marketplace-card-main">
                      <div className="plugin-marketplace-card-icon-wrap">
                        {isGitHub ? (
                          <img className="plugin-marketplace-card-icon" src={githubIcon} alt={plugin.name} />
                        ) : (
                          <span className="plugin-marketplace-card-avatar">
                            {Array.from(plugin.name.trim())[0]?.toLocaleUpperCase() ?? "P"}
                          </span>
                        )}
                      </div>
                      <div className="plugin-marketplace-card-title-group">
                        <div className="plugin-marketplace-card-name-row">
                          <strong className="plugin-marketplace-card-name">{plugin.name}</strong>
                          <span className="plugin-marketplace-badge">官方内置</span>
                          <span className="plugin-marketplace-version">v{plugin.version}</span>
                        </div>
                        <p className="plugin-marketplace-card-desc">{plugin.description || "未提供插件描述"}</p>
                      </div>
                    </div>

                    <div className="plugin-marketplace-card-actions">
                      <Button
                        variant={plugin.installed && !plugin.updateAvailable ? "secondary" : "primary"}
                        size="small"
                        disabled={isBusy || (plugin.installed && !plugin.updateAvailable)}
                        loading={isBusy}
                        onClick={() => void handleInstall(plugin)}
                      >
                        {plugin.updateAvailable ? "更新" : plugin.installed ? "已安装" : "安装"}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <footer className="plugin-marketplace-dialog-footer">
          <div className="plugin-marketplace-footer-tip">
            <span>有本地插件包？</span>
            {onImportLocal && (
              <button
                type="button"
                className="plugin-marketplace-link-btn"
                onClick={() => {
                  onClose();
                  onImportLocal();
                }}
              >
                <FinderIcon width={13} height={13} />
                <span>导入本地 Plugin...</span>
              </button>
            )}
          </div>
          <Button variant="secondary" size="medium" onClick={onClose}>
            关闭
          </Button>
        </footer>
      </div>
    </div>
  );
}

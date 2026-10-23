import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentCatalogItem, AgentDefinition, AgentDefinitionDocument } from "../../../../shared/collaboration.generated.js";
import type { ModelOption } from "../../contracts.js";
import { formatAgentModelDisplay } from "../AgentPanel.js";
import { AgentEditorDialog, AgentRoleIcon, agentRoleLabels } from "./AgentEditorDialog.js";
import { Button } from "../Button.js";
import { DropdownMenu } from "../DropdownMenu.js";
import { FieldError } from "../FieldError.js";
import { getProviderName, ProviderLogo } from "../ProviderLogo.js";
import { Toggle } from "../Toggle.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { EmptyState } from "../EmptyState.js";
import { SearchInput } from "../SearchInput.js";

import { userFacingError } from "../../session-state.js";
import { CopyIcon, TrashIcon } from "../MenuIcons.js";

type Editor = { document?: AgentDefinitionDocument; initialDefinition?: AgentDefinition };

const builtinDescriptions: Record<string, string> = {
  default: "继承父任务的模型与执行上下文，承担通用型任务调度与执行。",
  explorer: "专注于只读探索、源码搜索与证据收集",
  worker: "负责范围明确的实现、修复与验证。",
};

export function AgentSettings({ ready, onCountChange, onShowToast }: {
  ready: boolean;
  onCountChange?(count: number): void;
  onShowToast(message: string, type: "success" | "info" | "error"): void;
}) {
  const [agents, setAgents] = useState<AgentCatalogItem[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<Editor>();
  const [removing, setRemoving] = useState<AgentDefinitionDocument>();
  const searchRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    if (ready && typeof window.eidosRuntime?.listModels === "function") {
      void window.eidosRuntime.listModels().then(
        (result) => {
          if (active && result.models) setModels(result.models);
        },
        () => {},
      );
    }
    return () => { active = false; };
  }, [ready]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await window.eidosRuntime.listAgentTypes();
      if (!mounted.current) return;
      setAgents(result.agents ?? []);
      onCountChange?.(result.agents?.length ?? 0);
      setError(result.error ?? undefined);
    } catch (cause) {
      if (mounted.current) setError(userFacingError(cause));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    if (ready) void load();
    else setLoading(false);
  }, [ready, load]);

  async function action(run: () => Promise<void>) {
    setError(undefined);
    setBusy(true);
    try {
      await run();
    } catch (cause) {
      if (mounted.current) setError(userFacingError(cause));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  function read(agent: AgentCatalogItem, next: (document: AgentDefinitionDocument) => void) {
    void action(async () => {
      const document = await window.eidosRuntime.readAgentDefinition(agent.source);
      if (mounted.current) next(document);
    });
  }

  const needle = query.trim().toLocaleLowerCase();
  const visible = agents.filter((agent) =>
    `${agent.name} ${agent.description}`.toLocaleLowerCase().includes(needle),
  );

  return (
    <div className="settings-panel agent-settings">
      <div inert={Boolean(editor || removing)}>
        <header className="settings-panel-header header-title-with-stats">
          <div>
            <h1>智能体</h1>
            <p className="settings-panel-subtitle">定义子任务的模型与工作方式。</p>
          </div>
          <Button variant="primary" disabled={!ready || busy} onClick={() => setEditor({})}>
            + 创建智能体
          </Button>
        </header>

        <div className="agent-settings__toolbar">
          <SearchInput
            className="agent-settings__search-box"
            ariaLabel="搜索智能体"
            placeholder="搜索智能体名称或用途…"
            value={query}
            onChange={setQuery}
          />
          <Button
            variant="secondary"
            size="small"
            disabled={!ready || loading || busy}
            onClick={() => void load()}
          >
            刷新
          </Button>
        </div>

        {!ready && <FieldError>Runtime 尚未就绪。</FieldError>}
        {error && <FieldError>{error}</FieldError>}
        {loading && <p role="status" className="agent-settings__loading">正在读取智能体…</p>}

        {/* 自定义智能体列表 */}
        {visible.length > 0 && (
          <div className="setting-section agent-section-card">
            <div className="agent-settings__list" aria-label="自定义智能体">
              {visible.map((agent) => (
                <article className="agent-row" key={agent.source}>
                  <div className={`agent-role-avatar agent-role-avatar--${agent.baseRole ?? "default"}`} aria-hidden="true">
                    <AgentRoleIcon role={agent.baseRole ?? "default"} size={18} />
                  </div>

                  <div className="agent-row-content">
                    <div className="agent-row-header-line">
                      <h2 className="agent-name">{agent.name}</h2>
                      <span className={`agent-role-tag agent-role-tag--${agent.baseRole ?? "default"}`}>
                        {agent.baseRole ?? "default"}
                      </span>
                      <span
                        className={`status-badge status-badge--${
                          agent.enabled && agent.available
                            ? "success"
                            : agent.enabled && !agent.available
                            ? "danger"
                            : "neutral"
                        }`}
                      >
                        <span className="status-badge-dot" />
                        {agent.enabled && agent.available
                          ? "已启用"
                          : !agent.enabled
                          ? "已停用"
                          : agent.reason ?? "已停用"}
                      </span>
                      {agent.modelId && (() => {
                        const modelDisplay = formatAgentModelDisplay(agent.providerId, agent.modelId, models);
                        return (
                          <span className="agent-meta-badge" title={modelDisplay?.fullDisplay}>
                            {agent.providerId ? (
                              <ProviderLogo provider={agent.providerId} className="agent-provider-logo" />
                            ) : null}
                            {modelDisplay
                              ? modelDisplay.fullDisplay
                              : `${agent.providerId ? `${getProviderName(agent.providerId)} / ` : ""}${agent.modelId}`}
                          </span>
                        );
                      })()}
                      {agent.definitionVersion && (
                        <span className="agent-meta-source" title={agent.source}>
                          ~/{agent.source.split("/").slice(-2).join("/")}
                        </span>
                      )}
                    </div>

                    <p className="agent-desc">{agent.description || "配置尚未提供有效的用途说明。"}</p>
                  </div>

                  <div className="agent-row-actions">
                    <Toggle
                      label={`启用 ${agent.name}`}
                      checked={agent.enabled ?? false}
                      disabled={!ready || busy || !agent.definitionVersion}
                      onChange={(enabled) => void action(async () => {
                        const current = await window.eidosRuntime.readAgentDefinition(agent.source);
                        await window.eidosRuntime.saveAgentDefinition({
                          source: current.source,
                          expectedVersion: current.version,
                          definition: { ...current.definition, enabled },
                        });
                        await load();
                      })}
                    />
                    <Button
                      variant="secondary"
                      size="small"
                      disabled={!ready || busy || !agent.definitionVersion}
                      onClick={() => read(agent, (document) => setEditor({ document }))}
                    >
                      编辑
                    </Button>
                    <AgentActionMenu
                      agent={agent}
                      busy={busy}
                      onCopy={() => read(agent, (document) => setEditor({
                        initialDefinition: {
                          ...document.definition,
                          enabled: true,
                          name: `${document.definition.name.slice(0, 59)}-copy`,
                        },
                      }))}
                      onDelete={() => read(agent, setRemoving)}
                    />
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}

        {!loading && ready && !visible.length && (
          <EmptyState
            title={needle ? "没有匹配的智能体" : "还没有自定义智能体"}
            description={
              needle
                ? "请尝试其他名称或关键词。"
                : "你可以创建智能体，也可以继续通过 TOML 文件手动配置。"
            }
          />
        )}

        {/* 内置智能体模板 */}
        <details className="agent-builtin-card">
          <summary className="agent-builtin-summary">
            <div className="agent-builtin-summary__left">
              <svg
                className="agent-builtin-chevron"
                viewBox="0 0 16 16"
                width="12"
                height="12"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="m6 4 4 4-4 4" />
              </svg>
              <span className="agent-builtin-title">内置智能体 · 3</span>
              <span className="agent-builtin-tag">系统预设</span>
            </div>
          </summary>
          <div className="agent-builtin-list">
            {Object.entries(agentRoleLabels).map(([role, label]) => (
              <div className="agent-builtin-row" key={role}>
                <div className={`agent-role-avatar agent-role-avatar--${role}`} aria-hidden="true">
                  <AgentRoleIcon role={role} size={18} />
                </div>
                <div className="agent-builtin-copy">
                  <div className="agent-builtin-title-row">
                    <span className="agent-builtin-name">{label}</span>
                    <span className={`agent-role-tag agent-role-tag--${role}`}>{role}</span>
                  </div>
                  <p className="agent-builtin-desc">{builtinDescriptions[role] ?? label}</p>
                </div>
                <Button
                  variant="secondary"
                  size="small"
                  disabled={!ready || busy}
                  onClick={() => setEditor({
                    initialDefinition: {
                      schemaVersion: 1,
                      name: `${role}-custom`,
                      description: label,
                      baseRole: role as NonNullable<AgentDefinition["baseRole"]>,
                      enabled: true,
                      instructions: "你需要完成父任务分配的工作，并报告结果、证据和未完成事项。",
                    },
                  })}
                >
                  复制为自定义智能体
                </Button>
              </div>
            ))}
          </div>
        </details>
      </div>

      {editor && (
        <AgentEditorDialog
          {...editor}
          onClose={() => {
            setEditor(undefined);
            requestAnimationFrame(() => searchRef.current?.focus());
          }}
          onSaved={() => {
            setEditor(undefined);
            requestAnimationFrame(() => searchRef.current?.focus());
            onShowToast("智能体已保存，将用于后续新任务。", "success");
            void load();
          }}
        />
      )}

      <ConfirmDialog
        open={Boolean(removing)}
        title={`删除智能体“${removing?.definition.name ?? ""}”？`}
        description="删除后，新任务将无法选择该智能体。已经开始的任务会继续使用保存的定义。"
        confirmLabel="删除"
        isDestructive
        busy={busy}
        error={error}
        getFallbackFocus={() => searchRef.current}
        onCancel={() => {
          setRemoving(undefined);
          setError(undefined);
        }}
        onConfirm={() => {
          if (removing) {
            void action(async () => {
              await window.eidosRuntime.removeAgentDefinition({
                source: removing.source,
                expectedVersion: removing.version,
              });
              setRemoving(undefined);
              await load();
              onShowToast("智能体已删除。", "success");
            });
          }
        }}
      />
    </div>
  );
}

interface AgentActionMenuProps {
  agent: AgentCatalogItem;
  busy: boolean;
  onCopy: () => void;
  onDelete: () => void;
}

function AgentActionMenu({ agent, busy, onCopy, onDelete }: AgentActionMenuProps) {
  return (
    <DropdownMenu
      trigger="⋯"
      className="agent-settings__more"
      triggerClassName="agent-more-trigger"
      triggerAriaLabel={`${agent.name} 的更多操作`}
      label={`${agent.name} 选项`}
      items={[
        {
          key: "copy",
          label: "复制为新智能体",
          icon: <CopyIcon />,
          disabled: busy || !agent.definitionVersion,
          onClick: onCopy,
        },
        {
          key: "delete",
          label: "删除",
          icon: <TrashIcon />,
          danger: true,
          disabled: busy || !agent.definitionVersion,
          onClick: onDelete,
        },
      ]}
    />
  );
}

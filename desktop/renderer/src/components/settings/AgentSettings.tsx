import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentCatalogItem, AgentDefinition, AgentDefinitionDocument } from "../../../../shared/collaboration.generated.js";
import { AgentEditorDialog, agentRoleLabels } from "./AgentEditorDialog.js";
import { AgentSourceDialog } from "./AgentSourceDialog.js";
import { Button } from "../Button.js";
import { getProviderName } from "../ProviderLogo.js";
import { Toggle } from "./Toggle.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { EmptySettingsState } from "./EmptySettingsState.js";
import { userFacingError } from "../../session-state.js";

type Editor = { document?: AgentDefinitionDocument; initialDefinition?: AgentDefinition };

export function AgentSettings({ ready, onCountChange, onShowToast }: {
  ready: boolean;
  onCountChange(count: number): void;
  onShowToast(message: string, type: "success" | "info" | "error"): void;
}) {
  const [agents, setAgents] = useState<AgentCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<Editor>();
  const [source, setSource] = useState<AgentDefinitionDocument>();
  const [removing, setRemoving] = useState<AgentDefinitionDocument>();
  const searchRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await window.eidosRuntime.listAgentTypes();
      if (!mounted.current) return;
      setAgents(result.agents ?? []); onCountChange(result.agents?.length ?? 0); setError(result.error ?? undefined);
    } catch (cause) { if (mounted.current) setError(userFacingError(cause)); }
    finally { if (mounted.current) setLoading(false); }
  }, [onCountChange]);
  useEffect(() => { if (ready) void load(); else setLoading(false); }, [ready, load]);
  async function action(run: () => Promise<void>) {
    setError(undefined); setBusy(true);
    try { await run(); }
    catch (cause) { if (mounted.current) setError(userFacingError(cause)); }
    finally { if (mounted.current) setBusy(false); }
  }
  function read(agent: AgentCatalogItem, next: (document: AgentDefinitionDocument) => void) {
    void action(async () => {
      const document = await window.eidosRuntime.readAgentDefinition(agent.source);
      if (mounted.current) next(document);
    });
  }
  const needle = query.trim().toLocaleLowerCase();
  const visible = agents.filter((agent) => `${agent.name} ${agent.description}`.toLocaleLowerCase().includes(needle));
  return <div className="settings-panel agent-settings">
    <div inert={Boolean(editor || source || removing)}>
      <header className="settings-panel-header header-title-with-stats">
        <div><h1>智能体</h1><p className="settings-panel-subtitle">定义子任务的模型与工作方式。</p></div>
        <Button variant="primary" disabled={!ready || busy} onClick={() => setEditor({})}>+ 创建智能体</Button>
      </header>
      <div className="agent-settings__toolbar">
        <input ref={searchRef} type="search" aria-label="搜索智能体" placeholder="搜索智能体" value={query} onChange={(event) => setQuery(event.target.value)} />
        <Button size="small" disabled={!ready || loading || busy} onClick={() => void load()}>刷新</Button>
      </div>
      {!ready && <p role="alert">Runtime 尚未就绪。</p>}
      {error && <p className="setting-field-error" role="alert">{error}</p>}
      {loading && <p role="status">正在读取智能体…</p>}
      <div className="agent-settings__list" aria-label="自定义智能体">
        {visible.map((agent) => <article className="agent-settings__row" key={agent.source}>
          <div className="agent-settings__copy">
            <h2>{agent.name}</h2><p>{agent.description || "配置尚未提供有效的用途说明。"}</p>
            <p className="agent-settings__meta">{agentRoleLabels[agent.baseRole ?? "default"]} · {agent.providerId ? `${getProviderName(agent.providerId)} / ` : ""}{agent.modelId ?? "继承父任务模型"}</p>
            <span className={`agent-settings__status${agent.enabled && !agent.available ? " agent-settings__status--error" : ""}`}>
              {agent.enabled && agent.available ? "已启用" : agent.reason ?? "已停用"}
            </span>
          </div>
          <div className="agent-settings__actions">
            <Toggle label={`启用 ${agent.name}`} checked={agent.enabled ?? false} disabled={!ready || busy || !agent.definitionVersion}
              onChange={(enabled) => void action(async () => {
                const current = await window.eidosRuntime.readAgentDefinition(agent.source);
                await window.eidosRuntime.saveAgentDefinition({ source: current.source, expectedVersion: current.version, definition: { ...current.definition, enabled } });
                await load();
              })} />
            <Button size="small" disabled={!ready || busy || !agent.definitionVersion} onClick={() => read(agent, (document) => setEditor({ document }))}>编辑</Button>
            <details className="agent-settings__more"><summary aria-label={`${agent.name} 的更多操作`}>⋯</summary>
              <div className="dropdown-menu">
                <button type="button" disabled={busy || !agent.definitionVersion} onClick={(event) => {
                  event.currentTarget.closest("details")?.removeAttribute("open");
                  read(agent, (document) => setEditor({ initialDefinition: { ...document.definition, enabled: true, name: `${document.definition.name.slice(0, 59)}-copy` } }));
                }}>复制为新智能体</button>
                <button type="button" disabled={busy || !agent.definitionVersion} onClick={(event) => {
                  event.currentTarget.closest("details")?.removeAttribute("open"); read(agent, setSource);
                }}>查看配置</button>
                <button type="button" disabled={busy} onClick={() => void action(() => window.eidosRuntime.showItemInFolder(agent.source))}>在 Finder 中显示</button>
                <button type="button" className="danger-action" disabled={busy || !agent.definitionVersion} onClick={(event) => {
                  event.currentTarget.closest("details")?.removeAttribute("open"); read(agent, setRemoving);
                }}>删除</button>
              </div>
            </details>
          </div>
        </article>)}
      </div>
      {!loading && ready && !visible.length && <EmptySettingsState title={needle ? "没有匹配的智能体" : "还没有自定义智能体"}
        description={needle ? "请尝试其他名称或关键词。" : "你可以创建智能体，也可以继续通过 TOML 文件手动配置。"} />}
      <details className="agent-settings__builtins"><summary>内置智能体 · 3</summary>
        {Object.entries(agentRoleLabels).map(([role, label]) => <div className="agent-settings__builtin" key={role}>
          <span>{label} <small>{role}</small></span>
          <Button size="small" disabled={!ready || busy} onClick={() => setEditor({ initialDefinition: {
            schemaVersion: 1, name: `${role}-custom`, description: label, baseRole: role as NonNullable<AgentDefinition["baseRole"]>, enabled: true,
            instructions: "你需要完成父任务分配的工作，并报告结果、证据和未完成事项。",
          } })}>复制为自定义智能体</Button>
        </div>)}
      </details>
    </div>
    {editor && <AgentEditorDialog {...editor} onClose={() => { setEditor(undefined); requestAnimationFrame(() => searchRef.current?.focus()); }} onSaved={() => {
      setEditor(undefined); requestAnimationFrame(() => searchRef.current?.focus()); onShowToast("智能体已保存，将用于后续新任务。", "success"); void load();
    }} />}
    {source && <AgentSourceDialog document={source} onClose={() => { setSource(undefined); requestAnimationFrame(() => searchRef.current?.focus()); }} />}
    <ConfirmDialog open={Boolean(removing)} title={`删除智能体“${removing?.definition.name ?? ""}”？`}
      description="删除后，新任务将无法选择该智能体。已经开始的任务会继续使用保存的定义。" confirmLabel="删除" isDestructive busy={busy} error={error}
      getFallbackFocus={() => searchRef.current} onCancel={() => { setRemoving(undefined); setError(undefined); }}
      onConfirm={() => { if (removing) void action(async () => {
        await window.eidosRuntime.removeAgentDefinition({ source: removing.source, expectedVersion: removing.version });
        setRemoving(undefined); await load(); onShowToast("智能体已删除。", "success");
      }); }} />
  </div>;
}

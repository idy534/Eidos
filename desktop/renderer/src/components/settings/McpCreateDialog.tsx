import React, { useEffect, useRef, useState } from "react";
import type { McpCreateInput, McpServerRecord } from "../../contracts.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";

interface McpCreateDialogProps {
  open: boolean;
  busy: boolean;
  removeBusy?: boolean;
  error?: string | undefined;
  server?: McpServerRecord | null;
  onSave: (input: McpCreateInput) => Promise<void>;
  onRemove?: (serverId: string) => Promise<void>;
  onCancel: () => void;
}

interface EnvRow {
  name: string;
  value: string;
}

const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
const SERVER_ID = /^[a-z][a-z0-9_-]{0,63}$/;

function initialDraft() {
  return {
    serverId: "",
    executable: "",
    argv: [""],
    env: [{ name: "", value: "" }] as EnvRow[],
    envNames: [""],
    cwd: "",
    permissionProfile: "workspace_read" as McpCreateInput["permissionProfile"],
    startupTimeoutSeconds: 15,
    toolTimeoutSeconds: 60,
  };
}

function draftFromServer(server: McpServerRecord) {
  return {
    serverId: server.serverId,
    executable: server.executable,
    argv: server.argv.length ? [...server.argv] : [""],
    env: [{ name: "", value: "" }] as EnvRow[],
    envNames: server.envNames.length ? [...server.envNames] : [""],
    cwd: server.cwd ?? "",
    permissionProfile: server.permissionProfile,
    startupTimeoutSeconds: server.startupTimeoutSeconds,
    toolTimeoutSeconds: server.toolTimeoutSeconds,
  };
}

export function McpCreateDialog({
  open,
  busy,
  removeBusy = false,
  error,
  server = null,
  onSave,
  onRemove,
  onCancel,
}: McpCreateDialogProps) {
  const [draft, setDraft] = useState(initialDraft);
  const [localError, setLocalError] = useState<string>();
  const [confirmRemove, setConfirmRemove] = useState(false);
  const serverIdRef = useRef<HTMLInputElement>(null);
  useDialogFocusLifecycle({ open, initialFocusRef: serverIdRef });

  useEffect(() => {
    if (!open) return;
    setDraft(server ? draftFromServer(server) : initialDraft());
    setLocalError(undefined);
    setConfirmRemove(false);
  }, [open, server]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy && !removeBusy) onCancel();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, onCancel, open, removeBusy]);

  if (!open) return null;

  async function save() {
    const serverId = draft.serverId.trim();
    const executable = draft.executable.trim();
    const argv = draft.argv.map((value) => value.trim()).filter(Boolean);
    const envRows = draft.env.filter((row) => row.name.trim());
    const envNames = draft.envNames.map((value) => value.trim()).filter(Boolean);
    if (!SERVER_ID.test(serverId)) {
      setLocalError("Server ID 必须以小写字母开头，只能包含小写字母、数字、下划线和连字符。");
      return;
    }
    if (!executable) {
      setLocalError("请填写启动命令。");
      return;
    }
    const configuredEnv: Record<string, string> = {};
    for (const row of envRows) {
      const name = row.name.trim();
      if (!ENV_NAME.test(name)) {
        setLocalError(`环境变量名无效：${name}`);
        return;
      }
      if (configuredEnv[name] !== undefined) {
        setLocalError(`环境变量名重复：${name}`);
        return;
      }
      configuredEnv[name] = row.value;
    }
    const seenPassthrough = new Set<string>();
    for (const name of envNames) {
      if (!ENV_NAME.test(name) || seenPassthrough.has(name)) {
        setLocalError(`环境变量透传名无效或重复：${name}`);
        return;
      }
      seenPassthrough.add(name);
    }
    setLocalError(undefined);
    try {
      const input: McpCreateInput = {
        serverId,
        executable,
        argv,
        env: configuredEnv,
        envNames,
        permissionProfile: draft.permissionProfile,
        startupTimeoutSeconds: draft.startupTimeoutSeconds,
        toolTimeoutSeconds: draft.toolTimeoutSeconds,
      };
      if (draft.cwd.trim()) input.cwd = draft.cwd.trim();
      await onSave(input);
    } catch {
      // The parent reports the stable Runtime error below the form.
    }
  }

  async function remove() {
    if (!server || !onRemove) return;
    setLocalError(undefined);
    try {
      await onRemove(server.serverId);
      setConfirmRemove(false);
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : "卸载 MCP Server 失败");
    }
  }

  const formError = localError || error;
  return (
    <div className="modal-backdrop" onClick={busy || removeBusy ? undefined : onCancel}>
      <div
        className="modal-dialog modal-dialog--wide mcp-create-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-create-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="mcp-create-title">{server ? "编辑 MCP Server" : "连接至自定义 MCP"}</h3>
          <p className="modal-subtitle">配置一个本地 STDIO MCP Server。保存后，Eidos 会先等待你的审阅。</p>
        </div>
        <div className="modal-body mcp-create-body">
          <div className="mcp-create-type-row" aria-label="MCP 类型">
            <span className="mcp-create-type-label">类型</span>
            <button type="button" className="mcp-create-type-button is-active" disabled>STDIO</button>
            <button type="button" className="mcp-create-type-button" disabled>流式 HTTP（暂不支持）</button>
          </div>
          <label>
            <span>名称</span>
            <input
              ref={serverIdRef}
              aria-label="名称"
              placeholder="例如 filesystem"
              value={draft.serverId}
              disabled={busy || removeBusy}
              readOnly={Boolean(server)}
              onChange={(event) => setDraft({ ...draft, serverId: event.target.value })}
            />
          </label>
          <label>
            <span>启动命令</span>
            <input
              aria-label="启动命令"
              placeholder="例如 npx 或 python3"
              value={draft.executable}
              disabled={busy}
              onChange={(event) => setDraft({ ...draft, executable: event.target.value })}
            />
          </label>
          <div className="mcp-create-list-field">
            <span>参数</span>
            {draft.argv.map((value, index) => (
              <div className="mcp-create-entry" key={`arg-${index}`}>
                <input
                  aria-label={`参数 ${index + 1}`}
                  placeholder="参数"
                  value={value}
                  disabled={busy}
                  onChange={(event) => setDraft({
                    ...draft,
                    argv: draft.argv.map((item, itemIndex) => itemIndex === index ? event.target.value : item),
                  })}
                />
                <Button
                  variant="ghost"
                  size="small"
                  disabled={busy || draft.argv.length === 1}
                  onClick={() => setDraft({ ...draft, argv: draft.argv.filter((_, itemIndex) => itemIndex !== index) })}
                  aria-label={`删除参数 ${index + 1}`}
                >
                  删除
                </Button>
              </div>
            ))}
            <Button variant="ghost" size="small" disabled={busy} onClick={() => setDraft({ ...draft, argv: [...draft.argv, ""] })}>
              + 添加参数
            </Button>
          </div>
          <div className="mcp-create-list-field">
            <span>环境变量</span>
            {draft.env.map((row, index) => (
              <div className="mcp-create-entry mcp-create-env-entry" key={`env-${index}`}>
                <input
                  aria-label={`环境变量名 ${index + 1}`}
                  placeholder="名称"
                  value={row.name}
                  disabled={busy}
                  onChange={(event) => setDraft({
                    ...draft,
                    env: draft.env.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item),
                  })}
                />
                <input
                  aria-label={`环境变量值 ${index + 1}`}
                  type="password"
                  autoComplete="off"
                  placeholder={server ? "留空保持原值" : "值"}
                  value={row.value}
                  disabled={busy}
                  onChange={(event) => setDraft({
                    ...draft,
                    env: draft.env.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item),
                  })}
                />
                <Button
                  variant="ghost"
                  size="small"
                  disabled={busy || draft.env.length === 1}
                  onClick={() => setDraft({ ...draft, env: draft.env.filter((_, itemIndex) => itemIndex !== index) })}
                  aria-label={`删除环境变量 ${index + 1}`}
                >
                  删除
                </Button>
              </div>
            ))}
            <Button variant="ghost" size="small" disabled={busy} onClick={() => setDraft({ ...draft, env: [...draft.env, { name: "", value: "" }] })}>
              + 添加环境变量
            </Button>
          </div>
          <div className="mcp-create-list-field">
            <span>环境变量透传</span>
            {draft.envNames.map((value, index) => (
              <div className="mcp-create-entry" key={`env-name-${index}`}>
                <input
                  aria-label={`环境变量透传名 ${index + 1}`}
                  placeholder="例如 PATH"
                  value={value}
                  disabled={busy}
                  onChange={(event) => setDraft({
                    ...draft,
                    envNames: draft.envNames.map((item, itemIndex) => itemIndex === index ? event.target.value : item),
                  })}
                />
                <Button
                  variant="ghost"
                  size="small"
                  disabled={busy || draft.envNames.length === 1}
                  onClick={() => setDraft({ ...draft, envNames: draft.envNames.filter((_, itemIndex) => itemIndex !== index) })}
                  aria-label={`删除环境变量透传名 ${index + 1}`}
                >
                  删除
                </Button>
              </div>
            ))}
            <Button variant="ghost" size="small" disabled={busy} onClick={() => setDraft({ ...draft, envNames: [...draft.envNames, ""] })}>
              + 添加透传名
            </Button>
          </div>
          <label>
            <span>工作目录</span>
            <input
              aria-label="工作目录"
              placeholder="留空使用用户主目录"
              value={draft.cwd}
              disabled={busy}
              onChange={(event) => setDraft({ ...draft, cwd: event.target.value })}
            />
          </label>
          <label>
            <span>权限配置</span>
            <select
              aria-label="权限配置"
              value={draft.permissionProfile}
              disabled={busy}
              onChange={(event) => setDraft({
                ...draft,
                permissionProfile: event.target.value as McpCreateInput["permissionProfile"],
              })}
            >
              <option value="workspace_read">workspace_read：只读工作区</option>
              <option value="connector">connector：允许联网连接器</option>
            </select>
          </label>
          {formError && <p className="setting-field-error" role="alert">{formError}</p>}
        </div>
        <div className="modal-footer mcp-create-footer">
          {server && onRemove ? (
            <Button
              variant="danger"
              disabled={busy || removeBusy}
              onClick={() => setConfirmRemove(true)}
            >
              卸载
            </Button>
          ) : <span />}
          <div className="mcp-create-footer-actions">
            <Button variant="ghost" disabled={busy || removeBusy} onClick={onCancel}>取消</Button>
            <Button variant="primary" loading={busy} disabled={busy || removeBusy} onClick={() => void save()}>保存</Button>
          </div>
        </div>
      </div>
      <ConfirmDialog
        open={confirmRemove}
        title="卸载 MCP Server？"
        description={`确定要卸载“${server?.serverId ?? ""}”吗？卸载后需要重新添加才能使用。`}
        confirmLabel="卸载"
        isDestructive
        busy={removeBusy}
        error={localError}
        onConfirm={() => void remove()}
        onCancel={() => setConfirmRemove(false)}
      />
    </div>
  );
}

import { useState } from "react";
import type { ChannelConfig, ChannelPublicConnection, ChannelSaveRequest } from "../../../../shared/channels.js";
import type { ModelOption, Project } from "../../contracts.js";
import { Button } from "../Button.js";

interface Props {
  connection: ChannelPublicConnection | null;
  models: ModelOption[];
  projects: Project[];
  busy: boolean;
  onSave: (request: ChannelSaveRequest) => Promise<boolean>;
}

const splitIds = (text: string) => [...new Set(text.split(/[\s,，]+/).filter(Boolean))];

export function DingTalkForm({ connection, models, projects, busy, onSave }: Props) {
  const [config, setConfig] = useState<ChannelConfig>(connection?.config ?? {
    enabled: false, clientId: "", robotCode: "", corpId: "", modelId: models[0]?.id ?? "",
    projectId: null, approvalMode: "manual", allowedUserIds: [""], allowedConversationIds: [], allowDirectMessages: false,
  });
  const [secret, setSecret] = useState("");
  const [users, setUsers] = useState(config.allowedUserIds.join("\n"));
  const [groups, setGroups] = useState((config.allowedConversationIds ?? []).join("\n"));
  const [error, setError] = useState("");
  const change = <K extends keyof ChannelConfig>(key: K, value: ChannelConfig[K]) => setConfig((old) => ({ ...old, [key]: value }));
  const missingModel = config.modelId && !models.some((model) => model.id === config.modelId);
  const missingProject = config.projectId && !projects.some((project) => project.id === config.projectId);

  return <form className="channel-form" onSubmit={(event) => {
    event.preventDefault(); setError("");
    const identifiers = splitIds(users);
    if (!identifiers.length) { setError("请填写至少一个允许用户的企业 UserID。 "); return; }
    const targetGroups = splitIds(groups);
    if (config.enabled && !config.allowDirectMessages && !targetGroups.length) {
      setError("请填写至少一个允许群，或开启单聊。 "); return;
    }
    if (!secret && (!connection?.hasSecret || config.clientId !== connection.config.clientId)) {
      setError("请填写 Client Secret。 "); return;
    }
    void onSave({ expectedRevision: connection?.revision ?? 0,
      config: { ...config, allowedUserIds: identifiers as [string, ...string[]], allowedConversationIds: targetGroups },
      ...(secret ? { clientSecret: secret } : {}),
    }).then((saved) => { if (saved) setSecret(""); });
  }}>
    <fieldset disabled={busy}>
      <legend className="sr-only">钉钉连接配置</legend>
      <label className="channel-check"><input type="checkbox" checked={config.enabled ?? false}
        onChange={(event) => change("enabled", event.target.checked)}/>启用钉钉渠道</label>
      <div className="channel-fields">
        <label>Client ID<input required maxLength={256} value={config.clientId} autoComplete="off"
          onChange={(event) => change("clientId", event.target.value.trim())}/></label>
        <label>Client Secret<input type="password" maxLength={1024} value={secret} autoComplete="new-password"
          placeholder={connection?.hasSecret ? "留空保留已保存的凭证" : "请输入应用凭证"}
          onChange={(event) => setSecret(event.target.value)}/></label>
        <label>机器人 RobotCode<input required maxLength={256} value={config.robotCode}
          onChange={(event) => change("robotCode", event.target.value.trim())}/></label>
        <label>组织 CorpID<input required maxLength={256} value={config.corpId}
          onChange={(event) => change("corpId", event.target.value.trim())}/></label>
        <label>模型<select required value={config.modelId} onChange={(event) => change("modelId", event.target.value)}>
          <option value="">请选择模型</option>
          {missingModel && <option value={config.modelId}>已保存的模型不可用</option>}
          {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
        </select></label>
        <label>执行位置<select value={config.projectId ?? ""} onChange={(event) => change("projectId", event.target.value || null)}>
          <option value="">独立工作区</option>
          {missingProject && <option value={config.projectId ?? ""}>已保存的项目不可用</option>}
          {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select></label>
        <label>审批模式<select value={config.approvalMode ?? "manual"}
          onChange={(event) => change("approvalMode", event.target.value as "manual" | "auto_review")}>
          <option value="manual">请求审批</option><option value="auto_review">替我审批</option>
        </select></label>
      </div>
      <label>允许的用户 UserID<textarea required rows={3} value={users} maxLength={25_600}
        aria-describedby="channel-user-help" onChange={(event) => setUsers(event.target.value)}/></label>
      <p id="channel-user-help" className="channel-help">用户可以填写多个企业 UserID，每行一个。系统不会使用昵称识别用户。</p>
      <label>允许的群 ConversationID<textarea rows={3} value={groups} maxLength={25_600}
        onChange={(event) => setGroups(event.target.value)}/></label>
      <label className="channel-check"><input type="checkbox" checked={config.allowDirectMessages ?? false}
        onChange={(event) => change("allowDirectMessages", event.target.checked)}/>允许这些用户与机器人单聊</label>
      {error && <p role="alert" className="error-banner">{error}</p>}
      <Button type="submit" variant="primary">{busy ? "正在保存…" : "保存渠道配置"}</Button>
    </fieldset>
  </form>;
}

import { useEffect, useState } from "react";
import type { ChannelResponse, ChannelSaveRequest } from "../../../../shared/channels.js";
import type { ModelListResult, Project } from "../../contracts.js";
import { SettingSection } from "./SettingSection.js";
import { Button } from "../Button.js";
import { DingTalkForm } from "./DingTalkForm.js";

const states = { disabled: "未连接", connecting: "正在连接", connected: "已连接", error: "连接异常" };
const deliveryStates = { pending: "等待发送", sending: "正在发送", sent: "已发送", failed: "发送失败", unknown: "发送结果未知" };

export function ChannelSettings({ modelList }: { modelList?: ModelListResult | undefined }) {
  const [response, setResponse] = useState<ChannelResponse>();
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refresh() {
      try {
        const value = await window.eidosRuntime.readChannel();
        if (active) setResponse(value);
      } catch { if (active) setError("无法读取渠道状态，请检查 Runtime 后刷新。 "); }
      if (active) timer = setTimeout(() => { void refresh(); }, 3000);
    }
    void window.eidosRuntime.listProjects().then((value) => { if (active) setProjects(value.items); })
      .catch(() => { if (active) setError("无法读取项目列表，请重新打开设置。 "); });
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, []);

  async function save(request: ChannelSaveRequest) {
    setBusy(true); setError(""); setNotice("");
    try {
      const value = await window.eidosRuntime.saveChannel(request);
      setResponse(value);
      setNotice(request.config.enabled ? "配置已保存，Eidos 正在连接钉钉。" : "渠道已停用。");
      return true;
    } catch {
      setError("渠道保存失败。请检查配置、模型和项目；配置有冲突时请刷新后重试。 ");
      return false;
    } finally { setBusy(false); }
  }

  return <SettingSection className="channel-settings" title="钉钉" description="Eidos 运行时可以接收并回复钉钉消息。群内回复对群成员可见。">
    {error && <p className="error-banner" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!response ? <p role="status">正在读取渠道配置…</p> : <>
      <div className="channel-status" role="status"><span>{states[response.state ?? "disabled"]}</span>
        <Button size="small" variant="secondary" disabled={busy} onClick={() => {
          void window.eidosRuntime.readChannel().then((value) => { setResponse(value); setError(""); })
            .catch(() => setError("无法刷新渠道状态。"));
        }}>刷新状态</Button></div>
      {response.reason && <p role="status">{response.reason}</p>}
      <DingTalkForm key={response.connection?.revision ?? 0} connection={response.connection ?? null}
        models={modelList?.models ?? []} projects={projects} busy={busy} onSave={save}/>
      <div className="channel-help">
        <p>用户需要在钉钉开放平台创建企业内部应用，开启机器人并选择 Stream 模式，再把机器人加入所属组织的内部群。</p>
        <p>群消息需要 @ 机器人。系统按群和发送者保留上下文。人工审批和补充信息需要在本机 Eidos 完成。</p>
        <p>电脑休眠或 Eidos 退出时，机器人无法接收消息。同一应用请只在一台 Eidos 上启用。</p>
      </div>
      <h3>最近回复</h3>
      {!response.deliveries?.length ? <p>渠道还没有回复记录。</p> : <ul className="channel-deliveries">
        {response.deliveries.map((delivery) => <li key={delivery.id}>
          <span>{deliveryStates[delivery.status]}</span>
          <p>{delivery.text.slice(0, 120)}</p>
          {delivery.reason && <p>{delivery.reason}</p>}
        </li>)}
      </ul>}
    </>}
  </SettingSection>;
}

import { useState } from "react";
import type { Run } from "../contracts.js";
import { AlertBanner } from "./AlertBanner.js";
import { Button } from "./Button.js";
import { HookHistoryDialog } from "./settings/HookHistoryDialog.js";

export function HookActivityRows({ run }: { run: Run }) {
  const [history, setHistory] = useState(false);
  const activities = run.hookActivity ?? [];
  if (!activities.length) return null;
  const active = activities.find(item => item.status === "running" || item.status === "intent");
  const warnings = activities.filter(item => item.result?.systemMessage || item.status === "failed" || item.status === "unknown");
  return <>
    <div className="hook-feed-activity" inert={history}>
      <div><span role="status">{active ? active.statusMessage || `正在执行 ${active.event} Hook…` : "Hook 检查已结束"}</span>
        <Button variant="ghost" size="small" onClick={() => setHistory(true)}>查看 Hook 记录</Button></div>
      {warnings.map(item => <AlertBanner key={item.id} tone="warning">{item.result?.systemMessage || `${item.event} Hook ${item.status === "unknown" ? "结果未知" : "执行失败"}，此次检查未通过。`}</AlertBanner>)}
    </div>
    <HookHistoryDialog open={history} runId={run.id} onClose={() => setHistory(false)} />
  </>;
}

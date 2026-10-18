import { useEffect, useRef, useState } from "react";
import type { ContextDetails } from "../../../shared/context-management.js";
import type { ContextUsage } from "../contracts.js";
import { formatTokenCount } from "../context-usage.js";

const sections = {
  instructions: "指令", tools: "工具定义", conversation: "对话与思考",
  tool_results: "工具结果", summary: "历史摘要", other: "其他", overhead: "协议预留",
};
const measurements = {
  provider: "Provider 实测", provider_calibrated: "根据 Provider 实测估算",
  tokenizer_estimate: "词表估算", unavailable: "暂无统计",
};
const triggers = {
  manual: "用户手动请求", soft_target: "旧历史较多", window_pressure: "请求可能超过模型窗口",
  provider_limit: "提供商返回上下文过大", projection_limit: "历史投影超过内部上限",
};
const reasons: Record<string, string> = {
  model_unavailable: "系统使用规则摘要。",
  structured_output_unavailable: "当前模型不支持结构化工具输出，系统使用规则摘要。",
  summary_input_limit: "摘要输入超过预算，系统使用规则摘要。",
  summary_output_limit: "模型摘要超过预算，系统使用规则摘要。",
  summary_model_failed: "模型摘要无效或请求失败，系统使用规则摘要。",
  summary_claims_downgraded: "系统已把缺少确认依据的说明标为未验证。",
  summary_timeout: "模型摘要超时，系统使用规则摘要。",
  no_compactable_history: "系统没有找到可以压缩的旧历史。",
  source_changed: "任务事实已变化，系统没有采用这次摘要。",
  canceled: "任务已取消，系统没有采用这次摘要。",
  interrupted: "任务在压缩期间中断，系统没有采用未提交的摘要。",
};

export function ContextDetailsPanel({ runId, usage, onClose }: {
  runId: string; usage: ContextUsage | undefined; onClose(): void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [details, setDetails] = useState<ContextDetails>();
  const [loading, setLoading] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    dialog.current?.showModal();
    return () => { alive.current = false; };
  }, []);
  useEffect(() => {
    let current = true;
    setLoading(true);
    void window.eidosRuntime.readContextDetails(runId).then((value) => {
      if (current) { setDetails(value); setError(""); }
    }).catch(() => {
      if (current) setError("系统暂时无法读取上下文统计，请关闭后重试。");
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [runId, usage]);

  async function compact() {
    setRequesting(true);
    setError("");
    try {
      const record = await window.eidosRuntime.requestContextCompaction(runId);
      if (alive.current) setDetails((previous) => previous ? { ...previous, manualPending: true, compaction: record } : previous);
    } catch {
      if (alive.current) setError("系统未能安排压缩，请稍后重试。");
    } finally {
      if (alive.current) setRequesting(false);
    }
  }

  const record = details?.compaction;
  const succeeded = record?.status === "succeeded";
  return (
    <dialog ref={dialog} className="context-details-dialog" aria-label="上下文详情" onClose={onClose}
      onClick={(event) => { if (event.target === event.currentTarget) dialog.current?.close(); }}>
      <div className="context-details-content">
        <div className="context-details-heading"><strong>上下文</strong><button type="button" aria-label="关闭上下文详情" onClick={() => dialog.current?.close()}>×</button></div>
        {loading && !details && <p role="status">系统正在读取统计…</p>}
        {error && <p role="alert">{error}</p>}
        {details && <>
          <p>{measurements[details.measurement]} · {details.activeTokens == null ? "--" : formatTokenCount(details.activeTokens)} / {formatTokenCount(details.contextWindowTokens)}</p>
          <p className="context-details-hint">Provider 数值对应最近一次请求。请求内容变化后，系统需要估算下一次用量。</p>
          {details.sections?.length ? <>
            <p className="context-details-label">内容构成（词表参考值）</p>
            <dl>{details.sections.map((section) => <div key={section.name}><dt>{sections[section.name]}</dt><dd>{formatTokenCount(section.tokens)}</dd></div>)}</dl>
          </> : null}
          {!!details.clearedToolResults && <p>系统已缩减 {details.clearedToolResults} 份旧工具正文，原文可以回读。</p>}
          {details.lastEstimationErrorPercent != null && <p className="context-details-hint">最近请求估算比实测{details.lastEstimationErrorPercent >= 0 ? "高" : "低"} {Math.abs(details.lastEstimationErrorPercent)}% · 已记录 {details.calibrationSamples} 个样本</p>}
          {record && <div className="context-details-compaction">
            <p>{record.status === "queued" ? "系统已安排压缩。" : record.status === "running" ? "系统正在压缩上下文。" : succeeded ? `最近压缩：${record.strategy === "model" ? "模型辅助摘要" : "规则摘要"}` : record.status === "canceled" ? "最近压缩已取消。" : "最近压缩未完成。"}</p>
            <p className="context-details-hint">系统因{triggers[record.trigger]}发起压缩。{record.sourceItemCount ? `本次摘要覆盖 ${record.sourceItemCount} 条历史记录。` : ""}</p>
            {record.beforeTokens != null && record.afterTokens != null && <p>{formatTokenCount(record.beforeTokens)} → {formatTokenCount(record.afterTokens)} Tokens（估算）</p>}
            {record.stage === "finished" && <p className="context-details-hint">系统耗时 {((record.durationMs ?? 0) / 1000).toFixed(1)} 秒。{record.inputTokens != null ? `摘要模型用量：${formatTokenCount(record.inputTokens)} 输入 / ${formatTokenCount(record.outputTokens ?? 0)} 输出。` : ""}</p>}
            {record.reason && <p className="context-details-hint">{reasons[record.reason] ?? (succeeded ? "模型摘要未通过校验，系统使用规则摘要。" : "系统保留了原始历史和上一份有效摘要。")}</p>}
          </div>}
          <button className="context-details-compact" type="button" disabled={loading || requesting || details.manualPending || record?.status === "running"} onClick={() => void compact()}>
            {requesting ? "系统正在安排…" : details.manualPending ? "系统已安排压缩" : "压缩上下文"}
          </button>
          <p className="context-details-hint">压缩会在下一次安全的模型请求前执行。当前任务已结束时，系统会在下次发送后执行。原始历史会保留。</p>
        </>}
      </div>
    </dialog>
  );
}

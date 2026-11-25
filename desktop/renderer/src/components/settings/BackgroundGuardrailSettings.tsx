import { useEffect, useRef, useState } from "react";
import type { BackgroundGuardrailPolicy, GuardrailsResponse } from "../../../../shared/guardrails.js";
import { Button } from "../Button.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { ToastContainer } from "../ToastContainer.js";

function parseOptionalInt(
  raw: string,
  { min, max, label }: { min: number; max: number; label: string },
): { value: number | null; error?: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { value: null };
  if (!/^\d+$/.test(trimmed)) return { value: null, error: `${label}需要填写整数，留空表示不限制。` };
  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) {
    return { value: null, error: `${label}范围为 ${min.toLocaleString()} 到 ${max.toLocaleString()}。` };
  }
  return { value: parsed };
}

function parseOptionalFloat(
  raw: string,
  { max, label }: { max: number; label: string },
): { value: number | null; error?: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { value: null };
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed > max) {
    return { value: null, error: `${label}需要大于 0 且不超过 ${max.toLocaleString()}。` };
  }
  return { value: parsed };
}

export function BackgroundGuardrailSettings({ onShowToast }: {
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const supported = typeof window.eidosRuntime?.readGuardrails === "function";
  const [settings, setSettings] = useState<GuardrailsResponse>();
  const [maxTokens, setMaxTokens] = useState("");
  const [maxCost, setMaxCost] = useState("");
  const [maxIdleRounds, setMaxIdleRounds] = useState("3");
  const [loading, setLoading] = useState(supported);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    if (!supported) return () => { mounted.current = false; };
    let active = true;
    void window.eidosRuntime.readGuardrails().then((value) => {
      if (!active) return;
      setSettings(value);
      setMaxTokens(value.policy.maxTokensPerCycle != null ? String(value.policy.maxTokensPerCycle) : "");
      setMaxCost(value.policy.maxCostCreditsPerCycle != null ? String(value.policy.maxCostCreditsPerCycle) : "");
      setMaxIdleRounds(value.policy.maxIdleRounds != null ? String(value.policy.maxIdleRounds) : "");
    }).catch(() => {
      if (active) setError("后台护栏设置无法读取，请稍后重试。");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; mounted.current = false; };
  }, [supported]);

  if (!supported) return null;

  async function save() {
    if (!settings || saving) return;
    const tokens = parseOptionalInt(maxTokens, { min: 1_000, max: 100_000_000, label: "单轮 Token 上限" });
    if (tokens.error) { setError(tokens.error); onShowToast?.(tokens.error, "error"); return; }
    const cost = parseOptionalFloat(maxCost, { max: 1_000_000, label: "单轮成本上限" });
    if (cost.error) { setError(cost.error); onShowToast?.(cost.error, "error"); return; }
    const idle = parseOptionalInt(maxIdleRounds, { min: 1, max: 100, label: "连续无进展轮次上限" });
    if (idle.error) { setError(idle.error); onShowToast?.(idle.error, "error"); return; }

    const policy: BackgroundGuardrailPolicy = {
      maxTokensPerCycle: tokens.value,
      maxCostCreditsPerCycle: cost.value,
      maxIdleRounds: idle.value,
    };
    setSaving(true);
    setError("");
    try {
      const saved = await window.eidosRuntime.saveGuardrails({
        expectedRevision: settings.revision,
        policy,
      });
      if (!mounted.current) return;
      setSettings(saved);
      setMaxTokens(saved.policy.maxTokensPerCycle != null ? String(saved.policy.maxTokensPerCycle) : "");
      setMaxCost(saved.policy.maxCostCreditsPerCycle != null ? String(saved.policy.maxCostCreditsPerCycle) : "");
      setMaxIdleRounds(saved.policy.maxIdleRounds != null ? String(saved.policy.maxIdleRounds) : "");
      onShowToast?.("后台执行护栏已保存", "success");
    } catch {
      if (!mounted.current) return;
      const message = "后台执行护栏保存失败，请刷新后重试。";
      setError(message);
      onShowToast?.(message, "error");
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  return (
    <SettingSection title="后台执行护栏">
      {error && (
        onShowToast ? null : (
          <ToastContainer
            toasts={[{ id: `guardrail-error-${error}`, message: error, type: "error", duration: 4000 }]}
            onDismiss={() => setError("")}
          />
        )
      )}
      {error && onShowToast && <p role="alert" className="settings-error-text">{error}</p>}
      <SettingRow
        title="单轮 Token 上限"
        description="仅作用于 Goal 目标与定时任务；超限后暂停执行，恢复后从 0 重新计算"
        action={
          <input
            aria-label="单轮 Token 上限"
            type="text"
            inputMode="numeric"
            placeholder="不限制"
            value={maxTokens}
            disabled={loading || !settings || saving}
            onChange={(event) => setMaxTokens(event.target.value)}
          />
        }
      />
      <SettingRow
        title="单轮成本上限"
        description="单周期累计 Credits 上限；超限暂停，恢复后重新计数"
        action={
          <input
            aria-label="单轮成本上限"
            type="text"
            inputMode="decimal"
            placeholder="不限制"
            value={maxCost}
            disabled={loading || !settings || saving}
            onChange={(event) => setMaxCost(event.target.value)}
          />
        }
      />
      <SettingRow
        title="连续无进展轮次上限"
        description="连续多轮无新有效工具结果时暂停（默认 3 轮，留空不限制）"
        action={
          <input
            aria-label="连续无进展轮次上限"
            type="text"
            inputMode="numeric"
            placeholder="不限制"
            value={maxIdleRounds}
            disabled={loading || !settings || saving}
            onChange={(event) => setMaxIdleRounds(event.target.value)}
          />
        }
      />
      <SettingRow
        title="保存护栏配置"
        description="暂停支持手动恢复，新一轮再次超限时继续暂停"
        action={
          <Button
            size="small"
            variant="secondary"
            disabled={loading || !settings || saving}
            loading={saving}
            onClick={() => void save()}
          >
            保存护栏
          </Button>
        }
      />
    </SettingSection>
  );
}

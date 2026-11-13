import React, { useCallback, useEffect, useState } from "react";
import type { WorktreeSettings as WorktreeSettingsValue } from "../../../../shared/domain-contracts.js";
import { Button } from "../Button.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { Toggle } from "../Toggle.js";
import { AlertBanner } from "../AlertBanner.js";
import { Spinner } from "../Spinner.js";

export interface WorktreeSettingsProps {
  onShowToast?: (message: string, type?: "success" | "info" | "error") => void;
}

const LIMIT_MIN = 1;
const LIMIT_MAX = 100;

function clampLimit(value: number): number {
  return Math.max(LIMIT_MIN, Math.min(LIMIT_MAX, value));
}

function PanelHeader() {
  return (
    <div className="settings-panel-header">
      <h1>Git 工作树</h1>
      <p className="settings-panel-subtitle">管理 Eidos 为会话创建的工作树目录。</p>
    </div>
  );
}

export function WorktreeSettings({ onShowToast }: WorktreeSettingsProps = {}) {
  const [settings, setSettings] = useState<WorktreeSettingsValue | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [saving, setSaving] = useState(false);
  const [inputVal, setInputVal] = useState<string>("");

  const applyLoaded = useCallback((value: WorktreeSettingsValue) => {
    setSettings(value);
    setInputVal(String(value.managedWorktreeLimit));
  }, []);

  const loadSettings = useCallback(async () => {
    setError(undefined);
    try {
      applyLoaded(await window.eidosRuntime.readWorktreeSettings());
    } catch {
      setError("无法读取工作树设置。");
    }
  }, [applyLoaded]);

  useEffect(() => {
    let active = true;
    void window.eidosRuntime.readWorktreeSettings()
      .then((value) => {
        if (active) applyLoaded(value);
      })
      .catch(() => {
        if (active) setError("无法读取工作树设置。");
      });
    return () => { active = false; };
  }, [applyLoaded]);

  async function update(next: Pick<WorktreeSettingsValue, "automaticCleanup" | "managedWorktreeLimit">) {
    setSaving(true);
    setError(undefined);
    try {
      applyLoaded(await window.eidosRuntime.updateWorktreeSettings(next));
      onShowToast?.("工作树设置已更新", "success");
    } catch {
      if (settings) setInputVal(String(settings.managedWorktreeLimit));
      setError("工作树设置保存失败。");
      onShowToast?.("工作树设置保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  function updateLimit(limit: number) {
    if (!settings || saving) return;
    const next = clampLimit(limit);
    if (next !== settings.managedWorktreeLimit) {
      void update({ automaticCleanup: settings.automaticCleanup, managedWorktreeLimit: next });
    }
  }

  function commitInput() {
    if (!settings) return;
    const num = Number.parseInt(inputVal.trim(), 10);
    if (!Number.isNaN(num) && clampLimit(num) !== settings.managedWorktreeLimit) {
      updateLimit(num);
      return;
    }
    // Invalid or unchanged input falls back to the saved value.
    setInputVal(String(settings.managedWorktreeLimit));
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.currentTarget.blur();
    } else if (event.key === "Escape") {
      if (settings) setInputVal(String(settings.managedWorktreeLimit));
      event.currentTarget.blur();
    }
  }

  if (!settings) {
    return (
      <div className="settings-panel worktree-settings-panel">
        <PanelHeader />
        {error ? (
          <AlertBanner
            tone="error"
            action={
              <Button variant="secondary" size="small" onClick={() => void loadSettings()}>
                重试
              </Button>
            }
          >
            {error}
          </AlertBanner>
        ) : (
          <div className="worktree-loading-state">
            <Spinner size="md" tone="muted" ariaLabel="正在读取工作树设置…" />
          </div>
        )}
      </div>
    );
  }

  const limit = settings.managedWorktreeLimit;
  // The limit only matters while cleanup runs, so it is read-only when cleanup is off.
  const limitLocked = saving || !settings.automaticCleanup;

  return (
    <div className="settings-panel worktree-settings-panel">
      <PanelHeader />

      {error && <AlertBanner tone="error">{error}</AlertBanner>}

      <SettingSection title="清理策略">
        <SettingRow
          title="自动清理旧工作树"
          description="清理前保存改动，可在会话中恢复。"
          action={(
            <Toggle
              checked={settings.automaticCleanup}
              disabled={saving}
              label="自动清理旧工作树"
              onChange={(checked) => void update({ automaticCleanup: checked, managedWorktreeLimit: limit })}
            />
          )}
        />

        <SettingRow
          title="保留上限"
          disabled={limitLocked}
          action={(
            <div className="worktree-stepper">
              <button
                type="button"
                className="worktree-stepper-btn"
                disabled={limitLocked || limit <= LIMIT_MIN}
                onClick={() => updateLimit(limit - 1)}
                aria-label="减少 1 个"
              >
                −
              </button>
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                className="worktree-stepper-input"
                value={inputVal}
                disabled={limitLocked}
                aria-label="保留上限"
                onChange={(event) => setInputVal(event.target.value)}
                onBlur={commitInput}
                onKeyDown={handleKeyDown}
              />
              <button
                type="button"
                className="worktree-stepper-btn"
                disabled={limitLocked || limit >= LIMIT_MAX}
                onClick={() => updateLimit(limit + 1)}
                aria-label="增加 1 个"
              >
                +
              </button>
            </div>
          )}
        />
      </SettingSection>
    </div>
  );
}

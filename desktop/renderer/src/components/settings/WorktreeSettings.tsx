import React, { useCallback, useEffect, useState } from "react";
import type { WorktreeSettings as WorktreeSettingsValue } from "../../../../shared/domain-contracts.js";
import { Button } from "../Button.js";
import { SettingRow } from "./SettingRow.js";
import { SettingSection } from "./SettingSection.js";
import { StatusBadge } from "./StatusBadge.js";
import { Toggle } from "./Toggle.js";

export interface WorktreeSettingsProps {
  onShowToast?: (message: string, type?: "success" | "info" | "error") => void;
}

const WORKTREE_LIMIT_PRESETS = [5, 10, 15, 30, 50] as const;

/* ==========================================================================
   SVG Icons (macOS Native Vector Style)
   ========================================================================== */

function GitWorktreeIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <circle cx="4" cy="4" r="1.8" />
      <circle cx="4" cy="12" r="1.8" />
      <circle cx="12" cy="5.5" r="1.8" />
      <path d="M4 5.8v4.4" />
      <path d="M4 8.5a4 4 0 0 1 4-4h2.2" />
    </svg>
  );
}

function BroomIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M12.5 2.5l-4 4" />
      <path d="M8.5 6.5l-2-2-4.5 4.5c-.8.8-.8 2 0 2.8l1.2 1.2c.8.8 2 .8 2.8 0l4.5-4.5-2-2z" />
      <path d="M2 14h12" />
    </svg>
  );
}

function ShieldCheckIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M8 1.5 2.5 3.8v4.4c0 3.8 2.5 6.1 5.5 6.8 3-.7 5.5-3 5.5-6.8V3.8L8 1.5z" />
      <path d="m5.5 8 2 2 3.5-3.5" strokeWidth="1.4" />
    </svg>
  );
}

function SnapshotIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M2.5 4.5h2l1-2h5l1 2h2a1.5 1.5 0 0 1 1.5 1.5v6.5a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 2.5 12.5V6a1.5 1.5 0 0 1 1.5-1.5z" />
      <circle cx="8" cy="8.5" r="2.5" />
    </svg>
  );
}

/* ==========================================================================
   WorktreeSettings Component
   ========================================================================== */

export function WorktreeSettings({ onShowToast }: WorktreeSettingsProps = {}) {
  const [settings, setSettings] = useState<WorktreeSettingsValue | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [saving, setSaving] = useState(false);
  const [inputVal, setInputVal] = useState<string>("");

  const loadSettings = useCallback(async () => {
    setError(undefined);
    try {
      const value = await window.eidosRuntime.readWorktreeSettings();
      setSettings(value);
      setInputVal(String(value.managedWorktreeLimit));
    } catch {
      setError("无法读取 Worktree 设置。");
    }
  }, []);

  useEffect(() => {
    let active = true;
    void window.eidosRuntime.readWorktreeSettings()
      .then((value) => {
        if (active) {
          setSettings(value);
          setInputVal(String(value.managedWorktreeLimit));
        }
      })
      .catch(() => {
        if (active) setError("无法读取 Worktree 设置。");
      });
    return () => { active = false; };
  }, []);

  async function update(next: { automaticCleanup: boolean; managedWorktreeLimit: number }) {
    setSaving(true);
    setError(undefined);
    try {
      const value = await window.eidosRuntime.updateWorktreeSettings(next);
      setSettings(value);
      setInputVal(String(value.managedWorktreeLimit));
      onShowToast?.("Worktree 设置已更新", "success");
    } catch {
      setError("Worktree 设置保存失败。");
      onShowToast?.("Worktree 设置保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  function handleInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    setInputVal(event.target.value);
  }

  function commitInput() {
    if (!settings) return;
    const num = parseInt(inputVal.trim(), 10);
    if (!Number.isNaN(num)) {
      const clamped = Math.max(1, Math.min(100, num));
      if (clamped !== settings.managedWorktreeLimit) {
        void update({ automaticCleanup: settings.automaticCleanup, managedWorktreeLimit: clamped });
        return;
      }
    }
    // Revert to current valid value if input was invalid or unchanged
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

  function handleStep(delta: number) {
    if (!settings || saving) return;
    const nextLimit = Math.max(1, Math.min(100, settings.managedWorktreeLimit + delta));
    if (nextLimit !== settings.managedWorktreeLimit) {
      void update({ automaticCleanup: settings.automaticCleanup, managedWorktreeLimit: nextLimit });
    }
  }

  function handlePreset(preset: number) {
    if (!settings || saving) return;
    if (preset !== settings.managedWorktreeLimit) {
      void update({ automaticCleanup: settings.automaticCleanup, managedWorktreeLimit: preset });
    }
  }

  // Loading or Error State prior to data readiness
  if (!settings) {
    return (
      <div className="settings-panel worktree-settings-panel">
        <div className="settings-panel-header">
          <h1>Worktrees</h1>
          <p className="settings-panel-subtitle">管理本地 Git Managed Worktree 的生命周期与清理策略。</p>
        </div>
        {error ? (
          <div className="setting-banner-error" role="alert">
            <p>{error}</p>
            <Button variant="secondary" size="small" onClick={() => void loadSettings()}>
              重试
            </Button>
          </div>
        ) : (
          <div className="worktree-loading-state">
            <div className="worktree-loading-spinner" aria-hidden="true" />
            <p>正在读取 Worktree 设置…</p>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="settings-panel worktree-settings-panel">
      {/* Header Area */}
      <div className="settings-panel-header">
        <div className="header-title-with-stats">
          <div>
            <h1>Worktrees</h1>
            <p className="settings-panel-subtitle">
              Eidos 基于独立 Git Worktree 为会话提供代码执行与分支隔离。自动清理策略仅针对 Managed Worktree 目录，会话记录与差异快照均永久保留。
            </p>
          </div>
          <div className="worktree-header-badge">
            <StatusBadge tone={settings.automaticCleanup ? "success" : "neutral"} dot>
              {settings.automaticCleanup ? `自动清理开启中 (上限 ${settings.managedWorktreeLimit})` : "自动清理已停用"}
            </StatusBadge>
          </div>
        </div>
      </div>

      {error && <p className="setting-banner-error" role="alert">{error}</p>}

      {/* Summary / Metric Cards */}
      <div className="worktree-summary-grid">
        <div className="worktree-summary-card">
          <div className="worktree-summary-icon worktree-summary-icon--git">
            <GitWorktreeIcon />
          </div>
          <div className="worktree-summary-info">
            <span className="worktree-summary-label">执行隔离机制</span>
            <span className="worktree-summary-value">Git Worktree (Detached HEAD)</span>
            <span className="worktree-summary-hint">独立分支执行，主工作目录零干扰</span>
          </div>
        </div>

        <div className="worktree-summary-card">
          <div className="worktree-summary-icon worktree-summary-icon--cleanup">
            <BroomIcon />
          </div>
          <div className="worktree-summary-info">
            <span className="worktree-summary-label">清理保留策略</span>
            <span className="worktree-summary-value">
              {settings.automaticCleanup ? `保留最近 ${settings.managedWorktreeLimit} 个` : "永不主动清理"}
            </span>
            <span className="worktree-summary-hint">
              {settings.automaticCleanup ? "按最后使用时间回收超出配额的旧目录" : "全部 Managed 目录均持续保留"}
            </span>
          </div>
        </div>

        <div className="worktree-summary-card">
          <div className="worktree-summary-icon worktree-summary-icon--shield">
            <ShieldCheckIcon />
          </div>
          <div className="worktree-summary-info">
            <span className="worktree-summary-label">数据安全性保障</span>
            <span className="worktree-summary-value">完整差异快照与无损会话</span>
            <span className="worktree-summary-hint">释放目录前自动完成 Patch 归档保护</span>
          </div>
        </div>
      </div>

      {/* Configuration Section */}
      <SettingSection
        title="目录回收与保留配额"
        description="配置本地 Git 工作树的自动清理行为。当数量超出上限时，最久未使用的 Managed Worktree 将被安全释放以节省磁盘空间。"
      >
        <SettingRow
          title="自动清理 managed Worktree"
          description="达到数量上限时，Eidos 会在后台先为工作树生成差异快照（Snapshot），再释放旧目录。"
          action={(
            <div className="worktree-action-group">
              <StatusBadge tone={settings.automaticCleanup ? "success" : "neutral"} dot>
                {settings.automaticCleanup ? "已开启" : "已关闭"}
              </StatusBadge>
              <Toggle
                checked={settings.automaticCleanup}
                disabled={saving}
                label="自动清理 managed Worktree"
                onChange={(checked) => void update({
                  automaticCleanup: checked,
                  managedWorktreeLimit: settings.managedWorktreeLimit,
                })}
              />
            </div>
          )}
        />

        <SettingRow
          title="保留最近的 Worktree 数量"
          description="设定本地允许保留的最大 Worktree 数量（范围 1 到 100）。若关闭自动清理，此数值仍会保存但不会主动触发目录释放。"
          action={(
            <div className="worktree-stepper-container">
              <button
                type="button"
                className="worktree-stepper-btn"
                disabled={saving || settings.managedWorktreeLimit <= 1}
                onClick={() => handleStep(-1)}
                aria-label="减少 1 个"
              >
                −
              </button>
              <div className="worktree-stepper-input-wrap">
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  className="worktree-stepper-input"
                  value={inputVal}
                  disabled={saving}
                  aria-label="保留最近的 Worktree 数量"
                  onChange={handleInputChange}
                  onBlur={commitInput}
                  onKeyDown={handleKeyDown}
                />
                <span className="worktree-stepper-unit">个</span>
              </div>
              <button
                type="button"
                className="worktree-stepper-btn"
                disabled={saving || settings.managedWorktreeLimit >= 100}
                onClick={() => handleStep(1)}
                aria-label="增加 1 个"
              >
                +
              </button>
            </div>
          )}
        >
          <div className="worktree-presets-row">
            <span className="worktree-presets-label">快速预设：</span>
            <div className="worktree-preset-chips" role="group" aria-label="Worktree 数量预设">
              {WORKTREE_LIMIT_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  className={`worktree-preset-chip ${settings.managedWorktreeLimit === preset ? "is-selected" : ""}`}
                  disabled={saving}
                  onClick={() => handlePreset(preset)}
                >
                  {preset} 个{preset === 15 ? " (默认)" : ""}
                </button>
              ))}
            </div>
          </div>
          {!settings.automaticCleanup && (
            <div className="worktree-row-inactive-hint">
              <span>ℹ 自动清理功能已关闭，当前配额设置会在重新开启时生效。</span>
            </div>
          )}
        </SettingRow>
      </SettingSection>

      {/* Feature Knowledge Section */}
      <SettingSection
        title="工作机制与安全性保障"
        description="Eidos 采用非侵入式 Git 工作树内核，兼顾多任务并行开发与本地代码安全性。"
      >
        <div className="worktree-info-list">
          <div className="worktree-info-item">
            <div className="worktree-info-badge worktree-info-badge--snapshot">
              <SnapshotIcon />
            </div>
            <div className="worktree-info-content">
              <h4>自动快照保护（Worktree Snapshot）</h4>
              <p>
                在释放任何 Managed Worktree 之前，Eidos 会自动生成包含 Working Tree 与 Staged Changes 的二进制差异补丁，并在本地 Git 仓库中创建隐藏引用锚点（<code>refs/eidos/worktree-snapshots/*</code>），确保未提交改动绝不丢失。
              </p>
            </div>
          </div>

          <div className="worktree-info-item">
            <div className="worktree-info-badge worktree-info-badge--session">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="2.5" y="2.5" width="11" height="11" rx="2" />
                <path d="M5.5 6h5M5.5 9h3" />
              </svg>
            </div>
            <div className="worktree-info-content">
              <h4>会话与历史持久化（Durable Session）</h4>
              <p>
                Worktree 目录的清理仅释放文件系统的物理临时分支目录。会话元数据、上下文记忆、Checkpoints、Diff 对比记录及交互历史完整保留在 SQLite 数据库中。
              </p>
            </div>
          </div>

          <div className="worktree-info-item">
            <div className="worktree-info-badge worktree-info-badge--isolated">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="8" cy="8" r="5.5" />
                <path d="M8 2.5v11M2.5 8h11" />
              </svg>
            </div>
            <div className="worktree-info-content">
              <h4>主代码库零污染</h4>
              <p>
                任务在 Detached HEAD 状态的独立工作树中执行，不会打乱当前主仓库的 <code>HEAD</code>、分支检出或未暂存修改。如需将成果合入主分支，可随时使用分支合并或还原功能。
              </p>
            </div>
          </div>
        </div>
      </SettingSection>
    </div>
  );
}

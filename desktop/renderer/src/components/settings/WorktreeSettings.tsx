import { useEffect, useState } from "react";
import type { WorktreeSettings as WorktreeSettingsValue } from "../../../../shared/domain-contracts.js";
import { SettingRow } from "./SettingRow.js";
import { SettingSection } from "./SettingSection.js";
import { Toggle } from "./Toggle.js";

export function WorktreeSettings() {
  const [settings, setSettings] = useState<WorktreeSettingsValue | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    void window.eidosRuntime.readWorktreeSettings()
      .then((value) => { if (active) setSettings(value); })
      .catch(() => { if (active) setError("无法读取 Worktree 设置。"); });
    return () => { active = false; };
  }, []);

  async function update(next: { automaticCleanup: boolean; managedWorktreeLimit: number }) {
    setSaving(true);
    setError(undefined);
    try {
      const value = await window.eidosRuntime.updateWorktreeSettings(next);
      setSettings(value);
    } catch {
      setError("Worktree 设置保存失败。");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) {
    return <SettingSection title="Worktrees"><p className="settings-empty">正在加载…</p>{error && <p className="error-banner" role="alert">{error}</p>}</SettingSection>;
  }

  return (
    <SettingSection
      title="Worktrees"
      description="Eidos 只按数量清理 managed Worktree。Session 历史会保留。"
    >
      {error && <p className="error-banner" role="alert">{error}</p>}
      <SettingRow
        title="自动清理 managed Worktree"
        description="达到数量上限时，Eidos 会先保存 Snapshot，再释放旧目录。"
        action={(
          <Toggle
            checked={settings.automaticCleanup}
            disabled={saving}
            label="自动清理 managed Worktree"
            onChange={(checked) => void update({
              automaticCleanup: checked,
              managedWorktreeLimit: settings.managedWorktreeLimit,
            })}
          />
        )}
      />
      <SettingRow
        title="保留最近的 Worktree"
        description="范围为 1 到 100。关闭自动清理时，这个值会保留但不会执行清理。"
      >
        <label className="setting-number-field">
          <span className="sr-only">保留最近的 Worktree 数量</span>
          <input
            type="number"
            min={1}
            max={100}
            value={settings.managedWorktreeLimit}
            disabled={saving}
            onChange={(event) => {
              const value = Number(event.target.value);
              if (Number.isInteger(value) && value >= 1 && value <= 100) {
                void update({ automaticCleanup: settings.automaticCleanup, managedWorktreeLimit: value });
              }
            }}
          />
        </label>
      </SettingRow>
    </SettingSection>
  );
}

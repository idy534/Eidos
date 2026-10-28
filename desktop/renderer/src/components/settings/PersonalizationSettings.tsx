import { useId, useState } from "react";
import type { PersonalizationPreferences } from "../../../../shared/personalization.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { MarkdownEditor } from "../MarkdownEditor.js";
import { SettingRow } from "./SettingRow.js";
import { SettingSection } from "./SettingSection.js";
import "./PersonalizationSettings.css";
import { usePersonalizationSettings } from "./usePersonalizationSettings.js";
export { discardPersonalizationDraft } from "./usePersonalizationSettings.js";

type ChoiceKey = "responseLanguage" | "tone" | "responseLength" | "outputFormat";
const choices: { key: ChoiceKey; label: string; fallback: string; options: [string, string][] }[] = [
  { key: "responseLanguage", label: "回答语言", fallback: "auto", options: [["auto", "自动跟随提问"], ["zh-CN", "简体中文"], ["zh-TW", "繁體中文"], ["en", "English"], ["ja", "日本語"], ["ko", "한국어"], ["custom", "其他语言"]] },
  { key: "tone", label: "语气", fallback: "default", options: [["default", "默认"], ["direct", "直接"], ["friendly", "友好"], ["formal", "正式"]] },
  { key: "responseLength", label: "回答长度", fallback: "auto", options: [["auto", "自动"], ["concise", "简短"], ["standard", "适中"], ["detailed", "详细"]] },
  { key: "outputFormat", label: "输出格式偏好", fallback: "auto", options: [["auto", "自动"], ["conclusion_first", "先给结论"], ["paragraphs", "偏好段落"], ["lists", "偏好列表"]] },
];

export function PersonalizationSettings({ ready = true, paused = false, onShowToast, onDirtyChange, onBusyChange }: {
  ready?: boolean | undefined;
  paused?: boolean | undefined;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
  onDirtyChange?: ((dirty: boolean) => void) | undefined;
  onBusyChange?: ((busy: boolean) => void) | undefined;
}) {
  const id = useId();
  const [reloadOpen, setReloadOpen] = useState(false);
  const { saved, draft, busy, error, dirty, languageError, load, update, handleBlur } = usePersonalizationSettings({ ready, paused, onShowToast, onDirtyChange, onBusyChange });

  return (
    <div className="settings-panel personalization-settings" aria-busy={busy !== null}>
      <header className="settings-panel-header">
        <h1>个性化</h1>
        <p className="settings-panel-subtitle">设置 Eidos 的回答方式和个人偏好。保存后的设置会用于新任务。</p>
      </header>
      {!ready && <p role="status">Runtime 暂不可用。Eidos 会保留你尚未保存的修改。</p>}
      {busy === "load" && <p role="status">Eidos 正在读取个性化设置…</p>}
      {error && (
        <div className="personalization-status" role="alert">
          <span>{error}</span>
          <Button variant="secondary" size="small" disabled={!ready || busy !== null} onClick={() => dirty ? setReloadOpen(true) : void load()}>重新读取</Button>
        </div>
      )}
      <form onSubmit={event => { event.preventDefault(); handleBlur(); }}>
        <fieldset className="personalization-fields" disabled={!ready || !saved || busy === "load"}>
          <SettingSection title="语言与表达">
            {choices.map(choice => (
              <SettingRow
                key={choice.key}
                title={<label htmlFor={`${id}-${choice.key}`}>{choice.label}</label>}
                action={
                  <select
                    id={`${id}-${choice.key}`}
                    value={draft[choice.key] ?? choice.fallback}
                    onChange={event => update(choice.key, event.target.value as PersonalizationPreferences[ChoiceKey], true)}
                  >
                    {choice.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                }
              />
            ))}
            {draft.responseLanguage === "custom" && (
              <SettingRow title={<label htmlFor={`${id}-custom-language`}>其他回答语言</label>}>
                <input
                  id={`${id}-custom-language`}
                  value={draft.customLanguage ?? ""}
                  maxLength={80}
                  placeholder="例如：Español"
                  aria-invalid={Boolean(languageError)}
                  aria-describedby={languageError ? `${id}-language-error` : undefined}
                  onChange={event => update("customLanguage", event.target.value)}
                  onBlur={handleBlur}
                />
                {languageError && <p id={`${id}-language-error`} className="field-error" role="alert">请填写回答语言。</p>}
              </SettingRow>
            )}
          </SettingSection>
          <SettingSection title="关于你">
            <SettingRow title={<label htmlFor={`${id}-name`}>称呼</label>}>
              <input
                id={`${id}-name`}
                value={draft.preferredName ?? ""}
                maxLength={100}
                placeholder="你希望 Eidos 如何称呼你"
                onChange={event => update("preferredName", event.target.value)}
                onBlur={handleBlur}
              />
            </SettingRow>
            <SettingRow title={<label htmlFor={`${id}-role`}>角色</label>}>
              <input
                id={`${id}-role`}
                value={draft.role ?? ""}
                maxLength={300}
                placeholder="例如：产品经理、开发者、学生"
                onChange={event => update("role", event.target.value)}
                onBlur={handleBlur}
              />
            </SettingRow>
            <SettingRow title={<label htmlFor={`${id}-topics`}>熟悉的领域</label>}>
              <textarea
                id={`${id}-topics`}
                rows={3}
                value={draft.familiarTopics ?? ""}
                maxLength={2000}
                placeholder="例如：我熟悉 TypeScript 和产品设计，正在学习 Python。"
                onChange={event => update("familiarTopics", event.target.value)}
                onBlur={handleBlur}
              />
            </SettingRow>
          </SettingSection>
          <SettingSection title="自定义指令">
            <MarkdownEditor
              ariaLabel="自定义指令"
              value={draft.customInstructions ?? ""}
              maxLength={16384}
              rows={8}
              disabled={!ready || !saved || busy === "load"}
              allowExpand={false}
              placeholder="例如：请使用短句。你给出建议时，请说明依据。"
              onChange={value => update("customInstructions", value)}
              onBlur={handleBlur}
              hideCount
            />
          </SettingSection>
        </fieldset>
      </form>
      <ConfirmDialog
        open={reloadOpen}
        title="重新读取个性化设置？"
        description="Eidos 会载入最新设置。你尚未保存的修改将被丢弃。"
        confirmLabel="丢弃并重新读取"
        isDestructive
        onCancel={() => setReloadOpen(false)}
        onConfirm={() => { setReloadOpen(false); void load(); }}
      />
    </div>
  );
}

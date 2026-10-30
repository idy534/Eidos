import { useRef, useState } from "react";
import type { SkillMetadata, SkillRemoval } from "../../contracts.js";
import { userFacingError } from "../../session-state.js";
import type { SettingsPendingAction } from "./settings-types.js";
import { EmptyState } from "../EmptyState.js";
import { SearchInput } from "../SearchInput.js";
import { SkillDetailDialog, SkillIcon } from "./SkillDetailDialog.js";
import { Toggle } from "../Toggle.js";
import { ManagedResourceSettings } from "./ManagedResourceSettings.js";

interface SkillSettingsProps {
  skills: SkillMetadata[];
  pendingSkillIds?: ReadonlySet<string> | undefined;
  pendingAction: SettingsPendingAction;
  storageReady: boolean;
  onToggleSkill: (qualifiedId: string, enabled: boolean) => Promise<void>;
  onRemoveSkill: (qualifiedId: string) => Promise<SkillRemoval>;
  onShowToast: (message: string, type?: "success" | "info" | "error") => void;
}

export function SkillSettings({ skills, ...actions }: SkillSettingsProps) {
  const { onToggleSkill, pendingAction, pendingSkillIds = EMPTY_PENDING_SKILL_IDS, storageReady, onShowToast } = actions;
  const [category, setCategory] = useState<"personal" | "system">("personal");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string>();
  const [togglingIds, setTogglingIds] = useState<ReadonlySet<string>>(() => new Set());
  const searchRef = useRef<HTMLInputElement>(null);
  const standalone = skills.filter((skill) => skill.sourceKind !== "plugin");
  const selected = standalone.find((skill) => skill.qualifiedId === selectedId);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visible = standalone.filter((skill) => (
    (category === "system" ? skill.sourceKind === "system" : skill.sourceKind !== "system")
    && `${skill.name} ${skill.description}`.toLocaleLowerCase().includes(normalizedQuery)
  )).sort((a, b) => a.name.localeCompare(b.name));

  const isItemBusy = (qualifiedId: string) =>
    togglingIds.has(qualifiedId) ||
    pendingSkillIds.has(qualifiedId) ||
    (pendingAction?.type === "toggle_skill" && pendingAction.qualifiedId === qualifiedId) ||
    (pendingAction?.type === "remove_skill" && pendingAction.qualifiedId === qualifiedId);

  async function handleToggle(qualifiedId: string, enabled: boolean) {
    if (!onToggleSkill || isItemBusy(qualifiedId)) return;
    setTogglingIds((current) => new Set(current).add(qualifiedId));
    try {
      await onToggleSkill(qualifiedId, enabled);
      onShowToast(
        enabled ? "技能已启用，将从下一次任务生效" : "技能已禁用，将从下一次任务生效",
        "success",
      );
    } catch (cause) {
      onShowToast(userFacingError(cause), "error");
    } finally {
      setTogglingIds((current) => {
        const next = new Set(current);
        next.delete(qualifiedId);
        return next;
      });
    }
  }

  return (
    <div className="settings-panel skill-settings">
      <div inert={Boolean(selected)}>
        <div className="settings-panel-header">
          <h1>技能</h1>
          <p className="settings-panel-subtitle">管理个人和系统技能。</p>
        </div>
        <SearchInput
          className="skill-search"
          ariaLabel="搜索技能"
          placeholder="搜索技能"
          value={query}
          onChange={setQuery}
        />
        {category === "system" && <ManagedResourceSettings />}
        <div className="skill-categories" role="group" aria-label="技能分类">
          <button type="button" aria-pressed={category === "personal"} onClick={() => setCategory("personal")}>个人</button>
          <button type="button" aria-pressed={category === "system"} onClick={() => setCategory("system")}>系统</button>
        </div>
        <div className="skill-grid">
          {visible.map((skill) => {
            const itemBusy = isItemBusy(skill.qualifiedId);
            return (
              <div
                key={skill.qualifiedId}
                className={`skill-card${skill.enabled ? "" : " skill-card--disabled"}`}
              >
                <button
                  type="button"
                  className="skill-card-main"
                  aria-label={`${skill.name}，${skill.enabled ? "已启用" : "已禁用"}，查看详情`}
                  onClick={() => setSelectedId(skill.qualifiedId)}
                >
                  <SkillIcon name={skill.name} icon={skill.icon} />
                  <span className="skill-card-copy">
                    <span className="skill-card-name">{skill.name}</span>
                    <span className="skill-card-description">{skill.description || "未提供技能说明"}</span>
                  </span>
                </button>
                <div className="skill-card-action" onClick={(event) => event.stopPropagation()}>
                  <Toggle
                    checked={Boolean(skill.enabled)}
                    disabled={itemBusy || !storageReady}
                    label={`启用或停用 ${skill.name}`}
                    onChange={(enabled) => void handleToggle(skill.qualifiedId, enabled)}
                  />
                </div>
              </div>
            );
          })}
        </div>
        {visible.length === 0 && (
          <EmptyState
            title={query.trim() ? "没有匹配的技能" : category === "system" ? "没有系统技能" : "还没有个人技能"}
            description={query.trim() ? "请尝试其他名称或关键词。" : "安装后的技能会显示在这里。"}
          />
        )}
      </div>
      <SkillDetailDialog
        skill={selected}
        {...actions}
        onClose={() => setSelectedId(undefined)}
        getFallbackFocus={() => searchRef.current}
      />
    </div>
  );
}

const EMPTY_PENDING_SKILL_IDS: ReadonlySet<string> = new Set();

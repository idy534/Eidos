import { useRef, useState } from "react";
import type { SkillMetadata, SkillRemoval } from "../../contracts.js";
import type { SettingsPendingAction } from "./settings-types.js";
import { EmptySettingsState } from "./EmptySettingsState.js";
import { SkillDetailDialog, SkillInitial } from "./SkillDetailDialog.js";
import "./SkillSettings.css";

interface SkillSettingsProps {
  skills: SkillMetadata[];
  pendingAction: SettingsPendingAction;
  storageReady: boolean;
  onToggleSkill: (qualifiedId: string, enabled: boolean) => Promise<void>;
  onRemoveSkill: (qualifiedId: string) => Promise<SkillRemoval>;
  onShowToast: (message: string, type?: "success" | "info" | "error") => void;
}

export function SkillSettings({ skills, ...actions }: SkillSettingsProps) {
  const [category, setCategory] = useState<"personal" | "system">("personal");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string>();
  const searchRef = useRef<HTMLInputElement>(null);
  const selected = skills.find((skill) => skill.qualifiedId === selectedId);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visible = skills.filter((skill) => (
    (category === "system" ? skill.sourceKind === "system" : skill.sourceKind !== "system")
    && `${skill.name} ${skill.description}`.toLocaleLowerCase().includes(normalizedQuery)
  )).sort((a, b) => a.name.localeCompare(b.name));

  return (
    <div className="settings-panel skill-settings">
      <div inert={Boolean(selected)}>
        <div className="settings-panel-header">
          <h1>技能</h1>
          <p className="settings-panel-subtitle">通过任务专用技能扩展 Eidos</p>
        </div>
        <input
          ref={searchRef}
          className="skill-search"
          type="search"
          aria-label="搜索技能"
          placeholder="搜索技能"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="skill-categories" role="group" aria-label="技能分类">
          <button type="button" aria-pressed={category === "personal"} onClick={() => setCategory("personal")}>个人</button>
          <button type="button" aria-pressed={category === "system"} onClick={() => setCategory("system")}>系统</button>
        </div>
        <div className="skill-grid">
          {visible.map((skill) => (
            <button
              type="button"
              key={skill.qualifiedId}
              className={`skill-card${skill.enabled ? "" : " skill-card--disabled"}`}
              aria-label={`${skill.name}，${skill.enabled ? "已启用" : "已禁用"}，查看详情`}
              onClick={() => setSelectedId(skill.qualifiedId)}
            >
              <SkillInitial name={skill.name} />
              <span className="skill-card-copy">
                <span className="skill-card-name">{skill.name}</span>
                <span className="skill-card-description">{skill.description || "未提供技能说明"}</span>
              </span>
              <span className="skill-card-check" aria-hidden="true">{skill.enabled ? "✓" : ""}</span>
            </button>
          ))}
        </div>
        {visible.length === 0 && (
          <EmptySettingsState
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

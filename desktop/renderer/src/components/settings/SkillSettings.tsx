import React from "react";
import type { SkillMetadata } from "../../contracts";
import { SettingSection } from "./SettingSection";
import { SettingRow } from "./SettingRow";
import { StatusBadge } from "./StatusBadge";
import { EmptySettingsState } from "./EmptySettingsState";

interface SkillSettingsProps {
  skills: SkillMetadata[];
}

export function SkillSettings({ skills }: SkillSettingsProps) {
  return (
    <div className="settings-panel">
      <div className="settings-panel-header">
        <h1>Skills 能力集</h1>
        <p className="settings-panel-subtitle">
          展示由已启用的 Plugin 所注入的功能能力。Skill 由 Runtime 自动注入，在此处仅供查看。
        </p>
      </div>

      <SettingSection title="可用的 Skills">
        {skills.length === 0 ? (
          <EmptySettingsState
            title="还没有可用的 Skill"
            description="导入并启用包含 Skill 的 Plugin 后，功能能力会自动显示在这里。"
          />
        ) : (
          skills.map((skill) => (
            <SettingRow
              key={skill.qualifiedId}
              title={
                <div className="skill-row-title">
                  <span className="skill-name">{skill.name || skill.qualifiedId}</span>
                  <code className="skill-qualified-id">{skill.qualifiedId}</code>
                </div>
              }
              description={skill.description || "未提供 Skill 说明"}
              meta={
                <div className="skill-meta-info">
                  <span>来源 Plugin: <code>{skill.pluginId}</code></span>
                  <span className="meta-divider">•</span>
                  <span>版本 <code>v{skill.pluginVersion}</code></span>
                </div>
              }
              action={
                <StatusBadge tone="neutral" dot={false}>只读</StatusBadge>
              }
            />
          ))
        )}
      </SettingSection>
    </div>
  );
}

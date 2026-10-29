import React from "react";
import type { SettingsCategory } from "./settings-types.js";
import { SettingsCategoryIcon } from "./SettingsIcons.js";

export interface SettingsCategoryItem {
  id: SettingsCategory;
  label: string;
  count?: number | undefined;
  badgeTone?: "neutral" | "warning" | "danger" | "success" | undefined;
  icon?: React.ReactNode | undefined;
}

export interface SettingsNavGroup {
  title: string;
  items: SettingsCategoryItem[];
}

export interface SettingsNavigationProps {
  groups?: SettingsNavGroup[] | undefined;
  categories?: SettingsCategoryItem[] | undefined;
  activeCategory: SettingsCategory;
  onSelectCategory: (category: SettingsCategory) => void;
}

export function SettingsNavigation({
  groups,
  categories,
  activeCategory,
  onSelectCategory,
}: SettingsNavigationProps) {
  const navGroups: SettingsNavGroup[] =
    groups ?? (categories ? [{ title: "", items: categories }] : []);

  return (
    <nav className="settings-nav" aria-label="设置分类">
      <div className="settings-nav-groups" role="tablist" aria-label="设置分类列表">
        {navGroups.map((group, groupIdx) => (
          <div key={group.title || groupIdx} className="settings-nav-group" role="presentation">
            {group.title ? (
              <div
                className="settings-nav-group-title"
                id={`settings-nav-group-${groupIdx}`}
                aria-hidden="true"
              >
                {group.title}
              </div>
            ) : null}
            <ul
              className="settings-nav-list"
              role="presentation"
              aria-labelledby={group.title ? `settings-nav-group-${groupIdx}` : undefined}
            >
              {group.items.map((cat) => {
                const isSelected = activeCategory === cat.id;
                return (
                  <li key={cat.id} role="presentation">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={isSelected}
                      aria-current={isSelected ? "page" : undefined}
                      className={`settings-nav-item ${isSelected ? "settings-nav-item--active" : ""}`}
                      onClick={() => onSelectCategory(cat.id)}
                    >
                      <span className="settings-nav-item-start">
                        <span className="settings-nav-icon" aria-hidden="true">
                          {cat.icon ?? <SettingsCategoryIcon category={cat.id} />}
                        </span>
                        <span className="settings-nav-label">{cat.label}</span>
                      </span>
                      {typeof cat.count === "number" && (
                        <span
                          className={`settings-nav-badge ${
                            cat.badgeTone ? `settings-nav-badge--${cat.badgeTone}` : ""
                          }`}
                        >
                          {cat.count}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}

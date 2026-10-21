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

interface SettingsNavigationProps {
  categories: SettingsCategoryItem[];
  activeCategory: SettingsCategory;
  onSelectCategory: (category: SettingsCategory) => void;
}

export function SettingsNavigation({
  categories,
  activeCategory,
  onSelectCategory,
}: SettingsNavigationProps) {
  return (
    <nav className="settings-nav" aria-label="设置分类">
      <ul className="settings-nav-list" role="tablist">
        {categories.map((cat) => {
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
                  <span className={`settings-nav-badge ${cat.badgeTone ? `settings-nav-badge--${cat.badgeTone}` : ""}`}>
                    {cat.count}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

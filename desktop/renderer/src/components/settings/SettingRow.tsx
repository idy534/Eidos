import React from "react";

interface SettingRowProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  meta?: React.ReactNode;
  action?: React.ReactNode;
  children?: React.ReactNode;
  expandableDetails?: React.ReactNode;
  isExpanded?: boolean;
  onToggleExpand?: () => void;
  className?: string;
  disabled?: boolean;
}

export function SettingRow({
  title,
  description,
  meta,
  action,
  children,
  expandableDetails,
  isExpanded,
  onToggleExpand,
  className = "",
  disabled = false,
}: SettingRowProps) {
  return (
    <div className={`setting-row ${disabled ? "setting-row--disabled" : ""} ${className}`}>
      <div className="setting-row-main">
        <div className="setting-row-info">
          <div className="setting-row-title">{title}</div>
          {description && <div className="setting-row-desc">{description}</div>}
          {meta && <div className="setting-row-meta">{meta}</div>}
        </div>
        {action && <div className="setting-row-action">{action}</div>}
      </div>

      {children && <div className="setting-row-body">{children}</div>}

      {expandableDetails && (
        <div className="setting-row-expandable">
          <button
            type="button"
            className="setting-row-expand-btn"
            aria-expanded={isExpanded}
            onClick={onToggleExpand}
          >
            <span>{isExpanded ? "收起技术参数" : "展开技术参数"}</span>
            <span className={`expand-chevron ${isExpanded ? "expand-chevron--open" : ""}`}>▾</span>
          </button>
          {isExpanded && <div className="setting-row-details-content">{expandableDetails}</div>}
        </div>
      )}
    </div>
  );
}

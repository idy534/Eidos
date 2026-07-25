import React from "react";

interface EmptySettingsStateProps {
  icon?: React.ReactNode;
  title: string;
  description: string;
  action?: React.ReactNode;
}

export function EmptySettingsState({
  icon,
  title,
  description,
  action,
}: EmptySettingsStateProps) {
  return (
    <div className="empty-settings-state">
      {icon && <div className="empty-settings-icon">{icon}</div>}
      <h3 className="empty-settings-title">{title}</h3>
      <p className="empty-settings-desc">{description}</p>
      {action && <div className="empty-settings-action">{action}</div>}
    </div>
  );
}

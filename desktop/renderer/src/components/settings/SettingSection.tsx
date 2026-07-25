import React from "react";

interface SettingSectionProps {
  title: string;
  description?: React.ReactNode;
  headerAction?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export function SettingSection({
  title,
  description,
  headerAction,
  children,
  className = "",
}: SettingSectionProps) {
  return (
    <section className={`setting-section ${className}`}>
      <header className="setting-section-header">
        <div className="setting-section-title-group">
          <h2>{title}</h2>
          {description && <div className="setting-section-desc">{description}</div>}
        </div>
        {headerAction && <div className="setting-section-action">{headerAction}</div>}
      </header>
      <div className="setting-section-content">{children}</div>
    </section>
  );
}

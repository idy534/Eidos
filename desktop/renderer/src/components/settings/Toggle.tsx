import React from "react";

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label: string;
  id?: string;
  className?: string;
}

export function Toggle({
  checked,
  onChange,
  disabled = false,
  label,
  id,
  className = "",
}: ToggleProps) {
  return (
    <label
      className={`toggle-switch ${checked ? "toggle-switch--checked" : ""} ${disabled ? "toggle-switch--disabled" : ""} ${className}`}
    >
      <input
        id={id}
        type="checkbox"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        checked={checked}
        disabled={disabled}
        className="toggle-switch__input"
        onChange={(event) => onChange(event.currentTarget.checked)}
      />
      <span className="toggle-track" aria-hidden="true">
        <span className="toggle-thumb" />
      </span>
    </label>
  );
}

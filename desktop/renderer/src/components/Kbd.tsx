import React from "react";

export interface KbdProps {
  children: React.ReactNode;
  className?: string;
  title?: string;
}

export function Kbd({ children, className = "", title }: KbdProps) {
  return (
    <kbd className={`kbd-key ${className}`.trim()} title={title}>
      {children}
    </kbd>
  );
}

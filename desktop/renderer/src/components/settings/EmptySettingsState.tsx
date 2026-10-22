import React from "react";
import { EmptyState, type EmptyStateProps } from "../EmptyState.js";

export type EmptySettingsStateProps = EmptyStateProps;

export function EmptySettingsState(props: EmptySettingsStateProps) {
  return (
    <EmptyState
      {...props}
      className={`empty-settings-state ${props.className ?? ""}`.trim()}
    />
  );
}

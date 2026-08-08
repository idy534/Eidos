import type { Run } from "./domain-contracts.js";


export type ResponseFeedbackValue = "up" | "down";
export type RunRevisionKind = "regenerate" | "edit";

export interface ResponseFeedbackState {
  itemId: string;
  value: ResponseFeedbackValue;
}

export interface RunRevisionState {
  runId: string;
  sourceRunId: string;
  kind: RunRevisionKind;
}

export interface ResponseActionState {
  feedback: ResponseFeedbackState[];
  revisions: RunRevisionState[];
}

export interface ItemFeedbackResult {
  itemId: string;
  feedback?: ResponseFeedbackValue;
}

export interface RunRevisionResult {
  run: Run;
  sourceRunId: string;
  kind: RunRevisionKind;
}

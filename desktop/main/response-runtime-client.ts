import { randomUUID } from "node:crypto";

import type {
  ItemFeedbackResult,
  ResponseActionState,
  ResponseFeedbackValue,
  RunRevisionResult,
} from "../shared/response-actions.js";
import { RuntimeClient } from "./runtime-client.js";


type RuntimeValidator<T> = (value: unknown) => value is T;
type RuntimeClientRequestBoundary = {
  validatedRequest<T>(
    method: string,
    params: Record<string, unknown>,
    validate: RuntimeValidator<T>,
  ): Promise<T>;
};

declare module "./runtime-client.js" {
  interface RuntimeClient {
    readResponseActionState(sessionId: string): Promise<ResponseActionState>;
    setItemFeedback(
      itemId: string,
      feedback: ResponseFeedbackValue | null,
    ): Promise<ItemFeedbackResult>;
    reviseRun(
      sourceRunId: string,
      userInput?: string,
      operationId?: string,
    ): Promise<RunRevisionResult>;
  }
}

function requestBoundary(client: RuntimeClient): RuntimeClientRequestBoundary {
  // RuntimeClient intentionally owns JSON-RPC framing. This feature slice only
  // extends its typed method surface and reuses that existing validated boundary.
  return client as unknown as RuntimeClientRequestBoundary;
}

RuntimeClient.prototype.readResponseActionState = function readResponseActionState(
  sessionId: string,
): Promise<ResponseActionState> {
  return requestBoundary(this).validatedRequest(
    "responseAction/state",
    { sessionId },
    isResponseActionState,
  );
};

RuntimeClient.prototype.setItemFeedback = function setItemFeedback(
  itemId: string,
  feedback: ResponseFeedbackValue | null,
): Promise<ItemFeedbackResult> {
  return requestBoundary(this).validatedRequest(
    "item/setFeedback",
    { itemId, feedback },
    isItemFeedbackResult,
  );
};

RuntimeClient.prototype.reviseRun = function reviseRun(
  sourceRunId: string,
  userInput?: string,
  operationId = randomUUID(),
): Promise<RunRevisionResult> {
  return requestBoundary(this).validatedRequest(
    "run/revise",
    {
      sourceRunId,
      ...(userInput === undefined ? {} : { userInput }),
      operationId,
    },
    isRunRevisionResult,
  );
};

function isResponseActionState(value: unknown): value is ResponseActionState {
  if (!isRecord(value) || !hasOnlyKeys(value, ["feedback", "revisions"])) return false;
  return Array.isArray(value.feedback)
    && value.feedback.every((entry) => (
      isRecord(entry)
      && hasOnlyKeys(entry, ["itemId", "value"])
      && typeof entry.itemId === "string"
      && ["up", "down"].includes(String(entry.value))
    ))
    && Array.isArray(value.revisions)
    && value.revisions.every((entry) => (
      isRecord(entry)
      && hasOnlyKeys(entry, ["runId", "sourceRunId", "kind"])
      && typeof entry.runId === "string"
      && typeof entry.sourceRunId === "string"
      && ["regenerate", "edit"].includes(String(entry.kind))
    ));
}

function isItemFeedbackResult(value: unknown): value is ItemFeedbackResult {
  return isRecord(value)
    && hasOnlyKeys(value, ["itemId", "feedback"])
    && typeof value.itemId === "string"
    && (
      value.feedback === undefined
      || ["up", "down"].includes(String(value.feedback))
    );
}

function isRunRevisionResult(value: unknown): value is RunRevisionResult {
  return isRecord(value)
    && hasOnlyKeys(value, ["run", "sourceRunId", "kind"])
    && isRunLike(value.run)
    && typeof value.sourceRunId === "string"
    && ["regenerate", "edit"].includes(String(value.kind));
}

function isRunLike(value: unknown): boolean {
  return isRecord(value)
    && typeof value.id === "string"
    && typeof value.sessionId === "string"
    && typeof value.modelId === "string"
    && typeof value.status === "string"
    && typeof value.modelStepCount === "number"
    && typeof value.createdAt === "number"
    && typeof value.updatedAt === "number";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const allowed = new Set(keys);
  return Object.keys(value).every((key) => allowed.has(key));
}

import type {
  ApprovalReviewProgressStage,
  RunApprovalReviewProgressParams,
} from "./contracts.js";

export interface LiveApprovalReview {
  itemId: string;
  stage: Exclude<ApprovalReviewProgressStage, "finished">;
  stepIndex: number;
  totalSteps: number;
  actionTitle: string;
  preview?: string | undefined;
  attempt?: number | undefined;
  startedAt: number;
  updatedAt: number;
}

export type LiveApprovalReviewMap = Record<string, LiveApprovalReview>;

export function applyApprovalReviewProgress(
  map: LiveApprovalReviewMap,
  params: RunApprovalReviewProgressParams,
  now = Date.now(),
): LiveApprovalReviewMap {
  const existing = map[params.runId];
  if (params.stage === "finished") {
    if (!existing || existing.itemId !== params.itemId) {
      return map;
    }
    const next = { ...map };
    delete next[params.runId];
    return next;
  }

  if (
    existing
    && existing.itemId === params.itemId
    && existing.stage === params.stage
    && existing.stepIndex === params.stepIndex
    && existing.totalSteps === params.totalSteps
    && existing.actionTitle === params.actionTitle
    && existing.preview === params.preview
    && existing.attempt === params.attempt
  ) {
    return map;
  }

  return {
    ...map,
    [params.runId]: {
      itemId: params.itemId,
      stage: params.stage,
      stepIndex: params.stepIndex,
      totalSteps: params.totalSteps,
      actionTitle: params.actionTitle,
      ...(params.preview === undefined ? {} : { preview: params.preview }),
      ...(params.attempt === undefined ? {} : { attempt: params.attempt }),
      startedAt: existing?.itemId === params.itemId ? existing.startedAt : now,
      updatedAt: now,
    },
  };
}

export function clearApprovalReview(
  map: LiveApprovalReviewMap,
  runId: string,
): LiveApprovalReviewMap {
  if (!(runId in map)) {
    return map;
  }
  const next = { ...map };
  delete next[runId];
  return next;
}

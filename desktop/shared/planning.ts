import type { AnswerInputRequest, InputAnswer, InputQuestion, PlanDocument, PlanEditRequest, PlanningReadResponse, RunPlanningOptions, UserInputRequest, UserInputResponse } from "./planning.generated.js";

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}
function text(value: unknown, max: number, min = 0): value is string {
  return typeof value === "string" && value.length >= min && value.length <= max;
}
function positive(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}
function answer(value: unknown): value is InputAnswer {
  return record(value) && keys(value, ["questionId", "optionIds", "text"])
    && text(value.questionId, 80, 1) && (value.text === undefined || text(value.text, 8000))
    && (value.optionIds === undefined || (Array.isArray(value.optionIds) && value.optionIds.length <= 6 && value.optionIds.every((id) => text(id, 80, 1))));
}
function response(value: unknown): value is UserInputResponse {
  return record(value) && keys(value, ["status", "answers"])
    && ["answered", "skipped"].includes(String(value.status))
    && (value.answers === undefined || (Array.isArray(value.answers) && value.answers.length <= 3 && value.answers.every(answer)));
}
function question(value: unknown): value is InputQuestion {
  return record(value) && keys(value, ["id", "question", "type", "options", "recommendedOptionId"])
    && text(value.id, 80, 1) && text(value.question, 1000, 1)
    && (value.type === undefined || ["single_select", "multi_select", "text"].includes(String(value.type)))
    && (value.recommendedOptionId == null || text(value.recommendedOptionId, 80, 1))
    && (value.options === undefined || (Array.isArray(value.options) && value.options.length <= 6 && value.options.every((option) =>
      record(option) && keys(option, ["id", "label", "description"]) && text(option.id, 80, 1)
      && text(option.label, 200, 1) && (option.description === undefined || text(option.description, 500)))));
}
export function isUserInputRequest(value: unknown): value is UserInputRequest {
  return record(value) && keys(value, ["id", "sessionId", "runId", "itemId", "questions", "status", "response", "createdAt"])
    && [value.id, value.sessionId, value.runId, value.itemId].every((id) => text(id, 128, 1))
    && Array.isArray(value.questions) && value.questions.length >= 1 && value.questions.length <= 3 && value.questions.every(question)
    && ["pending", "answered", "skipped", "canceled"].includes(String(value.status))
    && (value.response == null || response(value.response)) && positive(value.createdAt);
}
export function isPlanDocument(value: unknown): value is PlanDocument {
  return record(value) && keys(value, ["id", "sessionId", "runId", "revision", "title", "markdown", "sha256", "status", "path", "updatedAt", "executionRunId"])
    && [value.id, value.sessionId, value.runId].every((id) => text(id, 128, 1))
    && positive(value.revision) && text(value.title, 200, 1) && text(value.markdown, 65536, 1)
    && typeof value.sha256 === "string" && /^[a-f0-9]{64}$/.test(value.sha256)
    && ["draft", "review", "accepted"].includes(String(value.status)) && text(value.path, 4096, 1)
    && positive(value.updatedAt) && (value.executionRunId == null || text(value.executionRunId, 128, 1));
}
export function isPlanningReadResponse(value: unknown): value is PlanningReadResponse {
  return record(value) && keys(value, ["plans", "questions"])
    && Array.isArray(value.plans) && value.plans.length <= 50 && value.plans.every(isPlanDocument)
    && Array.isArray(value.questions) && value.questions.length <= 50 && value.questions.every(isUserInputRequest);
}
export function isPlanResponse(value: unknown): value is { plan: PlanDocument } {
  return record(value) && keys(value, ["plan"]) && isPlanDocument(value.plan);
}
export function isAnswerInputRequest(value: unknown): value is AnswerInputRequest {
  return record(value) && keys(value, ["requestId", "response"]) && text(value.requestId, 128, 1) && response(value.response);
}
export function isPlanEditRequest(value: unknown): value is PlanEditRequest {
  return record(value) && keys(value, ["planId", "expectedRevision", "markdown"])
    && text(value.planId, 128, 1) && positive(value.expectedRevision) && text(value.markdown, 65536, 1);
}
export function isRunPlanningOptions(value: unknown): value is RunPlanningOptions {
  return record(value) && keys(value, ["workMode", "planId", "planRevision"])
    && (value.workMode === undefined || ["execute", "plan"].includes(String(value.workMode)))
    && (value.planId == null || text(value.planId, 128, 1)) && (value.planRevision == null || positive(value.planRevision))
    && ((value.planId == null) === (value.planRevision == null));
}

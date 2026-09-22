import type { InputReference, InputPrepareRequest, InputPreviewResponse, DraftResponse, InputReadAssetRequest, InputReadAssetResponse } from "./input-context.generated.js";
export type { InputReference, InputPrepareRequest, InputReadAssetRequest, InputReadAssetResponse } from "./input-context.generated.js";
export type InputReferenceKind = InputReference["kind"];
export type InputPreview = InputPreviewResponse;
export type InputDraft = DraftResponse;
export type InputAssetChunk = InputReadAssetResponse;

export const INPUT_KINDS: readonly string[] = ["file", "directory", "image", "skill", "mcp", "plugin", "history", "excerpt"];
export function isInputAssetChunk(value: unknown): value is InputReadAssetResponse {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return Object.keys(v).every((key) => ["id", "data", "mimeType", "sizeBytes", "nextOffset", "complete"].includes(key))
    && typeof v.id === "string" && /^[a-f0-9]{64}$/.test(v.id)
    && typeof v.data === "string"
    && typeof v.mimeType === "string" && v.mimeType.startsWith("image/")
    && Number.isSafeInteger(v.sizeBytes) && Number(v.sizeBytes) >= 0
    && Number.isSafeInteger(v.nextOffset) && Number(v.nextOffset) >= 0
    && typeof v.complete === "boolean";
}
export function isInputReference(value: unknown): value is InputReference {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return Object.keys(v).every((key) => ["id", "kind", "label", "source", "sha256", "status", "size"].includes(key))
    && typeof v.id === "string" && /^[a-f0-9]{64}$/.test(v.id)
    && typeof v.kind === "string" && INPUT_KINDS.includes(v.kind)
    && typeof v.label === "string" && v.label.length > 0 && v.label.length <= 512
    && typeof v.source === "string" && v.source.length <= 4096
    && typeof v.sha256 === "string" && /^[a-f0-9]{64}$/.test(v.sha256)
    && ["content", "location", "selection"].includes(String(v.status))
    && Number.isSafeInteger(v.size) && Number(v.size) >= 0 && Number(v.size) <= 10 * 1024 * 1024;
}
export function isInputDraft(value: unknown): value is InputDraft {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return Object.keys(v).every((key) => ["text", "references"].includes(key)) && typeof v.text === "string"
    && v.text.length <= 65536 && Array.isArray(v.references) && v.references.length <= 20 && v.references.every(isInputReference);
}
export function isInputPreview(value: unknown): value is InputPreview {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return Object.keys(v).every((key) => ["reference", "text", "thumbnail"].includes(key))
    && isInputReference(v.reference) && typeof v.text === "string" && v.text.length <= 65536
    && (v.thumbnail === undefined || (typeof v.thumbnail === "string" && v.thumbnail.startsWith("data:image/jpeg;base64,") && v.thumbnail.length <= 512 * 1024));
}
export function isInputPrepareRequest(value: unknown): value is InputPrepareRequest {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return Object.keys(v).every((key) => ["kind", "source", "label", "text", "itemIds", "pluginId", "startLine", "endLine", "sessionId", "origin"].includes(key))
    && typeof v.kind === "string" && INPUT_KINDS.includes(v.kind)
    && typeof v.source === "string" && v.source.length > 0 && v.source.length <= 4096
    && (v.sessionId === undefined || (typeof v.sessionId === "string" && v.sessionId.length <= 128))
    && (v.origin === undefined || v.origin === "selected" || v.origin === "clipboard")
    && (v.label === undefined || (typeof v.label === "string" && v.label.length <= 512))
    && (v.text === undefined || (v.kind === "excerpt" && typeof v.text === "string" && v.text.length <= 65536))
    && (v.pluginId === undefined || (typeof v.pluginId === "string" && v.pluginId.length <= 256))
    && (v.itemIds === undefined || (Array.isArray(v.itemIds) && v.itemIds.length <= 20 && v.itemIds.every((id) => typeof id === "string" && id.length <= 64)))
    && (v.startLine === undefined || (Number.isSafeInteger(v.startLine) && Number(v.startLine) >= 1))
    && (v.endLine === undefined || (Number.isSafeInteger(v.endLine) && Number(v.endLine) >= Number(v.startLine)));
}

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isInputDraft,
  isInputPrepareRequest,
  isInputPreview,
  isInputReference,
} from "../../shared/input-context.js";

const reference = {
  id: "a".repeat(64),
  kind: "file" as const,
  label: "notes.txt",
  source: "/workspace/notes.txt",
  sha256: "b".repeat(64),
  status: "content" as const,
  size: 12,
};

void test("input contracts accept bounded references, previews, and drafts", () => {
  assert.equal(isInputReference(reference), true);
  assert.equal(isInputPreview({ reference, text: "notes" }), true);
  assert.equal(isInputDraft({ text: "draft", references: [reference] }), true);
  assert.equal(isInputPrepareRequest({
    kind: "file",
    source: reference.source,
    sessionId: "session-1",
    startLine: 2,
    endLine: 3,
  }), true);
});

void test("input contracts reject unknown fields and invalid bounds", () => {
  assert.equal(isInputReference({ ...reference, extra: true }), false);
  assert.equal(isInputReference({ ...reference, id: "not-a-digest" }), false);
  assert.equal(isInputPreview({ reference, text: "text", thumbnail: "data:image/png;base64,AA==" }), false);
  assert.equal(isInputDraft({ text: "draft", references: [{ ...reference, size: 10 * 1024 * 1024 + 1 }] }), false);
  assert.equal(isInputPrepareRequest({ kind: "file", source: reference.source, startLine: 3, endLine: 2 }), false);
  assert.equal(isInputPrepareRequest({ kind: "file", source: reference.source, text: "not an excerpt" }), false);
});

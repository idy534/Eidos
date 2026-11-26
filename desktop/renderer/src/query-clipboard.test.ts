import assert from "node:assert/strict";
import test from "node:test";
import type { InputDraft, InputReference } from "../../shared/input-context.js";
import {
  MAX_DRAFT_REFERENCES,
  MAX_DRAFT_TEXT_LENGTH,
  buildQueryClipboardPayload,
  degradeInvalidQueryReferences,
  formatDegradationNotice,
  formatQueryAsPlainText,
  mergePastedQueryIntoDraft,
  parseQueryClipboardPayload,
  serializeQueryClipboardPayload,
} from "../../shared/query-clipboard.js";

function makeRef(
  overrides: Partial<InputReference> & { idChar: string; kind?: InputReference["kind"] },
): InputReference {
  const { idChar, ...rest } = overrides;
  const kind = rest.kind ?? "file";
  return {
    id: idChar.repeat(64),
    kind,
    label: rest.label ?? `${kind}-${idChar}`,
    source: rest.source ?? `/workspace/${kind}-${idChar}`,
    sha256: idChar.repeat(64),
    status: rest.status ?? (kind === "agent" || kind === "skill" || kind === "plugin" || kind === "mcp" ? "selection" : "content"),
    size: rest.size ?? 16,
  };
}

test("buildQueryClipboardPayload and formatQueryAsPlainText handle inline, shelf, legacy, and reference-only queries", () => {
  const agent = makeRef({ idChar: "1", kind: "agent", label: "researcher", source: "researcher" });
  const skill = makeRef({ idChar: "2", kind: "skill", label: "docx", source: "skills/docx" });
  const file = makeRef({ idChar: "3", kind: "file", label: "report.md", source: "/ws/report.md" });
  const dir = makeRef({ idChar: "4", kind: "directory", label: "docs", source: "/ws/docs", status: "location" });
  const image = makeRef({ idChar: "5", kind: "image", label: "粘贴的图片", source: "clipboard:image" });
  const excerpt = makeRef({ idChar: "6", kind: "excerpt", label: "选中内容", source: "session:s1:selection" });

  // Inline-only preserves exact plain text without leading shelf line.
  const inlineText = `[@researcher](ref:${agent.id.slice(0, 12)}) 调研 [$docx](ref:${skill.id.slice(0, 12)})`;
  assert.equal(formatQueryAsPlainText(inlineText, [agent, skill]), "@researcher 调研 $docx");

  // Unplaced legacy inline reference is canonicalized.
  assert.equal(formatQueryAsPlainText("分析一下", [agent]), "@researcher 分析一下");

  // Shelf + inline formats shelf descriptors on the first line.
  const mixedPayload = buildQueryClipboardPayload(inlineText, [file, dir, image, excerpt, agent, skill]);
  assert.equal(mixedPayload.references.length, 6);
  assert.equal(
    formatQueryAsPlainText(mixedPayload.text, mixedPayload.references),
    "[文件: report.md] [文件夹: docs] [图片: 粘贴的图片] [引用: 选中内容]\n@researcher 调研 $docx",
  );

  // References-only query produces shelf summary without trailing newline.
  assert.equal(formatQueryAsPlainText("", [file, image]), "[文件: report.md] [图片: 粘贴的图片]");
});

test("serializeQueryClipboardPayload and parseQueryClipboardPayload validate bounds and reject malformed inputs", () => {
  const agent = makeRef({ idChar: "a", kind: "agent", label: "analyst", source: "analyst" });
  const payload = buildQueryClipboardPayload(`[@analyst](ref:${agent.id.slice(0, 12)}) hi`, [agent]);
  const raw = serializeQueryClipboardPayload(payload);
  assert.deepEqual(parseQueryClipboardPayload(raw), payload);

  assert.equal(parseQueryClipboardPayload(""), undefined);
  assert.equal(parseQueryClipboardPayload("not json"), undefined);
  assert.equal(parseQueryClipboardPayload(JSON.stringify({ version: 2, text: "hi", references: [] })), undefined);
  assert.equal(parseQueryClipboardPayload(JSON.stringify({ ...payload, extra: true })), undefined);

  const tooManyRefs = Array.from({ length: MAX_DRAFT_REFERENCES + 1 }, (_, i) =>
    makeRef({ idChar: i.toString(16).padStart(1, "0").slice(-1) === "" ? "0" : "a", source: `/ws/${i}` }),
  );
  // Make distinct valid 64-hex ids
  const distinct21 = tooManyRefs.map((ref, i) => ({
    ...ref,
    id: i.toString(16).padStart(64, "0"),
  }));
  assert.equal(
    parseQueryClipboardPayload(JSON.stringify({ version: 1, text: "", references: distinct21 })),
    undefined,
  );
});

test("degradeInvalidQueryReferences converts invalid inline tokens and shelf cards into plain text", () => {
  const validAgent = makeRef({ idChar: "1", kind: "agent", label: "researcher", source: "researcher" });
  const deletedSkill = makeRef({ idChar: "2", kind: "skill", label: "old-skill", source: "skills/old" });
  const missingFile = makeRef({ idChar: "3", kind: "file", label: "gone.txt", source: "/ws/gone.txt" });
  const validHistory = makeRef({ idChar: "4", kind: "history", label: "旧对话", source: "sess-old" });

  const payload = buildQueryClipboardPayload(
    `[@researcher](ref:${validAgent.id.slice(0, 12)}) 使用 [$old-skill](ref:${deletedSkill.id.slice(0, 12)}) 和 [@旧对话](ref:${validHistory.id.slice(0, 12)})`,
    [missingFile, validAgent, deletedSkill, validHistory],
  );

  const degraded = degradeInvalidQueryReferences(
    payload,
    new Set([deletedSkill.id, missingFile.id]),
  );

  assert.equal(degraded.degradedCount, 2);
  assert.deepEqual(degraded.references, [validAgent, validHistory]);
  assert.equal(
    degraded.text,
    `[文件: gone.txt]\n[@researcher](ref:${validAgent.id.slice(0, 12)}) 使用 $old-skill 和 [@旧对话](ref:${validHistory.id.slice(0, 12)})`,
  );
});

test("mergePastedQueryIntoDraft supports mid-text insertion, replacement, dedup remapping, overflow, and text bounds", () => {
  const existingAgent = makeRef({ idChar: "a", kind: "agent", label: "researcher", source: "researcher" });
  const incomingSameAgentDiffId = makeRef({
    idChar: "b",
    kind: "agent",
    label: "researcher",
    source: "researcher",
    sha256: "c".repeat(64),
  });
  const replacedSkill = makeRef({ idChar: "d", kind: "skill", label: "xlsx", source: "skills/xlsx" });
  const newFile = makeRef({ idChar: "e", kind: "file", label: "data.csv", source: "/ws/data.csv" });

  const draft: InputDraft = {
    text: `请用 [$xlsx](ref:${replacedSkill.id.slice(0, 12)}) 完成`,
    references: [existingAgent, replacedSkill],
  };

  // Note: existingAgent is unplaced in draft.text, so canonicalizeInlineText prefixes it!
  const canonicalDraft = `[@researcher](ref:${existingAgent.id.slice(0, 12)}) 请用 [$xlsx](ref:${replacedSkill.id.slice(0, 12)}) 完成`;
  const replaceStart = canonicalDraft.indexOf("[$xlsx]");
  const replaceEnd = replaceStart + `[$xlsx](ref:${replacedSkill.id.slice(0, 12)})`.length;

  const merged = mergePastedQueryIntoDraft(
    draft,
    {
      text: `[@researcher](ref:${incomingSameAgentDiffId.id.slice(0, 12)})`,
      references: [incomingSameAgentDiffId, newFile],
    },
    { start: replaceStart, end: replaceEnd },
  );

  assert.equal(merged.overflowDegradedCount, 0);
  // replacedSkill was replaced by selection, existingAgent was kept & remapped, newFile was added
  assert.deepEqual(merged.draft.references, [existingAgent, newFile]);
  assert.equal(
    merged.draft.text,
    `[@researcher](ref:${existingAgent.id.slice(0, 12)}) 请用 [@researcher](ref:${existingAgent.id.slice(0, 12)}) 完成`,
  );
  assert.equal(
    merged.caretOffset,
    replaceStart + `[@researcher](ref:${existingAgent.id.slice(0, 12)})`.length,
  );

  // Overflow over 20 references degrades only the overflow items
  const fullDraftRefs = Array.from({ length: 19 }, (_, i) => ({
    id: (i + 1).toString(16).padStart(64, "0"),
    kind: "file" as const,
    label: `f${i}.txt`,
    source: `/ws/f${i}.txt`,
    sha256: "0".repeat(64),
    status: "content" as const,
    size: 10,
  }));
  const extraFile1 = {
    id: "f".repeat(63) + "1",
    kind: "file" as const,
    label: "fits.txt",
    source: "/ws/fits.txt",
    sha256: "0".repeat(64),
    status: "content" as const,
    size: 10,
  };
  const extraAgent2 = makeRef({ idChar: "9", kind: "agent", label: "extra-agent", source: "extra-agent" });
  const extraFile3 = {
    id: "f".repeat(63) + "3",
    kind: "file" as const,
    label: "overflow.txt",
    source: "/ws/overflow.txt",
    sha256: "0".repeat(64),
    status: "content" as const,
    size: 10,
  };

  const overflowResult = mergePastedQueryIntoDraft(
    { text: "已有：", references: fullDraftRefs },
    {
      text: `加一个 [@extra-agent](ref:${extraAgent2.id.slice(0, 12)})`,
      references: [extraFile1, extraAgent2, extraFile3],
    },
  );
  assert.equal(overflowResult.draft.references.length, MAX_DRAFT_REFERENCES);
  assert.equal(overflowResult.overflowDegradedCount, 2);
  assert.equal(
    overflowResult.draft.text,
    "已有：[文件: overflow.txt]\n加一个 @extra-agent",
  );

  // Max text length bound does not split inside an inline token or leak broken syntax
  const agentToken = `[@researcher](ref:${existingAgent.id.slice(0, 12)})`;
  const longDraft: InputDraft = {
    text: `${"a".repeat(MAX_DRAFT_TEXT_LENGTH - 10)})`,
    references: [],
  };
  const boundedAtBoundary = mergePastedQueryIntoDraft(
    longDraft,
    {
      text: `12345${agentToken}`,
      references: [existingAgent],
    },
    { start: MAX_DRAFT_TEXT_LENGTH - 10, end: MAX_DRAFT_TEXT_LENGTH - 10 },
  );
  assert.ok(boundedAtBoundary.draft.text.length <= MAX_DRAFT_TEXT_LENGTH);
  assert.equal(boundedAtBoundary.draft.text, `${"a".repeat(MAX_DRAFT_TEXT_LENGTH - 10)}12345)`);
  assert.deepEqual(boundedAtBoundary.draft.references, []);

  // buildQueryClipboardPayload drops truncated inline references when canonical text exceeds limit
  const oversizedPayload = buildQueryClipboardPayload(
    `${"b".repeat(MAX_DRAFT_TEXT_LENGTH - 5)}${agentToken}`,
    [existingAgent],
  );
  assert.equal(oversizedPayload.text.length <= MAX_DRAFT_TEXT_LENGTH, true);
  assert.deepEqual(oversizedPayload.references, []);

  // Misaligned selection range inside an existing inline token snaps to token boundaries
  const snappedDraft: InputDraft = {
    text: `前${agentToken}后`,
    references: [existingAgent],
  };
  const midTokenOffset = 1 + Math.floor(agentToken.length / 2);
  const snapped = mergePastedQueryIntoDraft(
    snappedDraft,
    { text: "新", references: [] },
    { start: midTokenOffset, end: midTokenOffset },
  );
  assert.equal(snapped.draft.text, `前${agentToken}新后`);
  assert.deepEqual(snapped.draft.references, [existingAgent]);
});

test("formatDegradationNotice returns accurate messages for all degradation combinations", () => {
  assert.equal(formatDegradationNotice(0, 0), undefined);
  assert.equal(formatDegradationNotice(1, 0), "部分引用已更新或不可用，已降级为纯文本。");
  assert.equal(formatDegradationNotice(0, 2), "每轮最多添加 20 个引用，超出部分已降级为纯文本。");
  assert.equal(
    formatDegradationNotice(1, 1),
    "部分引用已更新、不可用或超过 20 个上限，已降级为纯文本。",
  );
});

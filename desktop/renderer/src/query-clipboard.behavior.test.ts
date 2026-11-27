import { createHash } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { InputReference } from "../../shared/input-context.js";
import {
  QUERY_CLIPBOARD_MIME,
  buildQueryClipboardPayload,
  serializeQueryClipboardPayload,
} from "../../shared/query-clipboard.js";
import {
  clearQueryClipboardMirror,
  consumeRecentShiftPaste,
  extractSelectedQueryFromBubble,
  markShiftPasteIfMatching,
  readQueryFromClipboardEvent,
  resolvePastedQueryReferences,
  writeQueryToClipboard,
} from "./query-clipboard.js";

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function makeReference(
  overrides: Partial<InputReference> & { id: string; kind: InputReference["kind"] },
): InputReference {
  return {
    label: overrides.label ?? overrides.kind,
    source: overrides.source ?? `src:${overrides.kind}`,
    sha256: overrides.sha256 ?? "a".repeat(64),
    status: overrides.status ?? "content",
    size: overrides.size ?? 16,
    ...overrides,
  };
}

function makeClipboardEvent(initial: Record<string, string> = {}) {
  const store = new Map<string, string>(Object.entries(initial));
  const preventDefault = vi.fn();
  return {
    preventDefault,
    clipboardData: {
      getData: (type: string) => store.get(type) ?? "",
      setData: (type: string, data: string) => {
        store.set(type, data);
      },
      files: [] as unknown as FileList,
    },
    store,
  };
}

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");

describe("query-clipboard renderer mirror and A1 validation", () => {
  beforeEach(() => {
    clearQueryClipboardMirror();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    clearQueryClipboardMirror();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
    if (clipboardDescriptor) Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
    else delete (navigator as Partial<Navigator>).clipboard;
  });

  it("writes structured clipboard payload, reads via mirror or MIME, and invalidates mirror when external plain text changes", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });

    const fileRef = makeReference({ id: "1".repeat(64), kind: "file", label: "notes.md", source: "/ws/notes.md" });
    const agentRef = makeReference({ id: "2".repeat(64), kind: "agent", label: "researcher", source: "researcher", status: "selection" });

    const wrote = await writeQueryToClipboard(
      `[@researcher](ref:${agentRef.id.slice(0, 12)}) 总结`,
      [fileRef, agentRef],
    );
    expect(wrote).toBe(true);
    expect(writeText).toHaveBeenCalledWith("[文件: notes.md]\n@researcher 总结");

    // Matching plain text resolves via clipboard mirror
    const matchingPaste = makeClipboardEvent({ "text/plain": "[文件: notes.md]\n@researcher 总结" });
    const fromMirror = readQueryFromClipboardEvent(matchingPaste);
    expect(fromMirror?.references.map((r) => r.id)).toEqual([fileRef.id, agentRef.id]);

    // External copy changed text/plain -> mirror invalidated
    const modifiedPaste = makeClipboardEvent({ "text/plain": "something else" });
    expect(readQueryFromClipboardEvent(modifiedPaste)).toBeUndefined();

    // Custom MIME takes precedence and Shift+Paste bypasses structured restoration
    const mimePaste = makeClipboardEvent({
      "text/plain": "ignored",
      [QUERY_CLIPBOARD_MIME]: serializeQueryClipboardPayload(buildQueryClipboardPayload("hi", [fileRef])),
    });
    expect(readQueryFromClipboardEvent(mimePaste)?.references).toEqual([fileRef]);

    markShiftPasteIfMatching({ ctrlKey: true, metaKey: false, shiftKey: true, key: "V" });
    expect(readQueryFromClipboardEvent(mimePaste, { preferPlainText: consumeRecentShiftPaste() })).toBeUndefined();
    expect(consumeRecentShiftPaste()).toBe(false);
  });

  it("validates all 9 reference kinds under Strategy A1 and degrades missing, disabled, or updated references", async () => {
    const agentVer = "b".repeat(64);
    const skillRawHash = "skill-hash-v1";
    const pluginRawHash = "plugin-hash-v1";

    const validFile = makeReference({ id: "01".repeat(32), kind: "file", label: "a.ts", source: "/ws/a.ts" });
    const missingFile = makeReference({ id: "02".repeat(32), kind: "file", label: "gone.ts", source: "/ws/gone.ts" });
    const validDir = makeReference({ id: "03".repeat(32), kind: "directory", label: "src", source: "/ws/src", status: "location" });
    const validImg = makeReference({ id: "04".repeat(32), kind: "image", label: "粘贴的图片", source: "clipboard:image" });
    const validExcerpt = makeReference({ id: "05".repeat(32), kind: "excerpt", label: "选中内容", source: "session:s1:selection" });
    const validHistory = makeReference({ id: "06".repeat(32), kind: "history", label: "历史1", source: "sess-alive" });
    const deletedHistory = makeReference({ id: "07".repeat(32), kind: "history", label: "已删会话", source: "sess-gone" });
    const validAgent = makeReference({ id: "08".repeat(32), kind: "agent", label: "researcher", source: "researcher", sha256: agentVer, status: "selection" });
    const updatedAgent = makeReference({ id: "09".repeat(32), kind: "agent", label: "analyst", source: "analyst", sha256: "old".padEnd(64, "0"), status: "selection" });
    const validSkill = makeReference({ id: "0a".repeat(32), kind: "skill", label: "docx", source: "skills/docx", sha256: sha256(skillRawHash), status: "selection" });
    const disabledSkill = makeReference({ id: "0b".repeat(32), kind: "skill", label: "pdf", source: "skills/pdf", sha256: sha256("pdf-h"), status: "selection" });
    const validPlugin = makeReference({ id: "0c".repeat(32), kind: "plugin", label: "chrome", source: "plugin-chrome", sha256: sha256(pluginRawHash), status: "selection" });
    const removedPlugin = makeReference({ id: "0d".repeat(32), kind: "plugin", label: "legacy", source: "plugin-legacy", sha256: sha256("leg"), status: "selection" });
    const validMcp = makeReference({ id: "0e".repeat(32), kind: "mcp", label: "github", source: "github", sha256: "e".repeat(64), status: "selection" });
    const updatedMcp = makeReference({ id: "0f".repeat(32), kind: "mcp", label: "slack", source: "slack", sha256: "f".repeat(64), status: "selection" });

    const allRefs = [
      validFile, missingFile, validDir, validImg, validExcerpt,
      validHistory, deletedHistory,
      validAgent, updatedAgent,
      validSkill, disabledSkill,
      validPlugin, removedPlugin,
      validMcp, updatedMcp,
    ];
    const refById = new Map(allRefs.map((ref) => [ref.id, ref]));

    const api = {
      readInput: vi.fn(async (id: string) => {
        if (id === missingFile.id) throw new Error("RESOURCE_NOT_FOUND");
        const matched = refById.get(id);
        if (!matched) throw new Error("RESOURCE_NOT_FOUND");
        return { reference: matched, text: "ok" };
      }),
      readSession: vi.fn(async (sessionId: string) => {
        if (sessionId === "sess-gone") throw new Error("RESOURCE_NOT_FOUND");
        return { session: { id: sessionId } };
      }),
      listAgentTypes: vi.fn().mockResolvedValue({
        agents: [
          { name: "researcher", source: "researcher.toml", enabled: true, available: true, definitionVersion: agentVer },
          { name: "analyst", source: "analyst.toml", enabled: true, available: true, definitionVersion: "new".padEnd(64, "1") },
        ],
      }),
      listSkills: vi.fn().mockResolvedValue({
        skills: [
          { qualifiedId: "skills/docx", name: "docx", enabled: true, available: true, contentHash: skillRawHash },
          { qualifiedId: "skills/pdf", name: "pdf", enabled: false, available: true, contentHash: "pdf-h" },
        ],
      }),
      listPlugins: vi.fn().mockResolvedValue({
        plugins: [
          { id: "plugin-chrome", name: "chrome", enabled: true, status: "installed", contentHash: pluginRawHash },
          { id: "plugin-legacy", name: "legacy", enabled: true, status: "removed", contentHash: "leg" },
        ],
      }),
      listMcpServers: vi.fn().mockResolvedValue({
        servers: [
          { serverId: "github", pluginId: "p-gh", available: true, consented: true },
          { serverId: "slack", pluginId: "p-sl", available: true, consented: true },
        ],
      }),
      prepareInput: vi.fn(async (req: { kind: string; source: string; pluginId?: string }) => {
        if (req.kind === "mcp" && req.source === "github") return validMcp;
        if (req.kind === "mcp" && req.source === "slack") {
          return { ...updatedMcp, id: "9".repeat(64), sha256: "8".repeat(64) };
        }
        throw new Error("unexpected prepareInput");
      }),
    };
    Object.defineProperty(window, "eidosRuntime", { configurable: true, writable: true, value: api });

    const text = [
      `[@researcher](ref:${validAgent.id.slice(0, 12)})`,
      `[@analyst](ref:${updatedAgent.id.slice(0, 12)})`,
      `[$docx](ref:${validSkill.id.slice(0, 12)})`,
      `[$pdf](ref:${disabledSkill.id.slice(0, 12)})`,
      `[@chrome](ref:${validPlugin.id.slice(0, 12)})`,
      `[@legacy](ref:${removedPlugin.id.slice(0, 12)})`,
      `[@github](ref:${validMcp.id.slice(0, 12)})`,
      `[@slack](ref:${updatedMcp.id.slice(0, 12)})`,
      `[@历史1](ref:${validHistory.id.slice(0, 12)})`,
      `[@已删会话](ref:${deletedHistory.id.slice(0, 12)})`,
    ].join(" ");

    const resolved = await resolvePastedQueryReferences(buildQueryClipboardPayload(text, allRefs));

    expect(resolved.degradedCount).toBe(6);
    expect(resolved.references.map((r) => r.id)).toEqual([
      validFile.id,
      validDir.id,
      validImg.id,
      validExcerpt.id,
      validHistory.id,
      validAgent.id,
      validSkill.id,
      validPlugin.id,
      validMcp.id,
    ]);
    expect(resolved.text).toContain("[文件: gone.ts]");
    expect(resolved.text).toContain("@analyst");
    expect(resolved.text).toContain("$pdf");
    expect(resolved.text).toContain("@legacy");
    expect(resolved.text).toContain("@slack");
    expect(resolved.text).toContain("@已删会话");
    expect(resolved.text).toContain(`[@researcher](ref:${validAgent.id.slice(0, 12)})`);
    expect(resolved.text).toContain(`[$docx](ref:${validSkill.id.slice(0, 12)})`);
    expect(resolved.text).toContain(`[@github](ref:${validMcp.id.slice(0, 12)})`);
  });

  it("skips validation for exemptReferenceIds", async () => {
    const readInput = vi.fn().mockRejectedValue(new Error("should not be called"));
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: { readInput },
    });
    const exempt = makeReference({ id: "a".repeat(64), kind: "agent", label: "kept", source: "kept" });
    const resolved = await resolvePastedQueryReferences(
      buildQueryClipboardPayload(`[@kept](ref:${exempt.id.slice(0, 12)})`, [exempt]),
      { exemptReferenceIds: new Set([exempt.id]) },
    );
    expect(readInput).not.toHaveBeenCalled();
    expect(resolved.degradedCount).toBe(0);
    expect(resolved.references).toEqual([exempt]);
  });

  it("matches clipboard mirror across Windows CRLF line endings", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const fileRef = makeReference({ id: "10".repeat(32), kind: "file", label: "notes.md", source: "/ws/notes.md" });
    const agentRef = makeReference({ id: "20".repeat(32), kind: "agent", label: "researcher", source: "researcher", status: "selection" });

    await writeQueryToClipboard(
      `[@researcher](ref:${agentRef.id.slice(0, 12)}) 第一行\n第二行`,
      [fileRef, agentRef],
    );

    const windowsPaste = makeClipboardEvent({
      "text/plain": "[文件: notes.md]\r\n@researcher 第一行\r\n第二行",
    });
    const restored = readQueryFromClipboardEvent(windowsPaste);
    expect(restored?.references.map((r) => r.id)).toEqual([fileRef.id, agentRef.id]);
  });

  it("rejects tampered MIME payloads whose readInput authoritative reference does not match", async () => {
    const authoritativeSkill = makeReference({
      id: "30".repeat(32),
      kind: "skill",
      label: "docx",
      source: "skills/docx",
      status: "selection",
    });
    const forgedFileClaim: InputReference = {
      ...authoritativeSkill,
      kind: "file",
      label: "secret.txt",
      source: "/etc/passwd",
      status: "content",
    };
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: {
        readInput: vi.fn().mockResolvedValue({ reference: authoritativeSkill, text: "docx" }),
      },
    });

    const resolved = await resolvePastedQueryReferences(
      buildQueryClipboardPayload("检查一下", [forgedFileClaim]),
    );
    expect(resolved.degradedCount).toBe(1);
    expect(resolved.references).toEqual([]);
    expect(resolved.text).toBe("[文件: secret.txt]\n检查一下");
  });

  it("does not include an adjacent shelf card when selection ends at offset 0 of that card", () => {
    const file1 = makeReference({ id: "40".repeat(32), kind: "file", label: "one.md", source: "/ws/one.md" });
    const file2 = makeReference({ id: "50".repeat(32), kind: "file", label: "two.md", source: "/ws/two.md" });
    const bubble = document.createElement("div");
    bubble.innerHTML =
      `<div data-shelf-ref-id="${file1.id}"><button>one.md</button></div>`
      + `<div data-shelf-ref-id="${file2.id}"><button>two.md</button></div>`
      + `<p class="eidos-chat-prose">正文</p>`;
    document.body.appendChild(bubble);
    try {
      const card1 = bubble.querySelectorAll<HTMLElement>("[data-shelf-ref-id]")[0]!;
      const card2 = bubble.querySelectorAll<HTMLElement>("[data-shelf-ref-id]")[1]!;
      const range = document.createRange();
      range.setStart(card1.querySelector("button")!.firstChild!, 0);
      range.setEnd(card2, 0);
      const selection = window.getSelection()!;
      selection.removeAllRanges();
      selection.addRange(range);

      const extracted = extractSelectedQueryFromBubble(bubble, "正文", [file1, file2]);
      expect(extracted?.references).toEqual([file1]);
    } finally {
      window.getSelection()?.removeAllRanges();
      bubble.remove();
    }
  });

  it("extracts selections when range endpoints land on descendant Element nodes (buttons, icons, images, and markdown prose)", () => {
    const file1 = makeReference({ id: "41".repeat(32), kind: "file", label: "one.md", source: "/ws/one.md" });
    const file2 = makeReference({ id: "42".repeat(32), kind: "file", label: "two.md", source: "/ws/two.md" });
    const img1 = makeReference({ id: "43".repeat(32), kind: "image", label: "粘贴的图片", source: "clipboard:image1" });
    const img2 = makeReference({ id: "44".repeat(32), kind: "image", label: "第二张图", source: "clipboard:image2" });
    const agent1 = makeReference({ id: "45".repeat(32), kind: "agent", label: "researcher", source: "researcher", status: "selection" });
    const agent2 = makeReference({ id: "46".repeat(32), kind: "agent", label: "analyst", source: "analyst", status: "selection" });

    // 1. Shelf card endpoints on descendant <button> / .input-reference__icon offset 0
    const shelfBubble = document.createElement("div");
    shelfBubble.innerHTML =
      `<div data-shelf-ref-id="${file1.id}"><button><span class="input-reference__icon"><svg></svg></span><span class="input-reference__label">one.md</span></button></div>`
      + `<div data-shelf-ref-id="${file2.id}"><button><span class="input-reference__icon"><svg></svg></span><span class="input-reference__label">two.md</span></button></div>`
      + `<p class="eidos-chat-prose">正文</p>`;
    document.body.appendChild(shelfBubble);
    try {
      const card1 = shelfBubble.querySelectorAll<HTMLElement>("[data-shelf-ref-id]")[0]!;
      const card2 = shelfBubble.querySelectorAll<HTMLElement>("[data-shelf-ref-id]")[1]!;
      const selection = window.getSelection()!;

      const fromButton = document.createRange();
      fromButton.setStart(card1.querySelector("button")!, 0);
      fromButton.setEnd(card2.querySelector("button")!, 0);
      selection.removeAllRanges();
      selection.addRange(fromButton);
      expect(extractSelectedQueryFromBubble(shelfBubble, "正文", [file1, file2])?.references).toEqual([file1]);

      const fromIcon = document.createRange();
      fromIcon.setStart(card1.querySelector(".input-reference__icon")!, 0);
      fromIcon.setEnd(card2.querySelector(".input-reference__icon")!, 0);
      selection.removeAllRanges();
      selection.addRange(fromIcon);
      expect(extractSelectedQueryFromBubble(shelfBubble, "正文", [file1, file2])?.references).toEqual([file1]);
    } finally {
      window.getSelection()?.removeAllRanges();
      shelfBubble.remove();
    }

    // 2. Image shelf card endpoints covering thumbnail before .sr-only label and adjacent image boundary 0
    const imgBubble = document.createElement("div");
    imgBubble.innerHTML =
      `<div data-shelf-ref-id="${img1.id}"><button><span class="input-reference__icon"><img src="data:image/png;base64,AA==" alt="1" /></span><span class="sr-only">粘贴的图片</span></button></div>`
      + `<div data-shelf-ref-id="${img2.id}"><button><span class="input-reference__icon"><img src="data:image/png;base64,BB==" alt="2" /></span><span class="sr-only">第二张图</span></button></div>`
      + `<p class="eidos-chat-prose">看看截图</p>`;
    document.body.appendChild(imgBubble);
    try {
      const imgCard1 = imgBubble.querySelectorAll<HTMLElement>("[data-shelf-ref-id]")[0]!;
      const imgCard2 = imgBubble.querySelectorAll<HTMLElement>("[data-shelf-ref-id]")[1]!;
      const imgProse = imgBubble.querySelector<HTMLElement>(".eidos-chat-prose")!;
      const selection = window.getSelection()!;

      // Thumbnail-only selection: (button, 0) -> (button, 1) encloses <img> before .sr-only text
      const thumbOnly = document.createRange();
      thumbOnly.setStart(imgCard1.querySelector("button")!, 0);
      thumbOnly.setEnd(imgCard1.querySelector("button")!, 1);
      selection.removeAllRanges();
      selection.addRange(thumbOnly);
      expect(extractSelectedQueryFromBubble(imgBubble, "看看截图", [img1, img2])?.references).toEqual([img1]);

      // Stopping at imgCard2's button offset 0 excludes imgCard2
      const iconToSecondBtn = document.createRange();
      iconToSecondBtn.setStart(imgCard1.querySelector(".input-reference__icon")!, 0);
      iconToSecondBtn.setEnd(imgCard2.querySelector("button")!, 0);
      selection.removeAllRanges();
      selection.addRange(iconToSecondBtn);
      expect(extractSelectedQueryFromBubble(imgBubble, "看看截图", [img1, img2])?.references).toEqual([img1]);

      // Selecting from imgCard2's iconSpan offset 0 into prose keeps img2 and prose slice
      const iconToProse = document.createRange();
      iconToProse.setStart(imgCard2.querySelector(".input-reference__icon")!, 0);
      iconToProse.setEnd(imgProse.firstChild!, 2);
      selection.removeAllRanges();
      selection.addRange(iconToProse);
      const fromImgAndProse = extractSelectedQueryFromBubble(imgBubble, "看看截图", [img1, img2]);
      expect(fromImgAndProse?.references).toEqual([img2]);
      expect(fromImgAndProse?.text).toBe("看看");
    } finally {
      window.getSelection()?.removeAllRanges();
      imgBubble.remove();
    }

    // 3. Inline pill endpoints on descendant <button> / .input-reference__icon offset 0
    const raw1 = `[@researcher](ref:${agent1.id.slice(0, 12)})`;
    const raw2 = `[@analyst](ref:${agent2.id.slice(0, 12)})`;
    const canonicalContent = `${raw1} 分析一下 ${raw2}`;
    const inlineBubble = document.createElement("div");
    inlineBubble.innerHTML =
      `<p class="eidos-chat-prose eidos-inline-reference-prose">`
      + `<span data-inline-ref-id="${agent1.id}" data-inline-ref-raw="${raw1}" data-inline-ref-label="researcher"><button><span class="input-reference__icon"><svg></svg></span><span class="input-reference__label">researcher</span></button></span>`
      + ` 分析一下 `
      + `<span data-inline-ref-id="${agent2.id}" data-inline-ref-raw="${raw2}" data-inline-ref-label="analyst"><button><span class="input-reference__icon"><svg></svg></span><span class="input-reference__label">analyst</span></button></span>`
      + `</p>`;
    document.body.appendChild(inlineBubble);
    try {
      const pills = inlineBubble.querySelectorAll<HTMLElement>("[data-inline-ref-raw]");
      const middleText = pills[0]!.nextSibling!;
      const selection = window.getSelection()!;

      const fromPillBtn = document.createRange();
      fromPillBtn.setStart(pills[0]!.querySelector("button")!, 0);
      fromPillBtn.setEnd(pills[1]!.querySelector("button")!, 0);
      selection.removeAllRanges();
      selection.addRange(fromPillBtn);
      const res1 = extractSelectedQueryFromBubble(inlineBubble, canonicalContent, [agent1, agent2]);
      expect(res1?.references).toEqual([agent1]);
      expect(res1?.text).toBe(`${raw1} 分析一下 `);

      const fromPillIcon = document.createRange();
      fromPillIcon.setStart(pills[0]!.querySelector(".input-reference__icon")!, 0);
      fromPillIcon.setEnd(middleText, 4);
      selection.removeAllRanges();
      selection.addRange(fromPillIcon);
      const res2 = extractSelectedQueryFromBubble(inlineBubble, canonicalContent, [agent1, agent2]);
      expect(res2?.references).toEqual([agent1]);
      expect(res2?.text).toBe(`${raw1} 分析一`);
    } finally {
      window.getSelection()?.removeAllRanges();
      inlineBubble.remove();
    }

    // 4. Steering / Markdown prose (<div class="markdown-body eidos-chat-prose"><p>...</p></div>) paired with shelf card
    const steeringBubble = document.createElement("div");
    steeringBubble.innerHTML =
      `<div data-shelf-ref-id="${file1.id}"><button><span class="input-reference__icon"><svg></svg></span><span>one.md</span></button></div>`
      + `<div class="markdown-body eidos-chat-prose"><p>引导内容</p></div>`;
    document.body.appendChild(steeringBubble);
    try {
      const shelfBtn = steeringBubble.querySelector<HTMLElement>("[data-shelf-ref-id] button")!;
      const p = steeringBubble.querySelector<HTMLElement>(".eidos-chat-prose p")!;
      const selection = window.getSelection()!;

      const cardToPara = document.createRange();
      cardToPara.setStart(shelfBtn, 0);
      cardToPara.setEnd(p, 1);
      selection.removeAllRanges();
      selection.addRange(cardToPara);
      const extracted1 = extractSelectedQueryFromBubble(steeringBubble, "引导内容", [file1]);
      expect(extracted1?.references).toEqual([file1]);
      expect(extracted1?.text).toBe("引导内容");

      // Also verify starting inside <p> offset 0 when card is trailing (covered across <p> -> card)
      const trailingBubble = document.createElement("div");
      trailingBubble.innerHTML =
        `<div class="markdown-body eidos-chat-prose"><p>引导内容</p></div>`
        + `<div data-shelf-ref-id="${file1.id}"><button>one.md</button></div>`;
      document.body.appendChild(trailingBubble);
      try {
        const trailingP = trailingBubble.querySelector<HTMLElement>(".eidos-chat-prose p")!;
        const trailingBtn = trailingBubble.querySelector<HTMLElement>("[data-shelf-ref-id] button")!;
        const paraToCard = document.createRange();
        paraToCard.setStart(trailingP, 0);
        paraToCard.setEnd(trailingBtn, 1);
        selection.removeAllRanges();
        selection.addRange(paraToCard);
        const extracted2 = extractSelectedQueryFromBubble(trailingBubble, "引导内容", [file1]);
        expect(extracted2?.references).toEqual([file1]);
        expect(extracted2?.text).toBe("引导内容");
      } finally {
        trailingBubble.remove();
      }
    } finally {
      window.getSelection()?.removeAllRanges();
      steeringBubble.remove();
    }
  });
});

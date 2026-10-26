import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useInputDrafts } from "./useInputDrafts.js";
import type { EidosRuntimeAPI } from "../contracts.js";
import type { InputDraft, InputReference } from "../../../shared/input-context.js";

const reference: InputReference = {
  id: "a".repeat(64),
  kind: "file",
  label: "notes.txt",
  source: "/workspace/notes.txt",
  sha256: "b".repeat(64),
  status: "content",
  size: 12,
};
const replacementReference: InputReference = {
  ...reference,
  id: "c".repeat(64),
  sha256: "d".repeat(64),
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useInputDrafts behavior", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupRuntime(overrides: Record<string, unknown> = {}) {
    const api = {
      readInputDraft: vi.fn().mockResolvedValue({ text: "remote draft", references: [reference] }),
      writeInputDraft: vi.fn().mockResolvedValue({ text: "", references: [] }),
      ...overrides,
    } as unknown as EidosRuntimeAPI;
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: api,
    });
    return api;
  }

  it("hydrates a draft and replaces duplicate references by source", async () => {
    const api = setupRuntime();
    const { result } = renderHook(() => useInputDrafts("session-1", true));

    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.drafts["session-1"]?.text).toBe("remote draft");

    act(() => result.current.add("session-1", replacementReference));

    expect(result.current.drafts["session-1"]?.references).toEqual([replacementReference]);
    await waitFor(() => expect(api.writeInputDraft).toHaveBeenCalledWith("session-1", {
      text: "remote draft",
      references: [replacementReference],
    }));
  });

  it("does not let late hydration overwrite a local edit", async () => {
    let resolveRead: ((draft: InputDraft) => void) | undefined;
    const api = setupRuntime({
      readInputDraft: vi.fn().mockImplementation(
        () => new Promise<InputDraft>((resolve) => { resolveRead = resolve; }),
      ),
    });
    const { result } = renderHook(() => useInputDrafts("session-1", true));

    act(() => result.current.update("session-1", (draft) => ({ ...draft, text: "local edit" })));
    resolveRead?.({ text: "stale remote", references: [reference] });

    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.drafts["session-1"]?.text).toBe("local edit");
    expect(api.writeInputDraft).toHaveBeenCalledWith("session-1", {
      text: "local edit",
      references: [],
    });
  });

  it("inserts inline references at the requested cursor selection and persists token offsets", async () => {
    const skillReference: InputReference = {
      ...reference,
      id: "1234567890ab" + "e".repeat(52),
      kind: "skill",
      label: "docx",
      source: "skills/docx",
    };
    const api = setupRuntime({
      readInputDraft: vi.fn().mockResolvedValue({ text: "对比结果。并且输出文档。", references: [] }),
    });
    const { result } = renderHook(() => useInputDrafts("session-1", true));

    await waitFor(() => expect(result.current.ready).toBe(true));

    let caret: number | undefined;
    act(() => {
      caret = result.current.insert("session-1", skillReference, { start: 5, end: 5 });
    });

    expect(result.current.drafts["session-1"]?.text).toBe(
      "对比结果。[$docx](ref:1234567890ab)并且输出文档。",
    );
    expect(caret).toBe("对比结果。[$docx](ref:1234567890ab)".length);
    expect(result.current.drafts["session-1"]?.references).toEqual([skillReference]);
    await waitFor(() => expect(api.writeInputDraft).toHaveBeenCalledWith("session-1", {
      text: "对比结果。[$docx](ref:1234567890ab)并且输出文档。",
      references: [skillReference],
    }));
  });
});

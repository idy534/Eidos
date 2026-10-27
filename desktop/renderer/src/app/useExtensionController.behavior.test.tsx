import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EidosRuntimeAPI, SkillMetadata } from "../contracts.js";
import { useExtensionController } from "./useExtensionController.js";

const originalRuntime = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

function skill(qualifiedId: string, name: string): SkillMetadata {
  return {
    schemaVersion: 1,
    qualifiedId,
    name,
    description: `${name} skill`,
    pluginId: "eidos-user",
    pluginVersion: "local",
    pluginHash: "a".repeat(64),
    contentHash: "b".repeat(64),
    sourceKind: "user",
    enabled: true,
    available: true,
  };
}

describe("useExtensionController skill mutations", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (originalRuntime) Object.defineProperty(window, "eidosRuntime", originalRuntime);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("tracks simultaneous skill changes independently and rejects duplicate requests", async () => {
    const writer = skill("user:writer", "Writer");
    const coder = skill("user:coder", "Coder");
    let rejectWriter!: (cause: Error) => void;
    let resolveCoder!: (value: SkillMetadata) => void;
    const setSkillEnabled = vi.fn((qualifiedId: string, enabled: boolean) => {
      if (qualifiedId === writer.qualifiedId) {
        return new Promise<SkillMetadata>((_resolve, reject) => { rejectWriter = reject; });
      }
      return new Promise<SkillMetadata>((resolve) => { resolveCoder = resolve; }).then((value) => ({
        ...value,
        enabled,
      }));
    });
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readExtensions: vi.fn().mockResolvedValue({ plugins: [], skills: [writer, coder], servers: [], throughEventId: 0 }),
        readExtensionEvents: vi.fn().mockResolvedValue({ items: [], throughEventId: 0 }),
        setSkillEnabled,
      } as unknown as EidosRuntimeAPI,
    });

    const { result } = renderHook(() => useExtensionController());
    await act(async () => { await result.current[1].load(); });

    let writerMutation!: Promise<void>;
    let coderMutation!: Promise<void>;
    act(() => {
      writerMutation = result.current[1].setSkillEnabled(writer.qualifiedId, false);
      coderMutation = result.current[1].setSkillEnabled(coder.qualifiedId, false);
    });

    expect(result.current[0].pendingSkillIds).toEqual(new Set([writer.qualifiedId, coder.qualifiedId]));
    expect(result.current[0].pendingAction).toBeUndefined();
    expect(result.current[0].skills.find((item) => item.qualifiedId === writer.qualifiedId)?.enabled).toBe(false);
    expect(result.current[0].skills.find((item) => item.qualifiedId === coder.qualifiedId)?.enabled).toBe(false);

    await expect(result.current[1].setSkillEnabled(writer.qualifiedId, true)).rejects.toThrow("该技能正在更新");
    expect(setSkillEnabled).toHaveBeenCalledTimes(2);

    await act(async () => {
      rejectWriter(new Error("temporary failure"));
      await expect(writerMutation).rejects.toThrow("temporary failure");
      resolveCoder({ ...coder, enabled: false });
      await coderMutation;
    });

    expect(result.current[0].pendingSkillIds.size).toBe(0);
    expect(result.current[0].skills.find((item) => item.qualifiedId === writer.qualifiedId)?.enabled).toBe(true);
    expect(result.current[0].skills.find((item) => item.qualifiedId === coder.qualifiedId)?.enabled).toBe(false);
  });
});

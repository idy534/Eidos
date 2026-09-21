import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InputContextProvider } from "./InputContext.js";
import { InputPicker } from "./InputPicker.js";
import type { EidosRuntimeAPI } from "../contracts.js";
import type { InputReference } from "../../../shared/input-context.js";

const reference: InputReference = {
  id: "a".repeat(64),
  kind: "file",
  label: "notes.txt",
  source: "/workspace/notes.txt",
  sha256: "b".repeat(64),
  status: "content",
  size: 12,
};

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("InputPicker behavior", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupRuntime(overrides: Record<string, unknown> = {}) {
    const api = {
      listSkills: vi.fn().mockResolvedValue({ skills: [] }),
      listPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
      listMcpServers: vi.fn().mockResolvedValue({ servers: [] }),
      listSessions: vi.fn().mockResolvedValue({ items: [] }),
      listWorkspaceDirectory: vi.fn().mockResolvedValue({ path: ".", entries: [], truncated: false }),
      pickInputPaths: vi.fn().mockResolvedValue([]),
      prepareInput: vi.fn().mockResolvedValue(reference),
      onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      ...overrides,
    } as unknown as EidosRuntimeAPI;
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: api,
    });
    return api;
  }

  const pickerProps = {
    query: "",
    inline: false,
    onQuery: vi.fn(),
    onChoose: vi.fn(),
    onClose: vi.fn(),
    onCommand: vi.fn(),
  };

  it("shows command candidates and dispatches the selected command", async () => {
    setupRuntime();
    const onCommand = vi.fn();

    render(<InputPicker {...pickerProps} mode="commands" onCommand={onCommand} />);

    const option = await screen.findByRole("option", { name: /\/skills/ });
    fireEvent.click(option);

    expect(onCommand).toHaveBeenCalledWith("skills");
  });

  it("prepares a file selected by the quick action and adds it to the current draft", async () => {
    const api = setupRuntime({
      pickInputPaths: vi.fn().mockResolvedValue(["/workspace/notes.txt"]),
    });
    const onAdd = vi.fn();

    render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={onAdd}
        onSettings={vi.fn()}
      >
        <InputPicker {...pickerProps} mode="all" />
      </InputContextProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "添加文件" }));

    await waitFor(() => expect(onAdd).toHaveBeenCalledWith("session-1", reference));
    expect(api.prepareInput).toHaveBeenCalledWith({
      kind: "file",
      source: "/workspace/notes.txt",
      sessionId: "session-1",
    });
  });

  it("keeps unavailable skill candidates disabled", async () => {
    setupRuntime({
      listSkills: vi.fn().mockResolvedValue({
        skills: [{
          qualifiedId: "user:disabled",
          name: "Disabled Skill",
          description: "Unavailable",
          sourceKind: "user",
          pluginId: "",
          available: false,
          enabled: true,
        }],
      }),
    });
    const onChoose = vi.fn();

    render(<InputPicker {...pickerProps} mode="skill" onChoose={onChoose} />);

    const option = await screen.findByRole("option", { name: /Disabled Skill/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(option);
    expect(onChoose).not.toHaveBeenCalled();
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InputContextProvider, InputDropZone, InputReferenceCards, useInputContext } from "./InputContext.js";
import type { EidosRuntimeAPI } from "../contracts.js";
import type { InputReference } from "../../../shared/input-context.js";

const fileReference: InputReference = {
  id: "a".repeat(64),
  kind: "file",
  label: "a.txt",
  source: "/workspace/a.txt",
  sha256: "b".repeat(64),
  status: "content",
  size: 1,
};
const historyReference: InputReference = {
  ...fileReference,
  id: "c".repeat(64),
  kind: "history",
  label: "Previous conversation",
  source: "session-history",
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("InputContext behavior", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupRuntime(overrides: Record<string, unknown> = {}) {
    const api = {
      prepareInput: vi.fn().mockResolvedValue(fileReference),
      inputPathForFile: vi.fn().mockReturnValue("/workspace/a.txt"),
      onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      pasteInputImage: vi.fn().mockResolvedValue(fileReference),
      showItemInFolder: vi.fn().mockResolvedValue(undefined),
      ...overrides,
    } as unknown as EidosRuntimeAPI;
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: api,
    });
    return api;
  }

  function Actions() {
    const context = useInputContext();
    return (
      <>
        <button type="button" onClick={() => void context?.add({ kind: "excerpt", source: "selection", text: "quote" })}>
          add
        </button>
        <button type="button" onClick={() => void context?.paths(["/workspace/a.txt", "/workspace/b.txt"])}>
          paths
        </button>
        <span>{context?.error}</span>
      </>
    );
  }

  function provider(children: React.ReactNode, options: { ready?: boolean; onAdd?: ReturnType<typeof vi.fn> } = {}) {
    return (
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready={options.ready ?? true}
        onAdd={options.onAdd ?? vi.fn()}
        onSettings={vi.fn()}
        onNavigateToSession={vi.fn()}
      >
        {children}
      </InputContextProvider>
    );
  }

  it("keeps accepted paths when a later path fails", async () => {
    const onAdd = vi.fn();
    const api = setupRuntime({
      prepareInput: vi.fn()
        .mockResolvedValueOnce(fileReference)
        .mockRejectedValueOnce(new Error("second path failed")),
    });

    render(provider(<Actions />, { onAdd }));
    fireEvent.click(screen.getByRole("button", { name: "paths" }));

    await waitFor(() => expect(onAdd).toHaveBeenCalledWith("session-1", fileReference));
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(api.prepareInput).toHaveBeenCalledTimes(2);
    expect(screen.getByText("操作失败，请查看 Runtime 日志。")).toBeInTheDocument();
  });

  it("blocks additions until draft hydration is ready", async () => {
    const api = setupRuntime();
    render(provider(<Actions />, { ready: false }));

    fireEvent.click(screen.getByRole("button", { name: "add" }));

    await waitFor(() => expect(screen.getByText("草稿尚未恢复，请稍后再添加引用。")).toBeInTheDocument());
    expect(api.prepareInput).not.toHaveBeenCalled();
  });

  it("maps dropped files and shows the draft-only drop affordance", async () => {
    const onAdd = vi.fn();
    const api = setupRuntime();
    const { container } = render(provider(
      <InputDropZone><span>composer</span></InputDropZone>,
      { onAdd },
    ));
    const zone = container.querySelector(".input-drop-zone") as HTMLElement;
    const file = new File(["a"], "a.txt", { type: "text/plain" });
    const dataTransfer = { types: ["Files"], files: [file], dropEffect: "none" };

    fireEvent.dragEnter(zone, { dataTransfer });
    expect(screen.getByText("添加到当前草稿；文件不会自动发送")).toBeInTheDocument();
    fireEvent.drop(zone, { dataTransfer });

    await waitFor(() => expect(onAdd).toHaveBeenCalledWith("session-1", fileReference));
    expect(api.inputPathForFile).toHaveBeenCalledWith(file);
    expect(screen.queryByText("添加到当前草稿；文件不会自动发送")).not.toBeInTheDocument();
  });

  it("navigates to a referenced history session", () => {
    const navigate = vi.fn();
    setupRuntime();
    render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
        onNavigateToSession={navigate}
      >
        <InputReferenceCards references={[historyReference]} />
      </InputContextProvider>,
    );

    fireEvent.click(screen.getByTitle("跳转到该对话"));
    expect(navigate).toHaveBeenCalledWith("session-history");
  });

  it("renders bubble variant with semantic categorization and handles remove action", () => {
    const onRemove = vi.fn();
    setupRuntime();
    const codeRef: InputReference = {
      ...fileReference,
      id: "code-1",
      label: "agentic-kit.html",
      source: "/workspace/agentic-kit.html",
    };
    const skillRef: InputReference = {
      ...fileReference,
      id: "skill-1",
      kind: "skill",
      label: "docx",
      source: "skills/docx",
    };

    const { container, rerender } = render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <InputReferenceCards references={[codeRef, skillRef]} variant="bubble" />
      </InputContextProvider>,
    );

    const list = container.querySelector(".input-reference-list");
    expect(list).toHaveClass("input-reference-list--bubble");
    expect(list?.getAttribute("data-variant")).toBe("bubble");

    const codePill = container.querySelector(".input-reference--kind-file");
    expect(codePill).toHaveClass("input-reference--bubble");
    expect(codePill?.querySelector(".input-reference__icon--cat-code")).toBeInTheDocument();

    const skillPill = container.querySelector(".input-reference--kind-skill");
    expect(skillPill).toHaveClass("input-reference--bubble");
    expect(skillPill?.querySelector(".input-reference__icon--skill")).toBeInTheDocument();

    // In read-only bubble mode, remove buttons are not rendered
    expect(container.querySelector(".input-reference-remove")).toBeNull();

    // When onRemove is passed (e.g. composer or editing mode)
    rerender(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <InputReferenceCards references={[codeRef]} variant="composer" onRemove={onRemove} />
      </InputContextProvider>,
    );

    const removeBtn = screen.getByLabelText("移除 agentic-kit.html");
    fireEvent.click(removeBtn);
    expect(onRemove).toHaveBeenCalledWith("code-1");
  });
  it("previews frozen Agent instructions and restores focus when dismissed", async () => {
    const reference: InputReference = { ...fileReference, kind: "agent", label: "luna-work", source: "luna-work", status: "selection" };
    setupRuntime({ readInput: vi.fn().mockResolvedValue({ reference, text: "提供商：volcengine\n你需要保留其他 Agent 的修改。" }) });
    render(<InputReferenceCards references={[reference]} />);
    const trigger = screen.getByRole("button", { name: "luna-work" });
    const triggerIcon = trigger.querySelector(".input-reference__icon--agent svg");
    expect(triggerIcon).not.toBeNull();
    expect(triggerIcon?.querySelectorAll("circle")).toHaveLength(3);
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "luna-work" });
    expect(dialog).toHaveTextContent("提供商：volcengine");
    expect(dialog).toHaveTextContent("你需要保留其他 Agent 的修改。");
    expect(dialog.querySelector(".skill-type-label")).toHaveTextContent("智能体");
    expect(dialog.querySelector(".skill-icon.agent-icon svg")?.querySelectorAll("circle")).toHaveLength(3);
    expect(dialog.querySelector(".skill-detail-actions .skill-detail-close-btn")).not.toBeNull();
    const close = screen.getByRole("button", { name: "关闭智能体定义" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Tab" });
    expect(screen.getByLabelText("智能体配置和指令")).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("renders shelf cards (file miniature preview, directory two-line card, excerpt dialog) and filters out inline kinds when placement='shelf'", async () => {
    const contentFileRef: InputReference = {
      ...fileReference,
      id: "file-content-1",
      label: "AGENTS.md",
      source: "/workspace/AGENTS.md",
      status: "content",
    };
    const locationFileRef: InputReference = {
      ...fileReference,
      id: "file-loc-1",
      label: "large-bundle.ts",
      source: "/workspace/large-bundle.ts",
      status: "location",
    };
    const dirRef: InputReference = {
      ...fileReference,
      id: "dir-1",
      kind: "directory",
      label: "Eidos",
      source: "/workspace/Eidos",
      status: "location",
    };
    const excerptRef: InputReference = {
      ...fileReference,
      id: "excerpt-1",
      kind: "excerpt",
      label: "选中内容",
      source: "editor-selection",
      status: "selection",
    };
    const skillRef: InputReference = {
      ...fileReference,
      id: "skill-ignored",
      kind: "skill",
      label: "frontend-design",
      source: "skills/frontend-design",
    };

    setupRuntime({
      readInput: vi.fn().mockImplementation(async (id: string) => {
        if (id === "file-content-1") {
          return { reference: contentFileRef, text: "# AGENTS.md\nScope and authority" };
        }
        return { reference: excerptRef, text: "引用的一段完整代码与说明文字" };
      }),
    });

    const { container } = render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <InputReferenceCards
          references={[contentFileRef, locationFileRef, dirRef, excerptRef, skillRef]}
          placement="shelf"
        />
      </InputContextProvider>,
    );

    expect(container.querySelector(".input-reference-list--shelf")).not.toBeNull();
    // Skill is an inline reference and must not appear in shelf placement
    expect(screen.queryByText("frontend-design")).not.toBeInTheDocument();

    // Directory card shows name + "文件夹" subtitle
    expect(screen.getByText("Eidos")).toBeInTheDocument();
    expect(screen.getByText("文件夹")).toBeInTheDocument();

    // Location-only file shows extension badge
    expect(screen.getByText("TS")).toBeInTheDocument();

    // Content file loads miniature text snippet
    await waitFor(() => {
      expect(container.querySelector(".input-reference__mini-doc")?.textContent).toContain("# AGENTS.md");
    });

    // Clicking excerpt opens full text preview dialog
    fireEvent.click(screen.getByRole("button", { name: /选中内容/ }));
    const dialog = await screen.findByRole("dialog", { name: "选中内容" });
    expect(dialog).toHaveTextContent("引用的一段完整代码与说明文字");
    fireEvent.click(screen.getByRole("button", { name: "关闭引用文本详情" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders inline tokens and opens preview dialogs for skill, mcp, and plugin references", async () => {
    const skillRef: InputReference = {
      ...fileReference,
      id: "skill-1",
      kind: "skill",
      label: "frontend-design",
      source: "skills/frontend-design",
    };
    const mcpRef: InputReference = {
      ...fileReference,
      id: "mcp-1",
      kind: "mcp",
      label: "Context7",
      source: "context7",
    };
    const pluginRef: InputReference = {
      ...fileReference,
      id: "plugin-1",
      kind: "plugin",
      label: "chrome-devtools-plugin",
      source: "chrome-devtools-plugin",
    };

    setupRuntime({
      readInput: vi.fn().mockImplementation(async (id: string) => {
        if (id === "skill-1") {
          return {
            reference: skillRef,
            text: "---\nname: frontend-design\ndescription: UI design skill\n---\nUse intentional typography.",
          };
        }
        return {
          reference: pluginRef,
          text: "Plugin: chrome-devtools-plugin\nProvides DevTools debugging skills.",
        };
      }),
      listMcpServers: vi.fn().mockResolvedValue({
        servers: [
          {
            serverId: "context7",
            executable: "npx",
            argv: ["-y", "@upstash/context7-mcp"],
            consented: true,
            declaredEnabled: true,
            available: true,
          },
        ],
      }),
      readMcpTools: vi.fn().mockResolvedValue({
        state: "ready",
        tools: [],
        total: 0,
      }),
    });

    const { container } = render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <InputReferenceCards
          references={[fileReference, skillRef, mcpRef, pluginRef]}
          placement="inline"
        />
      </InputContextProvider>,
    );

    expect(container.querySelector(".input-reference-list--inline")).not.toBeNull();
    // Shelf file reference is excluded from inline placement
    expect(screen.queryByText("a.txt")).not.toBeInTheDocument();

    // Skill token opens SkillDetailDialog
    fireEvent.click(screen.getByRole("button", { name: "frontend-design" }));
    const skillDialog = await screen.findByRole("dialog", { name: "frontend-design" });
    expect(skillDialog).toHaveTextContent("Use intentional typography.");
    fireEvent.click(screen.getByRole("button", { name: "关闭技能详情" }));

    // MCP token opens McpDetailDialog
    fireEvent.click(screen.getByRole("button", { name: "Context7" }));
    const mcpDialog = await screen.findByRole("dialog", { name: "context7" });
    expect(mcpDialog).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭 MCP 详情" }));

    // Plugin token opens ReferenceTextPreviewDialog
    fireEvent.click(screen.getByRole("button", { name: "chrome-devtools-plugin" }));
    const pluginDialog = await screen.findByRole("dialog", { name: "chrome-devtools-plugin" });
    expect(pluginDialog).toHaveTextContent("Provides DevTools debugging skills.");
    fireEvent.click(screen.getByRole("button", { name: "关闭插件详情" }));

    // Even when onRemove is provided, inline tokens never render a hover × remove button
    const onRemove = vi.fn();
    const { container: editableContainer } = render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <InputReferenceCards
          references={[skillRef]}
          placement="inline"
          onRemove={onRemove}
        />
      </InputContextProvider>,
    );
    expect(editableContainer.querySelector(".input-reference-remove")).toBeNull();
    const skillBtn = editableContainer.querySelector(".input-reference__btn") as HTMLElement;
    fireEvent.keyDown(skillBtn, { key: "Backspace" });
    expect(onRemove).toHaveBeenCalledWith("skill-1");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  EidosRuntimeAPI,
  ModelListResult,
  ModelPresetsResult,
  RuntimeStatus,
  SkillDetail,
  SkillMetadata,
} from "../../contracts.js";
import { SettingsPage } from "./SettingsPage.js";

const runtime: RuntimeStatus = {
  state: "ready", protocolVersion: 1, runtimeVersion: "0.3.0", runShell: true,
  modelConfigured: true, storageHealth: { state: "ready" },
};
const modelList: ModelListResult = { defaultModelId: null, models: [] };
const presets: ModelPresetsResult = { providers: [] };
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");

function skill(overrides: Partial<SkillMetadata> = {}): SkillMetadata {
  return {
    schemaVersion: 1,
    qualifiedId: "user:writer",
    name: "Writer",
    description: "Write documents.",
    pluginId: "eidos-user",
    pluginVersion: "local",
    pluginHash: "a".repeat(64),
    contentHash: "b".repeat(64),
    sourceKind: "user",
    enabled: true,
    available: true,
    ...overrides,
  };
}

function props(overrides: Record<string, unknown> = {}) {
  return {
    runtime,
    modelList,
    modelLoading: false,
    modelError: undefined,
    plugins: [],
    skills: [],
    mcpServers: [],
    pendingAction: undefined,
    onClose: vi.fn(),
    onModelsChanged: vi.fn().mockResolvedValue(undefined),
    onImportPlugin: vi.fn().mockResolvedValue(undefined),
    onTogglePlugin: vi.fn().mockResolvedValue(undefined),
    onRemovePlugin: vi.fn().mockResolvedValue(undefined),
    onToggleSkill: vi.fn().mockResolvedValue(undefined),
    onRemoveSkill: vi.fn().mockResolvedValue({
      qualifiedId: "user:writer", removed: true, cleanupPending: false,
    }),
    onToggleMcp: vi.fn().mockResolvedValue(undefined),
    onCreateMcp: vi.fn().mockResolvedValue(undefined),
    onUpdateMcp: vi.fn().mockResolvedValue(undefined),
    onRemoveMcp: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function setRuntime(readSkillDetail: ReturnType<typeof vi.fn>, showItemInFolder = vi.fn()) {
  (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
    listModelPresets: vi.fn().mockResolvedValue(presets),
    readSkillDetail,
    showItemInFolder,
  } as EidosRuntimeAPI;
  return showItemInFolder;
}

afterEach(() => {
  vi.restoreAllMocks();
  if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
  else delete (window as Partial<Window>).eidosRuntime;
  if (clipboardDescriptor) Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else delete (navigator as Partial<Navigator>).clipboard;
});

describe("Skill settings", () => {
  it("separates personal and system skills and filters search results", async () => {
    const user = userEvent.setup();
    setRuntime(vi.fn().mockResolvedValue({}));
    render(<SettingsPage {...props({
      skills: [
        skill(),
        skill({
          qualifiedId: "system:review", name: "System Review", sourceKind: "system",
        }),
      ],
    })} />);

    await user.click(screen.getByRole("tab", { name: /技能/ }));
    expect(screen.getByRole("button", { name: /Writer.*已启用/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /System Review/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "系统" }));
    expect(screen.getByRole("button", { name: /System Review.*已启用/ })).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "搜索技能" }), "missing");
    expect(screen.queryByRole("button", { name: /System Review/ })).not.toBeInTheDocument();
    expect(screen.getByText("没有匹配的技能")).toBeInTheDocument();
  });

  it("loads detail, toggles the skill, and exposes Finder and Markdown actions", async () => {
    const user = userEvent.setup();
    const detail: SkillDetail = {
      qualifiedId: "user:writer",
      content: "---\nname: writer\ndescription: Write documents.\n---\n# Writer\n\nBody text.\n",
      body: "# Writer\n\nBody text.\n",
      directory: "/tmp/eidos/skills/writer",
    };
    const readSkillDetail = vi.fn().mockResolvedValue(detail);
    const showItemInFolder = setRuntime(readSkillDetail);
    const onToggleSkill = vi.fn().mockResolvedValue(undefined);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<SettingsPage {...props({ skills: [skill()], onToggleSkill })} />);

    await user.click(screen.getByRole("tab", { name: /技能/ }));
    const card = screen.getByRole("button", { name: /Writer.*已启用/ });
    await user.click(card);
    expect(within(await screen.findByRole("dialog")).getByRole("heading", { name: "Writer", level: 2 })).toBeInTheDocument();
    expect(readSkillDetail).toHaveBeenCalledWith("user:writer");
    expect(await screen.findByText("Body text.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "关闭技能详情" })).toHaveFocus());

    await user.click(screen.getByRole("switch", { name: "启用或禁用 Writer" }));
    expect(onToggleSkill).toHaveBeenCalledWith("user:writer", false);

    await user.click(screen.getByRole("button", { name: "技能选项" }));
    await user.click(screen.getByRole("menuitem", { name: "复制 Markdown" }));
    expect(writeText).toHaveBeenCalledWith(detail.content);
    await user.click(screen.getByRole("button", { name: "技能选项" }));
    await user.click(screen.getByRole("menuitem", { name: "在 Finder 中显示" }));
    expect(showItemInFolder).toHaveBeenCalledWith(detail.directory);

    await user.keyboard("{Escape}");
    expect(card).toHaveFocus();
  });

  it("requires confirmation before removing a personal skill", async () => {
    const user = userEvent.setup();
    const readSkillDetail = vi.fn().mockResolvedValue({
      qualifiedId: "user:writer",
      content: "---\nname: writer\ndescription: Write documents.\n---\nBody.\n",
      body: "Body.\n",
      directory: "/tmp/eidos/skills/writer",
    } satisfies SkillDetail);
    setRuntime(readSkillDetail);
    const onRemoveSkill = vi.fn().mockResolvedValue({
      qualifiedId: "user:writer", removed: true, cleanupPending: false,
    });
    render(<SettingsPage {...props({ skills: [skill()], onRemoveSkill })} />);

    await user.click(screen.getByRole("tab", { name: /技能/ }));
    await user.click(screen.getByRole("button", { name: /Writer.*已启用/ }));
    const detailDialog = await screen.findByRole("dialog");
    await user.click(within(detailDialog).getByRole("button", { name: "卸载" }));
    const confirmation = screen.getByRole("alertdialog");
    expect(confirmation).toHaveTextContent("卸载“Writer”？");
    await user.click(within(confirmation).getByRole("button", { name: "取消" }));
    expect(onRemoveSkill).not.toHaveBeenCalled();

    await user.click(within(detailDialog).getByRole("button", { name: "卸载" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "卸载" }));
    await waitFor(() => expect(onRemoveSkill).toHaveBeenCalledWith("user:writer"));
    expect(screen.getByText("技能已卸载")).toBeInTheDocument();
  });
});

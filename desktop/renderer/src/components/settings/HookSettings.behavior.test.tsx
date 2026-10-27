import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { HookActivity, HookConfigurationRequest, HookSourceReview } from "../../../../shared/hooks.js";
import { HookSourceSettings } from "./HookSourceSettings.js";
import { HookHistoryDialog } from "./HookHistoryDialog.js";
import { HookActivityRows } from "../HookActivityRows.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const source: HookSourceReview = { id: "project:one", scope: "project", paths: ["/project/.eidos/hooks.json"], contentHash: "a".repeat(64),
  enabled: true, trusted: false, definitions: [{ key: "start", event: "SessionStart", command: "python check.py", handlerType: "command" }] };
const activity: HookActivity = { id: "hook-1", runId: "run-1", sessionId: "session-1", sourceId: "demo", event: "SessionStart", handlerType: "command",
  status: "completed", createdAt: 1, result: { status: "completed", systemMessage: "需要复查" }, stdoutPreview: "output", previewTruncated: true };

afterEach(() => {
  cleanup(); vi.restoreAllMocks();
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

function api(value: object) { Object.defineProperty(window, "eidosRuntime", { configurable: true, value }); }

it("reviews exact local configuration, supports open/reveal actions, and trusts on toggle", async () => {
  let current = source;
  const hookConfiguration = vi.fn(async (request: HookConfigurationRequest) => {
    if (request.action === "trust") current = { ...current, trusted: request.trusted ?? false };
    if (request.action === "enable") current = { ...current, enabled: request.enabled ?? false };
    return { sources: [current] };
  });
  const openPath = vi.fn().mockResolvedValue(undefined);
  const showItemInFolder = vi.fn().mockResolvedValue(undefined);
  api({ hookConfiguration, openPath, showItemInFolder });

  render(<HookSourceSettings sessionId="session-1" />);
  const user = userEvent.setup();

  // 查看弹窗
  await user.click(await screen.findByRole("button", { name: "查看" }));
  expect(screen.getByRole("alertdialog")).toHaveTextContent("python check.py");
  await user.click(screen.getByRole("button", { name: "关闭" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());

  // 下拉菜单操作：打开文件 & 在 Finder 中显示
  await user.click(screen.getByRole("button", { name: "更多 project 选项" }));
  await user.click(await screen.findByRole("menuitem", { name: "打开 hooks.json" }));
  expect(openPath).toHaveBeenCalledWith("/project/.eidos/hooks.json");

  await user.click(screen.getByRole("button", { name: "更多 project 选项" }));
  await user.click(await screen.findByRole("menuitem", { name: "在 Finder 中显示" }));
  expect(showItemInFolder).toHaveBeenCalledWith("/project/.eidos/hooks.json");

  // 初始未审查/未信任，开关为关闭状态
  const toggle = screen.getByRole("switch", { name: "启用或停用project" });
  expect(toggle).not.toBeChecked();

  // 开启开关自动触发 trust 并 enable
  await user.click(toggle);
  await waitFor(() => expect(hookConfiguration).toHaveBeenCalledWith({ action: "trust", sourceId: source.id, contentHash: source.contentHash, trusted: true }));
  expect(hookConfiguration).toHaveBeenCalledWith({ action: "enable", sourceId: source.id, contentHash: source.contentHash, enabled: true });
  await waitFor(() => expect(toggle).toBeChecked());

  // 关闭开关触发 enable: false
  await user.click(toggle);
  await waitFor(() => expect(hookConfiguration).toHaveBeenCalledWith({ action: "enable", sourceId: source.id, contentHash: source.contentHash, enabled: false }));
});

it("keeps a changed-version error and handles toggle failure safely", async () => {
  const hookConfiguration = vi.fn(async (request: HookConfigurationRequest) => {
    if (request.action === "trust") throw new Error("EIDOS_RUNTIME_ERROR:HOOK_CONFIGURATION_CHANGED");
    return { sources: [source] };
  });
  api({ hookConfiguration });
  render(<HookSourceSettings />);
  const user = userEvent.setup();

  const toggle = await screen.findByRole("switch", { name: "启用或停用project" });
  expect(toggle).not.toBeChecked();
  await user.click(toggle);

  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("请刷新后重新审阅"));
  expect(toggle).not.toBeChecked();
});

it("ignores an old project's late mutation and does not reload its configuration", async () => {
  let finish!: (value: object) => void;
  const other = { ...source, id: "project:two", paths: ["/other/.eidos/hooks.json"] };
  const hookConfiguration = vi.fn((request: HookConfigurationRequest): Promise<object> => {
    if (request.action === "trust") return new Promise(resolve => { finish = resolve; });
    return Promise.resolve({ sources: [request.sessionId === "two" ? other : source] });
  });
  api({ hookConfiguration });
  const view = render(<HookSourceSettings sessionId="one" />);
  const user = userEvent.setup();

  const toggle = await screen.findByRole("switch", { name: "启用或停用project" });
  await user.click(toggle);

  view.rerender(<HookSourceSettings sessionId="two" />);
  await screen.findByText("/other/.eidos/hooks.json");
  finish({ sources: [{ ...source, trusted: true }] });
  await waitFor(() => expect(screen.queryByText("/project/.eidos/hooks.json")).not.toBeInTheDocument());
  expect(hookConfiguration.mock.calls.filter(([request]) => request.action === "read" && request.sessionId === "one")).toHaveLength(1);
});

it("loads bounded history pages and exposes warnings, previews, and keyboard close", async () => {
  const readHookHistory = vi.fn().mockResolvedValueOnce({ items: [activity], nextCursor: 9 })
    .mockResolvedValueOnce({ items: [{ ...activity, id: "hook-2", event: "Stop" }], nextCursor: null });
  const onClose = vi.fn();
  api({ readHookHistory });
  render(<HookHistoryDialog open sourceId="demo" onClose={onClose} />);
  const user = userEvent.setup();
  await user.click(await screen.findByText("SessionStart · 已完成"));
  expect(screen.getByRole("alert")).toHaveTextContent("需要复查");
  expect(screen.getByText("output")).toBeInTheDocument();
  expect(screen.getByText(/部分内容已省略/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "加载更早记录" }));
  expect(await screen.findByText("Stop · 已完成")).toBeInTheDocument();
  expect(readHookHistory).toHaveBeenLastCalledWith({ sourceId: "demo", cursor: 9 });
  const close = screen.getByRole("button", { name: "关闭 Hook 执行记录" });
  close.focus();
  await user.tab({ shift: true });
  expect(document.activeElement).not.toBe(document.body);
  await user.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalledTimes(1);
});

it("keeps loaded records after paging fails and discards a closed dialog's late response", async () => {
  let resolve!: (value: object) => void;
  const readHookHistory = vi.fn().mockResolvedValueOnce({ items: [activity], nextCursor: 9 })
    .mockRejectedValueOnce(new Error("记录读取失败"))
    .mockImplementationOnce(() => new Promise(finish => { resolve = finish; }));
  api({ readHookHistory });
  const view = render(<HookHistoryDialog open runId="run-1" onClose={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "加载更早记录" }));
  expect(await screen.findByText("操作失败，请查看 Runtime 日志。")).toBeInTheDocument();
  expect(screen.getByText("SessionStart · 已完成")).toBeInTheDocument();
  view.rerender(<HookHistoryDialog open runId="run-2" onClose={vi.fn()} />);
  await waitFor(() => expect(readHookHistory).toHaveBeenCalledTimes(3));
  view.rerender(<HookHistoryDialog open={false} runId="run-2" onClose={vi.fn()} />);
  resolve({ items: [activity] });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("shows active status and warning history from a completed Run without restarting it", async () => {
  const readHookHistory = vi.fn().mockResolvedValue({ items: [activity] });
  api({ readHookHistory });
  render(<HookActivityRows run={{ id: "run-1", sessionId: "session-1", status: "succeeded", modelId: "test", modelStepCount: 1, createdAt: 1, updatedAt: 1,
    hookActivity: [activity, { ...activity, id: "pending", status: "running", statusMessage: "检查资源中", result: null }] }} />);
  expect(screen.getByRole("status")).toHaveTextContent("检查资源中");
  expect(screen.getByRole("alert")).toHaveTextContent("需要复查");
  await userEvent.setup().click(screen.getByRole("button", { name: "查看 Hook 记录" }));
  const dialog = await screen.findByRole("dialog", { name: "Hook 执行记录" });
  expect(within(dialog).getByRole("button", { name: "刷新" })).toBeInTheDocument();
  expect(readHookHistory).toHaveBeenCalledWith({ runId: "run-1" });
});

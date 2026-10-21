import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { WebSearchResponse } from "../../../../shared/web-search.generated.js";
import { WebSearchSettings } from "./WebSearchSettings.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const initial = (): WebSearchResponse => ({
  revision: 0,
  config: { defaultProvider: null },
  credentials: { deepseek: "missing", langsearch: "missing" },
});

function setup(value = initial()) {
  let credentials: WebSearchResponse["credentials"] = { ...value.credentials };
  const api = {
    readWebSearch: vi.fn().mockResolvedValue(value),
    saveWebSearch: vi.fn().mockImplementation(async (request) => {
      for (const change of request.credentialChanges ?? []) {
        credentials = {
          ...credentials,
          [change.provider]: change.action === "set" ? "stored" : "missing",
        };
      }
      return {
        revision: request.expectedRevision + 1,
        config: request.config,
        credentials: { ...credentials },
      };
    }),
    openWebSearchOfficialPage: vi.fn().mockResolvedValue(undefined),
  };
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: api });
  render(<WebSearchSettings />);
  return api;
}

afterEach(() => {
  vi.restoreAllMocks();
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

it("requires an explicit default, allows neither selection and auto-saves credentials and default switch", async () => {
  const user = userEvent.setup();
  const api = setup();
  const deep = await screen.findByRole("checkbox", { name: "DeepSeek Search 默认使用" });
  const lang = screen.getByRole("checkbox", { name: "LangSearch 默认使用" });
  expect(deep).not.toBeChecked();
  expect(lang).not.toBeChecked();

  // Type keys and blur to commit
  const deepInput = screen.getByLabelText("DeepSeek Search API Key", { selector: "input" });
  await user.type(deepInput, "deep-key{Enter}");
  await waitFor(() =>
    expect(api.saveWebSearch).toHaveBeenCalledWith({
      expectedRevision: 0,
      config: { defaultProvider: null },
      credentialChanges: [{ provider: "deepseek", action: "set", value: "deep-key" }],
    }),
  );
  await waitFor(() => expect(deepInput).toHaveValue(""));

  const langInput = screen.getByLabelText("LangSearch API Key", { selector: "input" });
  await user.type(langInput, "lang-key{Enter}");
  await waitFor(() =>
    expect(api.saveWebSearch).toHaveBeenCalledWith({
      expectedRevision: 1,
      config: { defaultProvider: null },
      credentialChanges: [{ provider: "langsearch", action: "set", value: "lang-key" }],
    }),
  );

  // Toggle default to deepseek, then langsearch, then deselect
  await user.click(deep);
  await waitFor(() =>
    expect(api.saveWebSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        expectedRevision: 2,
        config: expect.objectContaining({ defaultProvider: "deepseek" }),
      }),
    ),
  );

  await user.click(lang);
  await waitFor(() =>
    expect(api.saveWebSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        expectedRevision: 3,
        config: expect.objectContaining({ defaultProvider: "langsearch" }),
      }),
    ),
  );

  await user.click(lang);
  await waitFor(() =>
    expect(api.saveWebSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        expectedRevision: 4,
        config: expect.objectContaining({ defaultProvider: null }),
      }),
    ),
  );

  expect(api.openWebSearchOfficialPage).not.toHaveBeenCalled();
});

it("shows alert when auto-save fails and allows refreshing revision", async () => {
  const user = userEvent.setup();
  const api = setup();
  await screen.findByText("LangSearch 官方网页搜索 API");
  const langInput = screen.getByLabelText("LangSearch API Key", { selector: "input" });

  api.saveWebSearch.mockRejectedValueOnce(new Error("conflict"));
  await user.type(langInput, "draft-key{Enter}");
  expect(await screen.findByRole("alert")).toHaveTextContent("网页搜索设置保存失败");

  api.readWebSearch.mockResolvedValueOnce({ ...initial(), revision: 3 });
  await user.click(screen.getByRole("button", { name: "刷新配置" }));
  // No refresh notice banner should appear
  expect(screen.queryByText(/Eidos 已读取最新配置/)).not.toBeInTheDocument();
});

it("keeps saved API keys hidden and clears them only through the explicit action", async () => {
  const user = userEvent.setup();
  const api = setup({
    ...initial(),
    config: { defaultProvider: "deepseek" },
    credentials: { deepseek: "stored", langsearch: "missing" },
  });
  const input = await screen.findByLabelText("DeepSeek Search API Key", { selector: "input" });
  expect(input).toHaveAttribute("type", "password");
  expect(input).toHaveValue("");
  expect(input).toHaveAttribute("placeholder", "已保存，留空则保留");

  fireEvent.blur(input);
  expect(api.saveWebSearch).not.toHaveBeenCalled();

  const toggleBtn = screen.getByRole("button", { name: "显示 DeepSeek Search API Key" });
  expect(toggleBtn).toBeDisabled();

  await user.click(screen.getByRole("button", { name: "清除已保存的 Key" }));
  await waitFor(() =>
    expect(api.saveWebSearch).toHaveBeenCalledWith({
      expectedRevision: 0,
      config: { defaultProvider: null },
      credentialChanges: [{ provider: "deepseek", action: "clear" }],
    }),
  );
  await waitFor(() => expect(input).toHaveAttribute("placeholder", "输入 API Key"));
});

it("opens official links and auto-saves other settings without extra summaries", async () => {
  const user = userEvent.setup();
  const api = setup();
  const section = (await screen.findByRole("heading", { name: "LangSearch", exact: true })).closest("section")!;

  await user.click(within(section).getByRole("button", { name: "获取 API Key ↗" }));
  expect(api.openWebSearchOfficialPage).toHaveBeenCalledWith({ provider: "langsearch", target: "keys" });

  // Open "其他设置"
  await user.click(within(section).getByRole("button", { name: /其他设置/ }));
  // Ensure the old verbose summary is removed
  expect(within(section).queryByText(/条数 · 超时/)).not.toBeInTheDocument();

  await user.type(screen.getByLabelText("仅搜索这些域名（可选）"), "docs.example.com, example.org");
  await user.click(screen.getByLabelText("返回网页正文"));

  await waitFor(
    () =>
      expect(api.saveWebSearch).toHaveBeenCalledWith(
        expect.objectContaining({
          config: expect.objectContaining({
            langsearch: expect.objectContaining({
              includeDomains: ["docs.example.com", "example.org"],
              includePageContent: true,
            }),
          }),
        }),
      ),
    { timeout: 3000 },
  );
});

it("shows a recoverable read error", async () => {
  const user = userEvent.setup();
  const api = setup();
  api.readWebSearch.mockRejectedValueOnce(new Error("offline"));
  await user.click(await screen.findByRole("button", { name: "刷新配置" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("无法读取");
  expect(screen.getByRole("button", { name: "刷新配置" })).toBeEnabled();
});

it("explains a missing default credential before auto-saving default", async () => {
  const user = userEvent.setup();
  const api = setup();
  await user.click(await screen.findByRole("checkbox", { name: "LangSearch 默认使用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("请先为默认搜索服务填写 API Key");
  expect(api.saveWebSearch).not.toHaveBeenCalled();
});

it("removes localization IP entry from deepseek search settings", async () => {
  const user = userEvent.setup();
  setup();
  const section = (await screen.findByRole("heading", { name: "DeepSeek Search", exact: true })).closest("section")!;
  await user.click(within(section).getByRole("button", { name: /其他设置/ }));

  expect(within(section).getByLabelText("默认结果条数")).toBeInTheDocument();
  expect(within(section).getByLabelText("请求超时（秒）")).toBeInTheDocument();
  expect(within(section).getByLabelText("每条结果最多字符数")).toBeInTheDocument();
  expect(within(section).getByLabelText("返回网页正文")).toBeInTheDocument();

  expect(within(section).queryByText("本地化 IP（可选）")).not.toBeInTheDocument();
  expect(within(section).queryByPlaceholderText("例如 203.0.113.1")).not.toBeInTheDocument();
  expect(within(section).queryByText("高级控制")).not.toBeInTheDocument();
});

it("supports redesigned freshness presets and custom date range in langsearch", async () => {
  const user = userEvent.setup();
  const api = setup();
  const section = (await screen.findByRole("heading", { name: "LangSearch", exact: true })).closest("section")!;
  await user.click(within(section).getByRole("button", { name: /其他设置/ }));

  const freshnessSelect = within(section).getByLabelText("时间范围");
  expect(freshnessSelect).toHaveValue("noLimit");

  // Select preset "oneWeek"
  await user.selectOptions(freshnessSelect, "oneWeek");
  await waitFor(() =>
    expect(api.saveWebSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        config: expect.objectContaining({
          langsearch: expect.objectContaining({ freshness: "oneWeek" }),
        }),
      }),
    ),
  );

  // Switch to custom date range
  await user.selectOptions(freshnessSelect, "custom");
  const startInput = await within(section).findByLabelText("起始日期");
  const endInput = within(section).getByLabelText("截止日期（可选）");

  await user.clear(startInput);
  await user.type(startInput, "2026-10-01");
  await user.type(endInput, "2026-10-21");

  await waitFor(
    () =>
      expect(api.saveWebSearch).toHaveBeenCalledWith(
        expect.objectContaining({
          config: expect.objectContaining({
            langsearch: expect.objectContaining({ freshness: "2026-10-01..2026-10-21" }),
          }),
        }),
      ),
    { timeout: 3000 },
  );
});

it("correctly restores existing custom freshness range from saved config", async () => {
  const user = userEvent.setup();
  setup({
    ...initial(),
    config: {
      defaultProvider: "langsearch",
      langsearch: {
        freshness: "2026-05-01..2026-05-31",
      },
    },
  });
  const section = (await screen.findByRole("heading", { name: "LangSearch", exact: true })).closest("section")!;
  await user.click(within(section).getByRole("button", { name: /其他设置/ }));

  const freshnessSelect = within(section).getByLabelText("时间范围");
  expect(freshnessSelect).toHaveValue("custom");

  const startInput = within(section).getByLabelText("起始日期");
  const endInput = within(section).getByLabelText("截止日期（可选）");
  expect(startInput).toHaveValue("2026-05-01");
  expect(endInput).toHaveValue("2026-05-31");
});

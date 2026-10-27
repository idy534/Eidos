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
  let credentialValues = { deepseek: "", langsearch: "", ...value.credentialValues };
  let credentials: WebSearchResponse["credentials"] = { ...value.credentials };
  const api = {
    readWebSearch: vi.fn().mockResolvedValue(value),
    saveWebSearch: vi.fn().mockImplementation(async (request) => {
      for (const change of request.credentialChanges ?? []) {
        credentialValues = { ...credentialValues, [change.provider]: change.action === "set" ? change.value : "" };
        credentials = {
          ...credentials,
          [change.provider]: change.action === "set" ? "stored" : "missing",
        };
      }
      return {
        revision: request.expectedRevision + 1,
        config: request.config,
        credentials: { ...credentials },
        credentialValues: { ...credentialValues },
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
  await waitFor(() => expect(deepInput).toHaveValue("deep-key"));

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

it("restores both saved keys masked, supports viewing, and clears a key through its current input", async () => {
  const user = userEvent.setup();
  const api = setup({
    ...initial(),
    config: { defaultProvider: "deepseek" },
    credentials: { deepseek: "stored", langsearch: "stored" },
    credentialValues: { deepseek: "saved-deep-key", langsearch: "saved-lang-key" },
  });
  const input = await screen.findByLabelText("DeepSeek Search API Key", { selector: "input" });
  const lang = screen.getByLabelText("LangSearch API Key", { selector: "input" });
  expect(input).toHaveAttribute("type", "password");
  expect(input).toHaveValue("saved-deep-key");
  expect(lang).toHaveAttribute("type", "password");
  expect(lang).toHaveValue("saved-lang-key");
  expect(screen.queryByRole("button", { name: "清除已保存的 Key" })).not.toBeInTheDocument();
  expect(input).toHaveAttribute("placeholder", "输入 API Key");
  fireEvent.blur(input);
  expect(api.saveWebSearch).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "显示 DeepSeek Search API Key" }));
  expect(input).toHaveAttribute("type", "text");
  await user.click(screen.getByRole("button", { name: "隐藏 DeepSeek Search API Key" }));
  expect(input).toHaveAttribute("type", "password");
  await user.clear(input);
  fireEvent.blur(input);
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenCalledWith({
    expectedRevision: 0,
    config: { defaultProvider: null },
    credentialChanges: [{ provider: "deepseek", action: "clear" }],
  }));
  expect(input).toHaveValue("");
  expect(lang).toHaveValue("saved-lang-key");
});

it("refreshes the actual key and resets its visibility", async () => {
  const user = userEvent.setup();
  const api = setup({ ...initial(), credentials: { deepseek: "stored", langsearch: "missing" }, credentialValues: { deepseek: "old-key" } });
  const input = await screen.findByLabelText("DeepSeek Search API Key", { selector: "input" });
  await user.click(screen.getByRole("button", { name: "显示 DeepSeek Search API Key" }));
  api.readWebSearch.mockResolvedValueOnce({ ...initial(), credentialValues: { deepseek: "new-key" } });
  await user.click(screen.getByRole("button", { name: "刷新配置" }));
  await waitFor(() => expect(input).toHaveValue("new-key"));
  expect(input).toHaveAttribute("type", "password");
});

it("preserves a newer edit during a pending save and debounces the latest value", async () => {
  const user = userEvent.setup();
  const api = setup();
  const input = await screen.findByLabelText("DeepSeek Search API Key", { selector: "input" });
  let resolveSave!: (value: WebSearchResponse) => void;
  api.saveWebSearch.mockImplementationOnce(() => new Promise<WebSearchResponse>(resolve => { resolveSave = resolve; }));
  await user.type(input, "first-key{Enter}");
  await user.clear(input);
  await user.type(input, "newer-key");
  resolveSave({ revision: 1, config: { defaultProvider: null }, credentials: { deepseek: "stored", langsearch: "missing" }, credentialValues: { deepseek: "first-key" } });
  await waitFor(() => expect(input).toHaveValue("newer-key"));
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenLastCalledWith(expect.objectContaining({
    expectedRevision: 1, credentialChanges: [{ provider: "deepseek", action: "set", value: "newer-key" }],
  })), { timeout: 2500 });
  expect(input).toHaveValue("newer-key");
});

it("keeps edited keys after a failed save", async () => {
  const user = userEvent.setup();
  const api = setup();
  const input = await screen.findByLabelText("LangSearch API Key", { selector: "input" });
  api.saveWebSearch.mockRejectedValueOnce(new Error("offline"));
  await user.type(input, "retry-key{Enter}");
  expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
  expect(input).toHaveValue("retry-key");
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


it("queues only the latest value per provider and clears a key entered during an unfinished save", async () => {
  const user = userEvent.setup();
  const api = setup();
  const input = await screen.findByLabelText("DeepSeek Search API Key", { selector: "input" });
  let resolveSave!: (value: WebSearchResponse) => void;
  api.saveWebSearch.mockImplementationOnce(() => new Promise<WebSearchResponse>(resolve => { resolveSave = resolve; }));
  await user.type(input, "first-key{Enter}");
  fireEvent.change(input, { target: { value: "second-key" } });
  fireEvent.blur(input);
  fireEvent.change(input, { target: { value: "third-key" } });
  fireEvent.blur(input);
  fireEvent.change(input, { target: { value: "" } });
  fireEvent.blur(input);
  resolveSave({ revision: 1, config: { defaultProvider: null }, credentials: { deepseek: "stored", langsearch: "missing" }, credentialValues: { deepseek: "first-key" } });
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenLastCalledWith({
    expectedRevision: 1, config: { defaultProvider: null }, credentialChanges: [{ provider: "deepseek", action: "clear" }],
  }));
  expect(api.saveWebSearch).toHaveBeenCalledTimes(2);
  expect(input).toHaveValue("");
});

it("debounces clearing a saved key without requiring blur", async () => {
  const user = userEvent.setup();
  const api = setup({ ...initial(), credentials: { deepseek: "missing", langsearch: "stored" }, credentialValues: { langsearch: "saved-lang-key" } });
  const input = await screen.findByLabelText("LangSearch API Key", { selector: "input" });
  await user.clear(input);
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenCalledWith(expect.objectContaining({
    credentialChanges: [{ provider: "langsearch", action: "clear" }],
  })), { timeout: 2500 });
});

it("saves Doubao credentials, switches default, masks the key and clears the selected service", async () => {
  const user = userEvent.setup();
  const api = setup();
  const input = await screen.findByLabelText("豆包搜索 API Key", { selector: "input" });
  expect(input).toHaveAttribute("type", "password");
  await user.type(input, "doubao-key{Enter}");
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenCalledWith(expect.objectContaining({
    credentialChanges: [{ provider: "doubao", action: "set", value: "doubao-key" }],
  })));
  const selected = screen.getByRole("checkbox", { name: "豆包搜索 默认使用" });
  await user.click(selected);
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenLastCalledWith(expect.objectContaining({
    config: expect.objectContaining({ defaultProvider: "doubao" }),
  })));
  expect(screen.getByRole("checkbox", { name: "LangSearch 默认使用" })).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "显示 豆包搜索 API Key" }));
  expect(input).toHaveAttribute("type", "text");
  await user.clear(input);
  fireEvent.blur(input);
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenLastCalledWith(expect.objectContaining({
    config: expect.objectContaining({ defaultProvider: null }),
    credentialChanges: [{ provider: "doubao", action: "clear" }],
  })));
  expect(selected).not.toBeChecked();
});

it("loads Doubao options and saves its time and domain filters through the shared controls", async () => {
  const user = userEvent.setup();
  const api = setup({ ...initial(), config: { doubao: { freshness: "oneWeek", includeDomains: ["example.com"] } }, credentialValues: { doubao: "saved-doubao-key" } });
  const section = (await screen.findByRole("heading", { name: "豆包搜索", exact: true })).closest("section")!;
  expect(within(section).getByLabelText("豆包搜索 API Key", { selector: "input" })).toHaveValue("saved-doubao-key");
  await user.click(within(section).getByRole("button", { name: /其他设置/ }));
  expect(within(section).getByLabelText("时间范围")).toHaveValue("oneWeek");
  expect(within(section).getByLabelText("仅搜索这些域名（可选）")).toHaveValue("example.com");
  await user.selectOptions(within(section).getByLabelText("时间范围"), "oneDay");
  fireEvent.change(within(section).getByLabelText("排除这些域名（可选）"), { target: { value: "spam.org" } });
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenLastCalledWith(expect.objectContaining({
    config: expect.objectContaining({ doubao: expect.objectContaining({ freshness: "oneDay", excludeDomains: ["spam.org"] }) }),
  })));
  await user.click(within(section).getByRole("button", { name: /获取 API Key/ }));
  expect(api.openWebSearchOfficialPage).toHaveBeenCalledWith({ provider: "doubao", target: "keys" });
});

it.each([
  ["默认结果条数", "0", "7", "defaultCount"],
  ["请求超时（秒）", "121", "45", "timeoutSeconds"],
  ["每条结果最多字符数", "99", "1500", "maxExcerptCharacters"],
  ["默认结果条数", "1.5", "8", "defaultCount"],
])("keeps invalid %s input local and saves a corrected integer", async (label, invalid, corrected, field) => {
  const user = userEvent.setup();
  const api = setup();
  await screen.findByText("LangSearch 官方网页搜索 API");
  const section = screen.getByRole("heading", { name: "LangSearch" }).closest("section")!;
  await user.click(within(section).getByRole("button", { name: "其他设置" }));
  const input = within(section).getByLabelText(label);
  fireEvent.change(input, { target: { value: "" } });
  expect(input).toHaveValue(null);
  fireEvent.change(input, { target: { value: invalid } });
  expect(input).toBeInvalid();
  // A later Key save must still use the last valid options.
  const key = within(section).getByLabelText("LangSearch API Key", { selector: "input" });
  fireEvent.change(key, { target: { value: "valid-search-key" } });
  fireEvent.blur(key);
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenCalledTimes(1));
  expect(api.saveWebSearch.mock.calls[0][0].config.langsearch).toBeUndefined();
  fireEvent.change(input, { target: { value: corrected } });
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenCalledTimes(2));
  expect(api.saveWebSearch.mock.calls[1][0].config.langsearch[field]).toBe(Number(corrected));
});

it("keeps an invalid Key draft without submitting it and accepts its correction", async () => {
  const api = setup();
  const input = await screen.findByLabelText("豆包搜索 API Key", { selector: "input" });
  fireEvent.change(input, { target: { value: "Bearer sample-key" } });
  fireEvent.blur(input);
  expect(await screen.findByRole("alert")).toHaveTextContent("API Key 不能包含空格");
  expect(api.saveWebSearch).not.toHaveBeenCalled();
  expect(input).toHaveValue("Bearer sample-key");
  fireEvent.change(input, { target: { value: "sample-key" } });
  fireEvent.blur(input);
  await waitFor(() => expect(api.saveWebSearch).toHaveBeenCalledTimes(1));
});

it("reports Runtime parameter rejection without claiming an internal failure", async () => {
  const api = setup();
  api.saveWebSearch.mockRejectedValueOnce(new Error("Error invoking remote method 'web-search:save': Error: EIDOS_RUNTIME_ERROR:INVALID_PARAMS"));
  const input = await screen.findByLabelText("LangSearch API Key", { selector: "input" });
  fireEvent.change(input, { target: { value: "draft-key" } });
  fireEvent.blur(input);
  expect(await screen.findByRole("alert")).toHaveTextContent("网页搜索参数无效");
  expect(input).toHaveValue("draft-key");
});

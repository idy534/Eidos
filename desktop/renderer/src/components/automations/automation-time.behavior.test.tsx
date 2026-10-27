import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Automation } from "../../../../shared/automation.js";
import type { ModelOption } from "../../contracts.js";
import { AutomationForm } from "./AutomationForm.js";
import { AutomationToolCard } from "./AutomationToolCard.js";
import { AutomationTaskList } from "./AutomationTaskList.js";
import { automationDateInput, formatAutomationTime, formatPreviewTime, oneTimeOptions } from "./automation-time.js";

const model = { id: "model", name: "本地模型" } as ModelOption;
const task: Automation = {
  id: "11111111-1111-4111-8111-111111111111", revision: 1, name: "提醒", prompt: "检查状态。", kind: "at",
  schedule: { runAt: "2026-10-28T01:00:37.123Z", timezone: "Asia/Shanghai" },
  status: "ACTIVE", model: { modelId: "model" }, approvalMode: "manual",
  target: { kind: "new_session", projectId: null, execution: { executionMode: "local" } }, createdAt: 1, updatedAt: 1,
};

afterEach(() => vi.restoreAllMocks());

function form(value = task) {
  const save = vi.fn(async () => {});
  const toast = vi.fn();
  render(<AutomationForm task={value} projects={[]} sessions={[]} models={[model]} busy={false} onSave={save} onShowToast={toast} />);
  return { save, toast, input: screen.getByLabelText("执行时间（所选时区）") };
}

describe("automation timezone boundaries", () => {
  it("preserves the exact instant when the host timezone changes during an ordinary edit", async () => {
    // Reproduce the old sv-SE conversion changing while the form stays mounted.
    let localTime = "2026-10-28 09:00:37";
    const original = Date.prototype.toLocaleString;
    vi.spyOn(Date.prototype, "toLocaleString").mockImplementation(function (locale, options) {
      return locale === "sv-SE" ? localTime : original.call(this, locale, options);
    });
    const { save, input } = form();
    expect(input).toHaveValue("2026-10-28T09:00");
    localTime = "2026-10-28 10:00:37";
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新名称" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith({ mode: "update", id: task.id, expectedRevision: 1, changes: { name: "新名称" } }));
  });

  it("converts an edited wall time using the selected timezone", async () => {
    const { save, input } = form();
    fireEvent.change(input, { target: { value: "2026-10-28T11:30" } });
    fireEvent.change(screen.getByLabelText("时区"), { target: { value: "Asia/Seoul" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ changes: {
      kind: "at", schedule: { runAt: "2026-10-28T02:30:00Z", timezone: "Asia/Seoul" },
    } })));
  });

  it("rejects nonexistent wall times before saving or previewing", async () => {
    window.eidosRuntime = { automation: vi.fn() } as unknown as typeof window.eidosRuntime;
    const { save, toast, input } = form();
    fireEvent.change(screen.getByLabelText("时区"), { target: { value: "America/New_York" } });
    fireEvent.change(input, { target: { value: "2026-03-08T02:30" } });
    expect(screen.getByRole("alert")).toHaveTextContent("不存在这个时间");
    expect(input).toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    fireEvent.click(screen.getByRole("button", { name: "预览执行时间" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining("不存在这个时间"), "error"));
    expect(save).not.toHaveBeenCalled();
    expect(window.eidosRuntime.automation).not.toHaveBeenCalled();
  });

  it("requires a choice for repeated wall times and saves the selected second occurrence", async () => {
    const { save, toast, input } = form();
    fireEvent.change(screen.getByLabelText("时区"), { target: { value: "America/New_York" } });
    fireEvent.change(input, { target: { value: "2026-11-01T01:30" } });
    const choice = screen.getByRole("combobox", { name: /这个时间出现两次/ });
    expect(choice).toHaveValue("");
    fireEvent.submit(screen.getByRole("button", { name: "保存任务" }).closest("form")!);
    expect(save).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(expect.stringContaining("UTC 偏移"), "error");
    expect(screen.getByRole("option", { name: "第 2 次（UTC-05:00）" })).toBeInTheDocument();
    fireEvent.change(choice, { target: { value: "2026-11-01T06:30:00Z" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ changes: {
      kind: "at", schedule: { runAt: "2026-11-01T06:30:00Z", timezone: "America/New_York" },
    } })));
  });

  it("preserves a saved second occurrence and sub-minute precision on ordinary edits", async () => {
    const value: Automation = { ...task, schedule: { runAt: "2026-11-01T06:30:37.123Z", timezone: "America/New_York" } };
    const { save } = form(value);
    expect(screen.getByRole("combobox", { name: /这个时间出现两次/ })).toHaveValue("2026-11-01T06:30:00Z");
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新名称" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ changes: { name: "新名称" } })));
  });

  it("handles half-hour transitions with the same conversion boundary", () => {
    expect(() => oneTimeOptions("2026-10-04T02:15", "Australia/Lord_Howe")).toThrow("不存在这个时间");
    expect(oneTimeOptions("2026-04-05T01:45", "Australia/Lord_Howe")).toEqual([
      { instant: "2026-04-04T14:45:00Z", offset: "+11:00" },
      { instant: "2026-04-04T15:15:00Z", offset: "+10:30" },
    ]);
    expect(automationDateInput("2026-10-28T01:00:00Z", "Asia/Seoul")).toBe("2026-10-28T10:00");
  });

  it("labels task-zone previews and uses explicit UTC for old previews without a zone", () => {
    const instant = "2026-10-28T00:30:00Z";
    const { unmount } = render(<AutomationToolCard mode="preview" schedule={{ timezone: "America/New_York" }} response={{ kind: "preview", nextRuns: [instant] }} />);
    expect(screen.getByText(formatAutomationTime(instant, "America/New_York"))).toHaveTextContent("2026/10/27");
    expect(screen.getByText(/America\/New_York/)).toHaveTextContent("GMT-04:00");
    unmount();
    render(<AutomationToolCard mode="preview" response={{ kind: "preview", nextRuns: [instant] }} />);
    expect(screen.getByText(formatAutomationTime(instant, "UTC"))).toHaveTextContent("2026/10/28");
  });

  it("uses the task timezone for list dates across midnight", () => {
    render(<AutomationTaskList items={[{ ...task, schedule: { timezone: "America/New_York" }, nextRunAt: "2026-10-28T00:30:00Z" }]}
      searchQuery="" busy={false} hasMore={false} onSelect={vi.fn()} />);
    expect(screen.getByRole("button")).toHaveTextContent("10/27");
    expect(screen.getByRole("button")).toHaveTextContent("America/New_York");
  });

  it("renders timezone selector grouped by continents and preserves custom timezone", () => {
    form(task);
    const timezoneSelect = screen.getByLabelText("时区");
    expect(timezoneSelect.tagName.toLowerCase()).toBe("select");
    const optgroups = Array.from(timezoneSelect.querySelectorAll("optgroup")).map((group) => group.getAttribute("label"));
    expect(optgroups).toEqual(expect.arrayContaining(["亚洲", "美洲", "欧洲", "大洋洲", "非洲"]));

    const shanghaiOption = screen.getByRole("option", { name: /Asia\/Shanghai/i });
    expect(shanghaiOption).toBeInTheDocument();
  });

  it("retains unknown or custom timezone under a dedicated optgroup", () => {
    form({ ...task, schedule: { ...task.schedule, timezone: "Antarctica/Troll" } });
    const timezoneSelect = screen.getByLabelText("时区");
    expect(timezoneSelect).toHaveValue("Antarctica/Troll");
    const customGroup = timezoneSelect.querySelector('optgroup[label="当前设置"]');
    expect(customGroup).toBeInTheDocument();
    expect(customGroup?.querySelector('option[value="Antarctica/Troll"]')).toBeInTheDocument();
  });

  it("formats preview time without redundant timezone suffixes", () => {
    const formatted = formatPreviewTime("2026-10-28T00:00:00Z", "Asia/Seoul");
    expect(formatted).toBe("2026/10/28 09:00");
    expect(formatted).not.toContain("Asia/Seoul");
    expect(formatted).not.toContain("GMT");
  });
});

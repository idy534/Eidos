import { Temporal } from "@js-temporal/polyfill";

export interface OneTimeOption {
  instant: string;
  offset: string;
}

export class AutomationTimeError extends Error {}

export function automationDateInput(instant: string, timezone: string): string {
  return Temporal.Instant.from(instant).toZonedDateTimeISO(timezone).toPlainDateTime()
    .toString({ smallestUnit: "minute" });
}

export function oneTimeOptions(input: string, timezone: string): OneTimeOption[] {
  try {
    const local = Temporal.PlainDateTime.from(input);
    const earlier = local.toZonedDateTime(timezone, { disambiguation: "earlier" });
    const later = local.toZonedDateTime(timezone, { disambiguation: "later" });
    const options = [earlier, later].filter((value, index, all) =>
      value.toPlainDateTime().equals(local) && (index === 0 || !value.equals(all[0]!)));
    if (!options.length) throw new AutomationTimeError("所选时区不存在这个时间，请选择其他执行时间。");
    return options.map((value) => ({ instant: value.toInstant().toString(), offset: value.offset }));
  } catch (error) {
    if (error instanceof RangeError) throw new AutomationTimeError("执行时间或时区无效，请检查输入。");
    throw error;
  }
}

export function initialOneTimeChoice(instant: string, timezone: string): string {
  const options = oneTimeOptions(automationDateInput(instant, timezone), timezone);
  const minute = Temporal.Instant.from(instant).round({ smallestUnit: "minute", roundingMode: "floor" });
  return options.length > 1
    ? options.find((option) => minute.equals(option.instant))?.instant ?? ""
    : "";
}

export function formatAutomationTime(instant: string, timezone?: string | null): string {
  const zone = timezone || "UTC";
  try {
    return `${new Date(instant).toLocaleString("zh-CN", {
      timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false, timeZoneName: "longOffset",
    })} · ${zone}`;
  } catch {
    // Old tool results may not carry a supported region. Always label the fallback.
    return `${new Date(instant).toISOString()} · UTC`;
  }
}

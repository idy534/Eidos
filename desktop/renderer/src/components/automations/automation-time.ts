import { Temporal } from "@js-temporal/polyfill";

export interface OneTimeOption {
  instant: string;
  offset: string;
}

export interface TimezoneOption {
  value: string;
  label: string;
}

export interface TimezoneGroup {
  region: string;
  zones: TimezoneOption[];
}

export const COMMON_TIMEZONE_GROUPS: TimezoneGroup[] = [
  {
    region: "亚洲",
    zones: [
      { value: "Asia/Shanghai", label: "Asia/Shanghai (北京、上海)" },
      { value: "Asia/Hong_Kong", label: "Asia/Hong_Kong (香港)" },
      { value: "Asia/Taipei", label: "Asia/Taipei (台北)" },
      { value: "Asia/Tokyo", label: "Asia/Tokyo (东京)" },
      { value: "Asia/Seoul", label: "Asia/Seoul (首尔)" },
      { value: "Asia/Singapore", label: "Asia/Singapore (新加坡)" },
      { value: "Asia/Bangkok", label: "Asia/Bangkok (曼谷、河内)" },
      { value: "Asia/Jakarta", label: "Asia/Jakarta (雅加达)" },
      { value: "Asia/Manila", label: "Asia/Manila (马尼拉)" },
      { value: "Asia/Kolkata", label: "Asia/Kolkata (新德里、加尔各答)" },
      { value: "Asia/Dubai", label: "Asia/Dubai (迪拜)" },
      { value: "Asia/Urumqi", label: "Asia/Urumqi (乌鲁木齐)" },
      { value: "Asia/Almaty", label: "Asia/Almaty (阿拉木图)" },
      { value: "Asia/Tehran", label: "Asia/Tehran (德黑兰)" },
      { value: "Asia/Jerusalem", label: "Asia/Jerusalem (耶路撒冷)" },
    ],
  },
  {
    region: "美洲",
    zones: [
      { value: "America/New_York", label: "America/New_York (纽约、美国东部)" },
      { value: "America/Chicago", label: "America/Chicago (芝加哥、美国中部)" },
      { value: "America/Denver", label: "America/Denver (丹佛、美国山地)" },
      { value: "America/Los_Angeles", label: "America/Los_Angeles (洛杉矶、美国太平洋)" },
      { value: "America/Anchorage", label: "America/Anchorage (安克雷奇、阿拉斯加)" },
      { value: "America/Phoenix", label: "America/Phoenix (菲尼克斯/凤凰城)" },
      { value: "America/Toronto", label: "America/Toronto (多伦多)" },
      { value: "America/Vancouver", label: "America/Vancouver (温哥华)" },
      { value: "America/Mexico_City", label: "America/Mexico_City (墨西哥城)" },
      { value: "America/Sao_Paulo", label: "America/Sao_Paulo (圣保罗)" },
      { value: "America/Buenos_Aires", label: "America/Buenos_Aires (布宜诺斯艾利斯)" },
      { value: "America/Santiago", label: "America/Santiago (圣地亚哥)" },
      { value: "America/Bogota", label: "America/Bogota (波哥大)" },
    ],
  },
  {
    region: "欧洲",
    zones: [
      { value: "Europe/London", label: "Europe/London (伦敦)" },
      { value: "Europe/Paris", label: "Europe/Paris (巴黎)" },
      { value: "Europe/Berlin", label: "Europe/Berlin (柏林)" },
      { value: "Europe/Rome", label: "Europe/Rome (罗马)" },
      { value: "Europe/Madrid", label: "Europe/Madrid (马德里)" },
      { value: "Europe/Amsterdam", label: "Europe/Amsterdam (阿姆斯特丹)" },
      { value: "Europe/Brussels", label: "Europe/Brussels (布鲁塞尔)" },
      { value: "Europe/Zurich", label: "Europe/Zurich (苏黎世)" },
      { value: "Europe/Stockholm", label: "Europe/Stockholm (斯德哥尔摩)" },
      { value: "Europe/Athens", label: "Europe/Athens (雅典)" },
      { value: "Europe/Moscow", label: "Europe/Moscow (莫斯科)" },
      { value: "Europe/Istanbul", label: "Europe/Istanbul (伊斯坦布尔)" },
    ],
  },
  {
    region: "大洋洲",
    zones: [
      { value: "Australia/Sydney", label: "Australia/Sydney (悉尼)" },
      { value: "Australia/Melbourne", label: "Australia/Melbourne (墨尔本)" },
      { value: "Australia/Brisbane", label: "Australia/Brisbane (布里斯班)" },
      { value: "Australia/Perth", label: "Australia/Perth (珀斯)" },
      { value: "Australia/Adelaide", label: "Australia/Adelaide (阿德莱德)" },
      { value: "Pacific/Auckland", label: "Pacific/Auckland (奥克兰)" },
      { value: "Pacific/Honolulu", label: "Pacific/Honolulu (檀香山、夏威夷)" },
      { value: "Pacific/Fiji", label: "Pacific/Fiji (斐济)" },
    ],
  },
  {
    region: "非洲",
    zones: [
      { value: "Africa/Cairo", label: "Africa/Cairo (开罗)" },
      { value: "Africa/Johannesburg", label: "Africa/Johannesburg (约翰内斯堡)" },
      { value: "Africa/Lagos", label: "Africa/Lagos (拉各斯)" },
      { value: "Africa/Nairobi", label: "Africa/Nairobi (内罗毕)" },
      { value: "Africa/Casablanca", label: "Africa/Casablanca (卡萨布兰卡)" },
    ],
  },
  {
    region: "其他",
    zones: [
      { value: "UTC", label: "UTC (协调世界时)" },
    ],
  },
];

export function isCommonTimezone(timezone: string): boolean {
  return COMMON_TIMEZONE_GROUPS.some((group) => group.zones.some((zone) => zone.value === timezone));
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

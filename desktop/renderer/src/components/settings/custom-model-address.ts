export function customModelAddressError(value: string): string | undefined {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash || /\s/u.test(value)) {
      return "接口地址必须是 HTTP 或 HTTPS 地址，且不能包含凭据、查询或片段。";
    }
  } catch {
    return "请填写有效的 HTTP 或 HTTPS 接口地址。";
  }
  return undefined;
}

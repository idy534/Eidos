import { isWebSearchOfficialPageRequest } from "../shared/web-search.js";

const pages = {
  deepseek: {
    keys: "https://platform.deepseek.com/api_keys",
    docs: "https://api-docs.deepseek.com/zh-cn/guides/search/",
    pricing: "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
  },
  langsearch: {
    keys: "https://langsearch.com/dashboard",
    docs: "https://docs.langsearch.com/reference/search-api-guide",
    pricing: "https://docs.langsearch.com/limits/api-limits",
  },
} as const;

export function webSearchOfficialUrl(request: unknown): string {
  if (!isWebSearchOfficialPageRequest(request)) throw new Error("官方页面入口无效。");
  return pages[request.provider][request.target];
}

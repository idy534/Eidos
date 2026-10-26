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
  doubao: {
    keys: "https://console.volcengine.com/search-infinity/api-key",
    docs: "https://docs.volcengine.com/docs/Networkedsearch/Networkedsearch-2?lang=zh",
    pricing: "https://docs.volcengine.com/docs/Networkedsearch/Onlinesearchproductbilling?lang=zh",
  },
} as const;

export function webSearchOfficialUrl(request: unknown): string {
  if (!isWebSearchOfficialPageRequest(request)) throw new Error("官方页面入口无效。");
  return pages[request.provider][request.target];
}

import { findText, type FindOptions } from "./find-text.js";
self.onmessage = (event: MessageEvent<{ text: string; query: string; options: FindOptions }>) => {
  try { self.postMessage(findText(event.data.text, event.data.query, event.data.options)); }
  catch { self.postMessage({ error: "正则表达式无效", matches: [], truncated: false }); }
};

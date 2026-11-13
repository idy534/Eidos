/** The editable request a "整理" action places in a new draft. */
export function memoryTidyPrompt(scope: "project" | "global"): string {
  const target = scope === "global" ? "全局记忆" : "当前项目的记忆";
  return [
    `请整理${target}：`,
    `1. 用 memory_search 列出全部笔记：scope 设为 ${scope === "global" ? "global" : "current"}，省略 query，order 设为 oldest，按 nextCursor 翻页。`,
    "2. 读取可能重复或过期的笔记。主题相同的内容合并到一篇，再归档多余的笔记。",
    scope === "project"
      ? "3. 对照工作区核实会变化的项目事实，更正已经过期的内容。"
      : "3. 只保留仍然适用的偏好和约定，更正已经改变的内容。",
    "4. 不再有用的笔记只归档，不要彻底删除。",
    "5. 最后逐项列出改动和理由。",
  ].join("\n");
}

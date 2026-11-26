import {
  computeNewLineNumber,
  computeOldLineNumber,
  type HunkData,
  type HunkTokens,
  type TokenNode,
} from "react-diff-view";

const MAX_TOKENIZE_LINE_LENGTH = 2000;

type SyntaxLanguage =
  | "ts"
  | "python"
  | "json"
  | "css"
  | "html"
  | "markdown"
  | "shell"
  | "rust"
  | "go"
  | "sql"
  | "yaml"
  | "plain";

const KEYWORDS_BY_LANGUAGE: Record<SyntaxLanguage, ReadonlySet<string>> = {
  ts: new Set([
    "import", "from", "export", "default", "const", "let", "var", "function",
    "async", "await", "return", "if", "else", "for", "of", "in", "while",
    "switch", "case", "break", "continue", "try", "catch", "finally", "throw",
    "new", "class", "extends", "implements", "interface", "type", "enum",
    "public", "private", "protected", "readonly", "as", "is", "keyof",
    "typeof", "instanceof", "true", "false", "null", "undefined", "void",
  ]),
  python: new Set([
    "import", "from", "as", "def", "class", "return", "yield", "async",
    "await", "if", "elif", "else", "for", "in", "while", "break", "continue",
    "try", "except", "finally", "raise", "with", "pass", "lambda", "and",
    "or", "not", "is", "True", "False", "None", "global", "nonlocal", "assert",
  ]),
  json: new Set(["true", "false", "null"]),
  css: new Set(["@media", "@import", "@keyframes", "important", "from", "to"]),
  html: new Set([]),
  markdown: new Set([]),
  shell: new Set([
    "if", "then", "else", "elif", "fi", "for", "in", "do", "done", "case",
    "esac", "while", "function", "return", "export", "local", "readonly",
    "true", "false", "set", "exit",
  ]),
  rust: new Set([
    "use", "mod", "pub", "fn", "let", "mut", "const", "static", "struct",
    "enum", "trait", "impl", "for", "in", "while", "loop", "if", "else",
    "match", "return", "async", "await", "move", "ref", "where", "as",
    "true", "false", "Some", "None", "Ok", "Err",
  ]),
  go: new Set([
    "package", "import", "func", "var", "const", "type", "struct",
    "interface", "map", "chan", "go", "defer", "return", "if", "else",
    "for", "range", "switch", "case", "default", "select", "break",
    "continue", "true", "false", "nil", "iota",
  ]),
  sql: new Set([
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET",
    "DELETE", "CREATE", "TABLE", "INDEX", "ALTER", "DROP", "JOIN", "LEFT",
    "RIGHT", "INNER", "ON", "AND", "OR", "NOT", "NULL", "AS", "ORDER", "BY",
    "GROUP", "LIMIT", "OFFSET", "COALESCE", "EXISTS", "IN", "PRIMARY", "KEY",
    "select", "from", "where", "insert", "into", "values", "update", "set",
    "delete", "create", "table", "index", "alter", "drop", "join", "left",
    "right", "inner", "on", "and", "or", "not", "null", "as", "order", "by",
    "group", "limit", "offset", "coalesce", "exists", "in", "primary", "key",
  ]),
  yaml: new Set(["true", "false", "null", "yes", "no"]),
  plain: new Set([]),
};

const LANGUAGE_BY_EXT: Record<string, SyntaxLanguage> = {
  ts: "ts",
  tsx: "ts",
  js: "ts",
  jsx: "ts",
  mjs: "ts",
  cjs: "ts",
  mts: "ts",
  cts: "ts",
  py: "python",
  pyw: "python",
  json: "json",
  css: "css",
  scss: "css",
  html: "html",
  htm: "html",
  xml: "html",
  svg: "html",
  md: "markdown",
  mdx: "markdown",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  ps1: "shell",
  rs: "rust",
  go: "go",
  sql: "sql",
  yml: "yaml",
  yaml: "yaml",
};

export function detectDiffLanguage(filePath: string): SyntaxLanguage {
  const dot = filePath.lastIndexOf(".");
  if (dot < 0) return "plain";
  const ext = filePath.slice(dot + 1).toLowerCase();
  return LANGUAGE_BY_EXT[ext] ?? "plain";
}

function makeSpan(kind: string, value: string): TokenNode {
  return {
    type: "span",
    className: `diff-token diff-token--${kind}`,
    children: [{ type: "text", value }],
  };
}

function makeText(value: string): TokenNode {
  return { type: "text", value };
}

export function tokenizeLine(
  line: string,
  language: SyntaxLanguage,
): TokenNode[] {
  if (!line) return [makeText("")];
  if (language === "plain" || line.length > MAX_TOKENIZE_LINE_LENGTH) {
    return [makeText(line)];
  }
  const keywords = KEYWORDS_BY_LANGUAGE[language];
  const tokenPattern =
    /(\/\/.*$|#(?!!).*$|--.*$|\/\*.*?\*\/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b\d+(?:\.\d+)?\b|\b[A-Z][A-Za-z0-9_]*\b|\b[@A-Za-z_][A-Za-z0-9_-]*\b)/g;

  const nodes: TokenNode[] = [];
  let lastIndex = 0;
  for (const match of line.matchAll(tokenPattern)) {
    const start = match.index ?? 0;
    const raw = match[0]!;
    if (start > lastIndex) {
      nodes.push(makeText(line.slice(lastIndex, start)));
    }
    if (
      (raw.startsWith("//") && language !== "python" && language !== "shell" && language !== "yaml")
      || raw.startsWith("/*")
      || (raw.startsWith("#") && (language === "python" || language === "shell" || language === "yaml"))
      || (raw.startsWith("--") && language === "sql")
    ) {
      nodes.push(makeSpan("comment", raw));
    } else if (
      raw.startsWith('"') || raw.startsWith("'") || raw.startsWith("`")
    ) {
      nodes.push(makeSpan("string", raw));
    } else if (/^\d/.test(raw)) {
      nodes.push(makeSpan("number", raw));
    } else if (keywords.has(raw)) {
      nodes.push(makeSpan("keyword", raw));
    } else if (/^[A-Z][A-Za-z0-9_]*$/.test(raw)) {
      nodes.push(makeSpan("type", raw));
    } else {
      nodes.push(makeText(raw));
    }
    lastIndex = start + raw.length;
  }
  if (lastIndex < line.length) {
    nodes.push(makeText(line.slice(lastIndex)));
  }
  return nodes.length ? nodes : [makeText(line)];
}

export function tokenizeDiffHunks(
  hunks: readonly HunkData[],
  filePath: string,
): HunkTokens {
  const language = detectDiffLanguage(filePath);
  const oldLines: TokenNode[][] = [];
  const newLines: TokenNode[][] = [];

  for (const hunk of hunks) {
    for (const change of hunk.changes) {
      const rendered = tokenizeLine(change.content, language);
      if (change.type === "delete" || change.type === "normal") {
        const oldLine = computeOldLineNumber(change);
        if (oldLine > 0) oldLines[oldLine - 1] = rendered;
      }
      if (change.type === "insert" || change.type === "normal") {
        const newLine = computeNewLineNumber(change);
        if (newLine > 0) newLines[newLine - 1] = rendered;
      }
    }
  }
  return { old: oldLines, new: newLines };
}

export function buildSingleHunkPatch(
  file: { oldPath?: string; newPath?: string; type: string },
  hunk: HunkData,
): string {
  const oldPath =
    file.type === "add" || !file.oldPath || file.oldPath === "/dev/null"
      ? "/dev/null"
      : file.oldPath.startsWith("a/")
        ? file.oldPath
        : `a/${file.oldPath}`;
  const newPath =
    file.type === "delete" || !file.newPath || file.newPath === "/dev/null"
      ? "/dev/null"
      : file.newPath.startsWith("b/")
        ? file.newPath
        : `b/${file.newPath}`;

  const lines = [
    `--- ${oldPath}`,
    `+++ ${newPath}`,
    hunk.content,
    ...hunk.changes.map((change) => {
      const prefix =
        change.type === "insert" ? "+" : change.type === "delete" ? "-" : " ";
      return `${prefix}${change.content}`;
    }),
  ];
  return `${lines.join("\n")}\n`;
}

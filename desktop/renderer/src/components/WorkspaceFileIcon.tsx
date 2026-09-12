interface WorkspaceFileIconSpec {
  kind: string;
  label: string;
}

const FILE_ICON_BY_EXTENSION: Record<string, WorkspaceFileIconSpec> = {
  js: { kind: "javascript", label: "JS" }, mjs: { kind: "javascript", label: "JS" }, cjs: { kind: "javascript", label: "JS" }, jsx: { kind: "javascript", label: "JS" },
  ts: { kind: "typescript", label: "TS" }, tsx: { kind: "typescript", label: "TS" },
  py: { kind: "python", label: "PY" }, go: { kind: "go", label: "GO" },
  md: { kind: "markdown", label: "MD" }, markdown: { kind: "markdown", label: "MD" }, mdx: { kind: "markdown", label: "MD" },
  txt: { kind: "text", label: "TXT" }, log: { kind: "text", label: "LOG" },
  json: { kind: "json", label: "{}" }, jsonl: { kind: "json", label: "{}" }, jsonc: { kind: "json", label: "{}" },
  html: { kind: "html", label: "<>" }, htm: { kind: "html", label: "<>" },
  css: { kind: "css", label: "#" }, scss: { kind: "css", label: "#" }, sass: { kind: "css", label: "#" }, less: { kind: "css", label: "#" },
  yaml: { kind: "yaml", label: "YML" }, yml: { kind: "yaml", label: "YML" },
  sh: { kind: "shell", label: "SH" }, bash: { kind: "shell", label: "SH" }, zsh: { kind: "shell", label: "SH" },
  rs: { kind: "rust", label: "RS" }, java: { kind: "java", label: "JV" },
  c: { kind: "c", label: "C" }, h: { kind: "c", label: "C" },
  cc: { kind: "cpp", label: "C++" }, cpp: { kind: "cpp", label: "C++" }, hpp: { kind: "cpp", label: "C++" },
  rb: { kind: "ruby", label: "RB" }, swift: { kind: "swift", label: "SW" }, sql: { kind: "sql", label: "SQL" }, xml: { kind: "xml", label: "XML" }, toml: { kind: "toml", label: "TOM" },
  svg: { kind: "image", label: "IMG" }, png: { kind: "image", label: "IMG" }, jpg: { kind: "image", label: "IMG" }, jpeg: { kind: "image", label: "IMG" }, gif: { kind: "image", label: "IMG" }, webp: { kind: "image", label: "IMG" },
  pdf: { kind: "pdf", label: "PDF" },
  zip: { kind: "archive", label: "ZIP" }, "7z": { kind: "archive", label: "ZIP" }, rar: { kind: "archive", label: "ZIP" }, tar: { kind: "archive", label: "ZIP" }, gz: { kind: "archive", label: "ZIP" },
  doc: { kind: "document", label: "DOC" }, docx: { kind: "document", label: "DOC" },
  ppt: { kind: "document", label: "PPT" }, pptx: { kind: "document", label: "PPT" }, odp: { kind: "document", label: "PPT" },
  csv: { kind: "table", label: "CSV" }, xls: { kind: "table", label: "XLS" }, xlsx: { kind: "table", label: "XLS" },
  db: { kind: "database", label: "DB" }, sqlite: { kind: "database", label: "DB" }, sqlite3: { kind: "database", label: "DB" },
};

const FILE_ICON_BY_NAME: Record<string, WorkspaceFileIconSpec> = {
  dockerfile: { kind: "docker", label: "DK" }, makefile: { kind: "makefile", label: "MK" }, ".gitignore": { kind: "git", label: "GIT" },
};

export function WorkspaceFileIcon({ name }: { name: string }) {
  const lowered = name.toLowerCase();
  const extension = lowered.includes(".") ? lowered.slice(lowered.lastIndexOf(".") + 1) : "";
  const icon = FILE_ICON_BY_NAME[lowered] ?? FILE_ICON_BY_EXTENSION[extension];
  return (
    <svg viewBox="0 0 20 20" className="workspace-file-type-icon" data-file-icon={icon?.kind ?? "generic"}>
      <path d="M5 2.5h7l3 3v12H5zM12 2.5v3h3" />
      {icon && <><rect x="2" y="10" width="16" height="7.5" rx="2" /><text x="10" y="15.5" textAnchor="middle">{icon.label}</text></>}
    </svg>
  );
}

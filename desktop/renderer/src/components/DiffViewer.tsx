import { memo, useMemo, useState } from "react";
import { CopyIcon, CheckmarkIcon } from "./MenuIcons.js";

export interface DiffViewerProps {
  diff?: string | undefined;
  title?: string | undefined;
}

interface DiffLine {
  type: "add" | "del" | "context" | "hunk";
  lineNumber?: number | undefined;
  text: string;
}

interface ParsedDiffFile {
  filename: string;
  additions: number;
  deletions: number;
  lines: DiffLine[];
  rawDiff: string;
}

export function parseMultiFileDiff(raw: string, defaultTitle = "代码差异"): ParsedDiffFile[] {
  if (!raw || !raw.trim()) return [];

  const rawLines = raw.split("\n");
  const files: ParsedDiffFile[] = [];
  let currentFile: ParsedDiffFile | null = null;
  let currentRawLines: string[] = [];

  function flushCurrentFile() {
    if (currentFile) {
      currentFile.rawDiff = currentRawLines.join("\n");
      files.push(currentFile);
      currentFile = null;
      currentRawLines = [];
    }
  }

  function startNewFile(filename: string): ParsedDiffFile {
    flushCurrentFile();
    const cleanFn = filename ? filename.replace(/^[ab]\//, "").trim() : "";
    const created: ParsedDiffFile = {
      filename: cleanFn || defaultTitle,
      additions: 0,
      deletions: 0,
      lines: [],
      rawDiff: "",
    };
    currentFile = created;
    return created;
  }

  function ensureCurrentFile(): ParsedDiffFile {
    if (!currentFile) {
      currentFile = startNewFile(defaultTitle);
    }
    return currentFile;
  }

  let oldLine = 0;
  let newLine = 0;

  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i]!;

    if (line.startsWith("diff --git ")) {
      const match = line.match(/diff --git a\/(.*?) b\/(.*)/);
      const filename = match ? (match[2] || match[1]) : line.slice(11).trim();
      currentFile = startNewFile(filename || defaultTitle);
      currentRawLines.push(line);
      continue;
    }

    if (
      line.startsWith("*** Update File: ") ||
      line.startsWith("*** Add File: ") ||
      line.startsWith("*** Delete File: ")
    ) {
      const fn = line.replace(/^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*/, "").trim();
      currentFile = startNewFile(fn || defaultTitle);
      currentRawLines.push(line);
      continue;
    }

    if (line.startsWith("Index: ")) {
      const fn = line.slice(7).trim();
      currentFile = startNewFile(fn || defaultTitle);
      currentRawLines.push(line);
      continue;
    }

    if (line.startsWith("--- ")) {
      const active = currentFile as ParsedDiffFile | null;
      const isNewFileBoundary = Boolean(
        active && (
          active.lines.length > 0 ||
          active.additions > 0 ||
          active.deletions > 0 ||
          currentRawLines.some((l) => l.startsWith("+++ ") || l.startsWith("@@ "))
        )
      );
      if (isNewFileBoundary) {
        flushCurrentFile();
      }
      const fn = line.replace(/^---\s+(?:[ab]\/)?/, "").split("\t")[0]?.trim();
      if (!currentFile) {
        currentFile = startNewFile(fn || defaultTitle);
      }
      currentRawLines.push(line);
      continue;
    }

    if (line.startsWith("+++ ")) {
      const activeFile = ensureCurrentFile();
      const fn = line.replace(/^\+\+\+\s+(?:[ab]\/)?/, "").split("\t")[0]?.trim();
      if (
        fn &&
        fn !== "/dev/null" &&
        (activeFile.filename === defaultTitle || activeFile.filename === "diff" || activeFile.filename === "/dev/null")
      ) {
        activeFile.filename = fn.replace(/^[ab]\//, "").trim();
      }
      currentRawLines.push(line);
      continue;
    }

    // Metadata lines like `\ Eidos EOF newline: ...`, `\ Eidos line endings: ...`, `\ No newline at end of file`
    if (line.startsWith("\\")) {
      currentRawLines.push(line);
      continue;
    }

    if (line.startsWith("@@ ")) {
      const activeFile = ensureCurrentFile();
      currentRawLines.push(line);
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (match) {
        oldLine = parseInt(match[1]!, 10);
        newLine = parseInt(match[2]!, 10);
      }
      activeFile.lines.push({
        type: "hunk",
        text: line,
      });
      continue;
    }

    if (line.startsWith("+")) {
      const activeFile = ensureCurrentFile();
      currentRawLines.push(line);
      activeFile.additions++;
      activeFile.lines.push({
        type: "add",
        lineNumber: newLine ? newLine++ : undefined,
        text: line.slice(1),
      });
      continue;
    }

    if (line.startsWith("-")) {
      const activeFile = ensureCurrentFile();
      currentRawLines.push(line);
      activeFile.deletions++;
      activeFile.lines.push({
        type: "del",
        lineNumber: oldLine ? oldLine++ : undefined,
        text: line.slice(1),
      });
      continue;
    }

    if (
      line.startsWith("index ") ||
      line.startsWith("new file mode") ||
      line.startsWith("deleted file mode") ||
      line.startsWith("similarity index")
    ) {
      ensureCurrentFile();
      currentRawLines.push(line);
      continue;
    }

    // Context or plain line
    const activeFile = ensureCurrentFile();
    currentRawLines.push(line);
    const text = line.startsWith(" ") ? line.slice(1) : line;
    activeFile.lines.push({
      type: "context",
      lineNumber: newLine ? newLine++ : undefined,
      text,
    });
    if (oldLine) oldLine++;
  }

  flushCurrentFile();
  return files;
}

export const DiffViewer = memo(function DiffViewer({ diff = "", title = "代码差异" }: DiffViewerProps) {
  const [copiedFileIndex, setCopiedFileIndex] = useState<number | null>(null);

  const parsedFiles = useMemo(() => {
    return parseMultiFileDiff(diff, title);
  }, [diff, title]);

  const handleCopyFile = (rawContent: string, index: number) => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      void navigator.clipboard.writeText(rawContent).then(() => {
        setCopiedFileIndex(index);
        setTimeout(() => setCopiedFileIndex(null), 2000);
      });
    }
  };

  if (parsedFiles.length === 0) {
    return (
      <div className="diff-viewer-empty diff-view" role="region" aria-label="无代码差异">
        <span className="diff-viewer-empty-hint">暂无代码差异</span>
      </div>
    );
  }

  return (
    <div className="diff-viewer-group" role="region" aria-label="代码差异对比">
      {parsedFiles.map((file, fileIdx) => {
        const isCopied = copiedFileIndex === fileIdx;
        return (
          <div
            key={`${file.filename}-${fileIdx}`}
            className="diff-viewer-card diff-file-card diff-view"
            data-diff-header={`+++ b/${file.filename}`}
          >
            <div className="diff-file-header diff-viewer-header">
              <div className="diff-file-meta">
                <span className="diff-file-name">{file.filename}</span>
                {file.additions > 0 && <span className="diff-stat-add">+{file.additions}</span>}
                {file.deletions > 0 && <span className="diff-stat-del">-{file.deletions}</span>}
              </div>
              <button
                type="button"
                className="code-copy-btn"
                onClick={() => handleCopyFile(file.rawDiff || diff, fileIdx)}
                aria-label={isCopied ? "已复制代码差异" : "复制代码差异"}
                title={isCopied ? "已复制代码差异" : "复制代码差异"}
              >
                {isCopied ? (
                  <CheckmarkIcon className="copy-icon-svg copy-icon-svg--copied" />
                ) : (
                  <CopyIcon className="copy-icon-svg" />
                )}
              </button>
            </div>
            <pre className="diff-file-body diff-viewer-content">
              <code>
                {file.lines.map((line, lineIdx) => {
                  if (line.type === "hunk") {
                    return (
                      <div
                        key={lineIdx}
                        className="diff-line diff-line--hunk diff-row diff-row--hunk"
                      >
                        <span className="diff-hunk-text">{line.text}</span>
                      </div>
                    );
                  }
                  return (
                    <div
                      key={lineIdx}
                      className={`diff-line diff-line--${line.type} diff-row diff-row--${line.type}`}
                    >
                      <span className="diff-line-number" aria-hidden="true">
                        {line.lineNumber ?? ""}
                      </span>
                      <span className="diff-line-content">{line.text}</span>
                    </div>
                  );
                })}
              </code>
            </pre>
          </div>
        );
      })}
    </div>
  );
});


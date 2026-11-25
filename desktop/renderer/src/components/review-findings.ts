export type ReviewSeverity = "P0" | "P1" | "P2" | "P3";

export interface StructuredReviewFinding {
  id: string;
  severity: ReviewSeverity;
  title: string;
  path: string;
  line: number;
  body: string;
  suggestion?: string | undefined;
}

const FINDING_HEADER_RE =
  /^(?:#{1,6}\s+|[-*]\s+)?\[(P[0-3])\]\s+(.+?)\s+[—–-]{1,2}\s+`?([^\s`:]+):(\d+)`?\s*$/;
const SUGGESTION_LABEL_RE = /^\*\*建议修改\*\*\s*[：:]/;

export function parseStructuredReviewFindings(
  markdown: string,
): StructuredReviewFinding[] {
  if (!markdown || !markdown.includes("[P")) return [];
  const lines = markdown.split(/\r?\n/);
  const findings: StructuredReviewFinding[] = [];
  let current:
    | {
        severity: ReviewSeverity;
        title: string;
        path: string;
        line: number;
        bodyLines: string[];
        suggestionLines: string[];
        inSuggestion: boolean;
      }
    | null = null;

  const flush = () => {
    if (!current) return;
    const body = current.bodyLines.join("\n").trim();
    const suggestion = current.suggestionLines.join("\n").trim();
    findings.push({
      id: `${current.severity}:${current.path}:${current.line}:${findings.length}`,
      severity: current.severity,
      title: current.title.trim(),
      path: current.path.trim(),
      line: current.line,
      body,
      ...(suggestion ? { suggestion } : {}),
    });
    current = null;
  };

  let inFence = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      inFence = !inFence;
      if (current) {
        if (current.inSuggestion) current.suggestionLines.push(line);
        else current.bodyLines.push(line);
      }
      continue;
    }
    if (!inFence) {
      const headerMatch = FINDING_HEADER_RE.exec(trimmed);
      if (headerMatch) {
        flush();
        current = {
          severity: headerMatch[1] as ReviewSeverity,
          title: headerMatch[2]!,
          path: headerMatch[3]!,
          line: Number(headerMatch[4]),
          bodyLines: [],
          suggestionLines: [],
          inSuggestion: false,
        };
        continue;
      }
      if (current && SUGGESTION_LABEL_RE.test(trimmed)) {
        current.inSuggestion = true;
        const remainder = trimmed.replace(SUGGESTION_LABEL_RE, "").trim();
        if (remainder) current.suggestionLines.push(remainder);
        continue;
      }
    }
    if (current) {
      if (current.inSuggestion) current.suggestionLines.push(line);
      else current.bodyLines.push(line);
    }
  }
  flush();
  return findings;
}

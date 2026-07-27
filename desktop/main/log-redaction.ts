const SENSITIVE_KEY_REGEX = /api[-_]?key|secret|password|auth|token|cookie|credential|private[-_]?key/i;

export const MAX_LOG_META_DEPTH = 6;

const HEADERS_BEARER_REGEX = /((?:Authorization|authorization|Proxy-Authorization|proxy-authorization)\s*:\s*Bearer\s+)\S+/gi;
const HEADERS_GENERIC_REGEX = /((?:Authorization|authorization|Proxy-Authorization|proxy-authorization)\s*[:=]\s*)(?!Bearer\s+)\S+/gi;

const ENV_KEY_VALUE_REGEX = /((?:api_key|api-key|apiKey|OPENAI_API_KEY|DEEPSEEK_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|GH_TOKEN|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)\s*[:=]\s*)\S+/gi;
const JSON_FIELD_REGEX = /("(?:apiKey|api_key|authorization|token|accessToken|refreshToken)"\s*:\s*)("(?:[^"\\]|\\.)*"|[^\s,\}\]]+)/gi;

const SK_SECRET_REGEX = /\bsk-[A-Za-z0-9_-]{8,}\b/g;
const BEARER_STANDALONE_REGEX = /\bBearer\s+[A-Za-z0-9\-._~+/]+=*/g;
const JWT_REGEX = /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g;
const AWS_ID_REGEX = /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g;
const OPAQUE_TOKEN_REGEX = /\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}|glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9_-]{10,})\b/g;

/**
 * Redacts secrets, authorization tokens, API keys, and complete Cookie headers from a log line.
 */
export function redactLogLine(line: string): string {
  if (typeof line !== "string" || !line) return line;

  let redacted = line;

  // 1. JSON fields
  redacted = redacted.replace(JSON_FIELD_REGEX, '$1"[REDACTED]"');

  // 2. Key-value env / config pairs
  redacted = redacted.replace(ENV_KEY_VALUE_REGEX, '$1[REDACTED]');

  // 3. Headers (Bearer & generic auth)
  redacted = redacted.replace(HEADERS_BEARER_REGEX, '$1[REDACTED]');
  redacted = redacted.replace(HEADERS_GENERIC_REGEX, '$1[REDACTED]');

  // 4. Redact Complete Cookie / Set-Cookie header values with attribute preservation
  redacted = redacted.replace(
    /((?:Set-)?Cookie:\s*)([^\r\n]+)/gi,
    (_match, prefix, value) => {
      const semPos = value.indexOf(";");
      if (semPos !== -1) {
        const rest = value.slice(semPos);
        if (/\b(?:Path|HttpOnly|Secure|SameSite|Domain|Max-Age|Expires)=/i.test(rest) || /;\s*(?:HttpOnly|Secure)\b/i.test(rest)) {
          return `${prefix}[REDACTED]${rest}`;
        }
      }
      return `${prefix}[REDACTED]`;
    },
  );

  // 5. Standalone token patterns
  redacted = redacted.replace(JWT_REGEX, '[REDACTED]');
  redacted = redacted.replace(SK_SECRET_REGEX, '[REDACTED]');
  redacted = redacted.replace(AWS_ID_REGEX, '[REDACTED]');
  redacted = redacted.replace(OPAQUE_TOKEN_REGEX, '[REDACTED]');
  redacted = redacted.replace(BEARER_STANDALONE_REGEX, 'Bearer [REDACTED]');

  return redacted;
}

/**
 * Recursively sanitizes metadata object for structured logging.
 * Replaces sensitive keys, redacts secret patterns in strings, caps recursion depth,
 * prevents circular structure errors, and strips stack traces with potentially sensitive content.
 */
export function sanitizeLogValue(
  value: unknown,
  depth = 0,
  seen: WeakSet<object> = new WeakSet(),
): unknown {
  if (value === null || value === undefined) {
    return value;
  }

  if (typeof value === "string") {
    return redactLogLine(value);
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return value;
  }

  if (typeof value === "function") {
    return "[FUNCTION]";
  }

  if (typeof value === "symbol") {
    return value.toString();
  }

  if (depth >= MAX_LOG_META_DEPTH) {
    return "[MAX_DEPTH]";
  }

  if (typeof value === "object") {
    if (seen.has(value)) {
      return "[CIRCULAR]";
    }
    seen.add(value);

    if (value instanceof Error) {
      const errObj: Record<string, unknown> = {
        name: value.name,
        message: redactLogLine(value.message),
      };
      if ("code" in value && (value as { code?: unknown }).code !== undefined) {
        errObj.code = (value as { code?: unknown }).code;
      }
      return errObj;
    }

    if (Array.isArray(value)) {
      return value.map((item) => sanitizeLogValue(item, depth + 1, seen));
    }

    const sanitized: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(value)) {
      if (SENSITIVE_KEY_REGEX.test(key)) {
        sanitized[key] = "[REDACTED]";
      } else {
        sanitized[key] = sanitizeLogValue(val, depth + 1, seen);
      }
    }
    return sanitized;
  }

  return String(value);
}

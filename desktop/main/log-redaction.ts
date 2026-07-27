const SENSITIVE_KEY_REGEX = /api[-_]?key|secret|password|auth|token|cookie|credential|private[-_]?key/i;

export const MAX_LOG_META_DEPTH = 6;

/**
 * Redacts secrets, authorization tokens, API keys, and complete Cookie headers from a log line.
 */
export function redactLogLine(line: string): string {
  if (typeof line !== "string" || !line) return line;

  let redacted = line;

  // Redact Complete Cookie / Set-Cookie header values
  redacted = redacted.replace(
    /((?:Set-)?Cookie:\s*)([^\r\n]+)/gi,
    (_match, prefix, value) => {
      // Check if there are attributes like ; Path=/; Secure after cookie value in Set-Cookie
      const semPos = value.indexOf(";");
      if (semPos !== -1) {
        const rest = value.slice(semPos);
        // If rest only contains attributes like Path=, HttpOnly, Secure, SameSite, Domain, Max-Age, Expires
        if (/\b(?:Path|HttpOnly|Secure|SameSite|Domain|Max-Age|Expires)=/i.test(rest) || /;\s*(?:HttpOnly|Secure)\b/i.test(rest)) {
          return `${prefix}[REDACTED]${rest}`;
        }
      }
      return `${prefix}[REDACTED]`;
    },
  );

  // Redact Bearer / Authorization tokens
  redacted = redacted.replace(
    /(Authorization:\s*Bearer\s+)[^\r\n]+/gi,
    "$1[REDACTED]",
  );

  // Redact API keys (sk-..., etc.)
  redacted = redacted.replace(
    /\b(sk-[a-zA-Z0-9_-]{8,})\b/g,
    "[REDACTED]",
  );

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

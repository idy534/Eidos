/**
 * Log redaction module for Eidos Desktop.
 * Redacts sensitive content (API keys, tokens, credentials, cookies, headers)
 * before written to stdout, stderr, or log files.
 */

const HEADERS_BEARER_REGEX = /((?:Authorization|authorization|Proxy-Authorization|proxy-authorization)\s*:\s*Bearer\s+)\S+/gi;
const HEADERS_GENERIC_REGEX = /((?:Authorization|authorization|Proxy-Authorization|proxy-authorization)\s*[:=]\s*)(?!Bearer\s+)\S+/gi;
const COOKIE_REGEX = /((?:Cookie|Set-Cookie|cookie|set-cookie)\s*:\s*)[^;\r\n]+/gi;

const ENV_KEY_VALUE_REGEX = /((?:api_key|api-key|apiKey|OPENAI_API_KEY|DEEPSEEK_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|GH_TOKEN|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)\s*[:=]\s*)\S+/gi;

const JSON_FIELD_REGEX = /("(?:apiKey|api_key|authorization|token|accessToken|refreshToken)"\s*:\s*)("(?:[^"\\]|\\.)*"|[^\s,\}\]]+)/gi;

const SK_SECRET_REGEX = /\bsk-[A-Za-z0-9_-]{8,}\b/g;
const BEARER_STANDALONE_REGEX = /\bBearer\s+[A-Za-z0-9\-._~+/]+=*/g;
const JWT_REGEX = /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g;
const AWS_ID_REGEX = /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g;
const OPAQUE_TOKEN_REGEX = /\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}|glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9_-]{10,})\b/g;

export function redactLogLine(line: string): string {
  if (!line) return line;

  let redacted = line;

  // 1. JSON fields
  redacted = redacted.replace(JSON_FIELD_REGEX, '$1"[REDACTED]"');

  // 2. Key-value env / config pairs
  redacted = redacted.replace(ENV_KEY_VALUE_REGEX, '$1[REDACTED]');

  // 3. Headers
  redacted = redacted.replace(HEADERS_BEARER_REGEX, '$1[REDACTED]');
  redacted = redacted.replace(HEADERS_GENERIC_REGEX, '$1[REDACTED]');
  redacted = redacted.replace(COOKIE_REGEX, '$1[REDACTED]');

  // 4. Standalone token patterns
  redacted = redacted.replace(JWT_REGEX, '[REDACTED]');
  redacted = redacted.replace(SK_SECRET_REGEX, '[REDACTED]');
  redacted = redacted.replace(AWS_ID_REGEX, '[REDACTED]');
  redacted = redacted.replace(OPAQUE_TOKEN_REGEX, '[REDACTED]');
  redacted = redacted.replace(BEARER_STANDALONE_REGEX, 'Bearer [REDACTED]');

  return redacted;
}

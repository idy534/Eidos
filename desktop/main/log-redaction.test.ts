import test from "node:test";
import assert from "node:assert/strict";
import { redactLogLine, sanitizeLogValue, MAX_LOG_META_DEPTH } from "./log-redaction.js";

test("redactLogLine redacts multi-pair Cookie headers", () => {
  const line = "Cookie: session=abc; csrf=def; preferences=ghi";
  const redacted = redactLogLine(line);
  assert.equal(redacted, "Cookie: [REDACTED]");
  assert.equal(redacted.includes("abc"), false);
  assert.equal(redacted.includes("def"), false);
});

test("redactLogLine redacts Set-Cookie headers with attributes preserved", () => {
  const line = "Set-Cookie: session=abc12345; Path=/; HttpOnly; Secure";
  const redacted = redactLogLine(line);
  assert.equal(redacted, "Set-Cookie: [REDACTED]; Path=/; HttpOnly; Secure");
  assert.equal(redacted.includes("abc12345"), false);
});

test("redactLogLine redacts Bearer authorization header and API keys", () => {
  const line = "Authorization: Bearer secret-token-123 with key sk-abcdef12345678";
  const redacted = redactLogLine(line);
  assert.equal(redacted, "Authorization: Bearer [REDACTED] with key [REDACTED]");
  assert.equal(redacted.includes("sk-abcdef12345678"), false);
});

test("redacts lowercase authorization header and equal format", () => {
  assert.equal(redactLogLine("authorization: secretToken"), "authorization: [REDACTED]");
  assert.equal(redactLogLine("authorization=secretToken"), "authorization=[REDACTED]");
});

test("redacts Proxy-Authorization header", () => {
  assert.equal(
    redactLogLine("Proxy-Authorization: Bearer proxySecret"),
    "Proxy-Authorization: Bearer [REDACTED]",
  );
});

test("redacts api_key and apiKey assignments", () => {
  assert.equal(redactLogLine("api_key=sk-1234567890"), "api_key=[REDACTED]");
  assert.equal(redactLogLine("apiKey=my-secret-key"), "apiKey=[REDACTED]");
  assert.equal(redactLogLine("api-key=my-secret-key"), "api-key=[REDACTED]");
});

test("redacts vendor API key environment variables", () => {
  assert.equal(redactLogLine("OPENAI_API_KEY=sk-proj-1234567890"), "OPENAI_API_KEY=[REDACTED]");
  assert.equal(redactLogLine("DEEPSEEK_API_KEY=sk-deepseek-123456"), "DEEPSEEK_API_KEY=[REDACTED]");
  assert.equal(redactLogLine("ANTHROPIC_API_KEY=sk-ant-12345678"), "ANTHROPIC_API_KEY=[REDACTED]");
  assert.equal(redactLogLine("GITHUB_TOKEN=ghp_123456789012345678901234567890123456"), "GITHUB_TOKEN=[REDACTED]");
});

test("redacts AWS credential values", () => {
  assert.equal(redactLogLine("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"), "AWS_ACCESS_KEY_ID=[REDACTED]");
  assert.equal(
    redactLogLine("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    "AWS_SECRET_ACCESS_KEY=[REDACTED]",
  );
  assert.equal(redactLogLine("AWS_SESSION_TOKEN=AQoDYXdzEJr..."), "AWS_SESSION_TOKEN=[REDACTED]");
});

test("redacts JSON fields named apiKey, authorization, token, etc.", () => {
  assert.equal(
    redactLogLine('{"apiKey":"sk-secret-value"}'),
    '{"apiKey":"[REDACTED]"}',
  );
  assert.equal(
    redactLogLine('{"authorization":"Bearer token123"}'),
    '{"authorization":"[REDACTED]"}',
  );
  assert.equal(
    redactLogLine('{"token":"secret_tok_12345"}'),
    '{"token":"[REDACTED]"}',
  );
  assert.equal(
    redactLogLine('{"accessToken":"acc_12345", "refreshToken":"ref_67890"}'),
    '{"accessToken":"[REDACTED]", "refreshToken":"[REDACTED]"}',
  );
});

test("redacts standalone sk-* credentials and JWT tokens", () => {
  assert.equal(
    redactLogLine("Using sk-1234567890abcdef for request"),
    "Using [REDACTED] for request",
  );
  const jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c";
  assert.equal(
    redactLogLine(`Bearer ${jwt}`),
    "Bearer [REDACTED]",
  );
});

test("sanitizeLogValue recursively sanitizes objects and arrays", () => {
  const meta = {
    apiKey: "sk-secret1234567890",
    nested: {
      password: "my-password",
      message: "Header Cookie: session=123; user=foo",
      list: ["sk-key1234567890", { token: "abc" }],
    },
  };

  const sanitized = sanitizeLogValue(meta) as Record<string, unknown>;
  assert.equal(sanitized.apiKey, "[REDACTED]");
  const nested = sanitized.nested as Record<string, unknown>;
  assert.equal(nested.password, "[REDACTED]");
  assert.equal(nested.message, "Header Cookie: [REDACTED]");
  const list = nested.list as Array<unknown>;
  assert.equal(list[0], "[REDACTED]");
  assert.deepEqual(list[1], { token: "[REDACTED]" });
});

test("sanitizeLogValue handles circular references safely", () => {
  const circular: Record<string, unknown> = { name: "test" };
  circular.self = circular;

  assert.doesNotThrow(() => {
    const sanitized = sanitizeLogValue(circular) as Record<string, unknown>;
    assert.equal(sanitized.name, "test");
    assert.equal(sanitized.self, "[CIRCULAR]");
  });
});

test("sanitizeLogValue bounds depth at MAX_LOG_META_DEPTH", () => {
  let curr: Record<string, unknown> = { depth: 0 };
  const root = curr;
  for (let i = 1; i <= MAX_LOG_META_DEPTH + 2; i++) {
    const child: Record<string, unknown> = { depth: i };
    curr.child = child;
    curr = child;
  }

  const sanitized = sanitizeLogValue(root) as Record<string, unknown>;
  assert.ok(sanitized);

  let p: unknown = sanitized;
  for (let d = 0; d < MAX_LOG_META_DEPTH; d++) {
    p = (p as Record<string, unknown>).child;
  }
  assert.equal(p, "[MAX_DEPTH]");
});

test("sanitizeLogValue sanitizes Error objects without exposing unredacted stacks", () => {
  const err = new Error("Connection failed with Cookie: session=secret123");
  const sanitized = sanitizeLogValue(err) as Record<string, unknown>;
  assert.equal(sanitized.name, "Error");
  assert.equal(sanitized.message, "Connection failed with Cookie: [REDACTED]");
  assert.equal(sanitized.stack, undefined);
});

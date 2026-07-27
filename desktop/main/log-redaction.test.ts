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
  assert.equal(redacted, "Authorization: Bearer [REDACTED]");
  assert.equal(redacted.includes("sk-abcdef12345678"), false);
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

  // Traverse down to max depth
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

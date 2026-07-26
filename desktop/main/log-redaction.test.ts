import assert from "node:assert/strict";
import test from "node:test";

import { redactLogLine } from "./log-redaction.js";

test("redacts Bearer Authorization header", () => {
  const line = "Authorization: Bearer abc123def456";
  assert.equal(redactLogLine(line), "Authorization: Bearer [REDACTED]");
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

test("redacts Cookie and Set-Cookie headers", () => {
  assert.equal(redactLogLine("Cookie: session=12345"), "Cookie: [REDACTED]");
  assert.equal(redactLogLine("Set-Cookie: session=12345; Path=/"), "Set-Cookie: [REDACTED]; Path=/");
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

test("supports multiple secrets in one line", () => {
  const line = "OPENAI_API_KEY=sk-1234567890 and GITHUB_TOKEN=ghp_123456789012345678901234567890123456 in Authorization: Bearer abc123xyz";
  const expected = "OPENAI_API_KEY=[REDACTED] and GITHUB_TOKEN=[REDACTED] in Authorization: Bearer [REDACTED]";
  assert.equal(redactLogLine(line), expected);
});

test("preserves ordinary diagnostic text", () => {
  const line = "[runtime] Runtime initialized protocolVersion=1 runtimeVersion=0.3.0 runId=run-101 status=running";
  assert.equal(redactLogLine(line), line);
});

test("handles empty line", () => {
  assert.equal(redactLogLine(""), "");
});

test("handles very long input without throwing", () => {
  const longInput = "info: " + "a".repeat(100_000) + " OPENAI_API_KEY=sk-1234567890 " + "b".repeat(100_000);
  const redacted = redactLogLine(longInput);
  assert.ok(redacted.includes("OPENAI_API_KEY=[REDACTED]"));
  assert.equal(redacted.length, 200_000 + "info:  OPENAI_API_KEY=[REDACTED] ".length);
});

test("handles malformed JSON fragment without throwing", () => {
  const line = '{"apiKey": "sk-secret-value", "message": "incomplete...';
  assert.doesNotThrow(() => {
    const redacted = redactLogLine(line);
    assert.ok(redacted.includes('"apiKey": "[REDACTED]"') || redacted.includes('"apiKey":"[REDACTED]"'));
  });
});

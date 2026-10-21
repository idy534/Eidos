import assert from "node:assert/strict";
import test from "node:test";
import { terminalRunPresentation } from "./run-presentation.js";

test("uses the specific cause before the coarse Run error code", () => {
  const result = terminalRunPresentation({ status: "failed", errorCode: "MODEL_PROTOCOL_ERROR",
    termination: { stage: "tool_validation", reason: "TOOL_ARGUMENT_CONTRACT_VIOLATION", toolName: "read_file", validationPath: "path" },
  });
  assert.equal(result?.label, "模型传给“读取文件”的“文件路径”参数不符合要求");
});

test("distinguishes request timeout, account permission, and unknown model failure", () => {
  assert.match(terminalRunPresentation({ status: "failed", errorCode: "MODEL_REQUEST_FAILED",
    termination: { stage: "model", reason: "provider_timeout" },
  })!.label, /响应超时/);
  assert.match(terminalRunPresentation({ status: "failed", errorCode: "MODEL_REQUEST_FAILED",
    termination: { stage: "model", reason: "provider_unavailable", httpStatus: 504 },
  })!.label, /响应超时/);
  assert.match(terminalRunPresentation({ status: "failed", errorCode: "MODEL_AUTHENTICATION_FAILED",
    termination: { stage: "model", reason: "authentication_failed", httpStatus: 403 },
  })!.label, /模型服务拒绝访问，请检查账号权限/);
  assert.match(terminalRunPresentation({ status: "failed", errorCode: "MODEL_PROTOCOL_ERROR" })!.label, /无法处理模型响应/);
  assert.doesNotMatch(terminalRunPresentation({ status: "failed", errorCode: "SECRET_UNKNOWN_ERROR" })!.label, /SECRET|网络|重试/);
});

test("keeps operation uncertainty out of the task ending notice", () => {
  for (const status of ["succeeded", "failed", "stopped", "interrupted", "canceled"] as const) {
    assert.deepEqual(terminalRunPresentation({ status, reconciliationRequired: true }),
      terminalRunPresentation({ status, reconciliationRequired: false }));
  }
  assert.doesNotMatch(terminalRunPresentation({ status: "interrupted" })!.label, /重新启动|未能确认/);
  assert.equal(terminalRunPresentation({ status: "running" }), undefined);
});

test("distinguishes output truncation, malformed JSON, and tool contract violations", () => {
  const label = (reason: string) => terminalRunPresentation({
    status: "failed", errorCode: "MODEL_PROTOCOL_ERROR",
    termination: { stage: "model", reason },
  })!.label;
  assert.equal(label("length"), "模型输出达到 Token 上限，回复未完成");
  assert.equal(label("invalid_tool_arguments"), "模型返回的工具参数不是有效的 JSON 对象");
  assert.equal(label("TOOL_ARGUMENT_CONTRACT_VIOLATION"), "模型提供的工具参数不符合要求");
  assert.doesNotMatch(label("incomplete_response"), /Token|JSON|超时/);
  assert.doesNotMatch(label("content_filter"), /Token|JSON|超时/);
});

test("does not infer timeout or credential failure from unavailable or unknown causes", () => {
  for (const httpStatus of [500, 502, 503]) {
    const result = terminalRunPresentation({ status: "failed",
      termination: { stage: "model", reason: "provider_unavailable", httpStatus },
    });
    assert.match(result!.label, /暂时不可用/);
    assert.doesNotMatch(result!.label, /超时|API Key|账号/);
  }
});

test("terminal notices remain concise and have no sentence-ending period", () => {
  const reasons = [
    "MODEL_PROTOCOL_ERROR", "protocol_error", "invalid_response", "provider_control_syntax",
    "too_many_tool_calls", "error", "invalid_tool_arguments",
    "invalid_tool_call", "invalid_tool_results", "invalid_turn_signal", "empty_response",
    "length", "content_filter", "incomplete", "incomplete_response", "MODEL_STREAM_INTERRUPTED",
    "model_stream_interrupted", "MODEL_REQUEST_FAILED", "model_request_failed", "provider_unavailable",
    "provider_timeout", "MODEL_RATE_LIMITED", "rate_limited", "MODEL_AUTHENTICATION_FAILED",
    "authentication_failed", "MODEL_INVALID_REQUEST", "invalid_request", "CONTEXT_INPUT_TOO_LARGE",
    "context_exceeded", "CONTEXT_LIMIT_EXCEEDED", "CONTEXT_PROJECTION_OVERFLOW", "context_still_over_budget",
    "repeated_tool_call", "no_progress", "repeated_empty_response", "repeated_sensitive_tool_input",
    "SENSITIVE_SCAN_FAILED", "SENSITIVE_CONTENT_REJECTED", "TOOL_INFRASTRUCTURE_FAILURE",
    "RUNTIME_STATE_CONFLICT", "SKILL_REFERENCE_AMBIGUOUS", "SKILL_SNAPSHOT_INVALID", "INTERNAL_ERROR",
    "RUNTIME_INTERRUPTED", "active_shell_stopped", "TOOL_ARGUMENT_CONTRACT_VIOLATION", "unknown",
  ];
  for (const status of ["succeeded", "failed", "stopped", "interrupted", "canceled"] as const) {
    for (const reason of reasons) {
      for (const reconciliationRequired of [false, true]) {
        const { label } = terminalRunPresentation({ status, reconciliationRequired,
          termination: { stage: "model", reason },
        })!;
        assert.doesNotMatch(label, /[。.．]$|任务无法继续/, `${status}: ${reason}`);
        assert.ok(label.length <= 80, `${status}: ${reason}: ${label}`);
      }
    }
  }
});

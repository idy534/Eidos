import assert from "node:assert/strict";
import test from "node:test";
import { terminalRunPresentation } from "./run-presentation.js";

test("uses the specific cause before the coarse Run error code", () => {
  const result = terminalRunPresentation({ status: "failed", errorCode: "MODEL_PROTOCOL_ERROR",
    termination: { stage: "tool_validation", reason: "TOOL_ARGUMENT_CONTRACT_VIOLATION", toolName: "read_file", validationPath: "path" },
  });
  assert.equal(result?.label, "模型调用“读取文件”时提供的“文件路径”参数不符合要求，任务无法继续。");
});

test("distinguishes request timeout, account permission, and unknown model failure", () => {
  assert.match(terminalRunPresentation({ status: "failed", errorCode: "MODEL_REQUEST_FAILED",
    termination: { stage: "model", reason: "provider_timeout" },
  })!.label, /没有在等待时间内返回结果/);
  assert.match(terminalRunPresentation({ status: "failed", errorCode: "MODEL_REQUEST_FAILED",
    termination: { stage: "model", reason: "provider_unavailable", httpStatus: 504 },
  })!.label, /没有在等待时间内返回结果/);
  assert.match(terminalRunPresentation({ status: "failed", errorCode: "MODEL_AUTHENTICATION_FAILED",
    termination: { stage: "model", reason: "authentication_failed", httpStatus: 403 },
  })!.label, /账号是否有权/);
  assert.match(terminalRunPresentation({ status: "failed", errorCode: "MODEL_PROTOCOL_ERROR" })!.label, /未能正常处理这次模型请求/);
  assert.doesNotMatch(terminalRunPresentation({ status: "failed", errorCode: "SECRET_UNKNOWN_ERROR" })!.label, /SECRET|网络|重试/);
});

test("reports unknown operation results independently of lifecycle status", () => {
  for (const status of ["failed", "stopped", "interrupted", "canceled"] as const) {
    assert.match(terminalRunPresentation({ status, reconciliationRequired: true })!.label, /未能确认部分操作的结果/);
  }
  assert.doesNotMatch(terminalRunPresentation({ status: "interrupted" })!.label, /重新启动|未能确认/);
  assert.equal(terminalRunPresentation({ status: "running" }), undefined);
});

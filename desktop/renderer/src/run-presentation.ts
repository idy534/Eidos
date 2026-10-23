import type { Run } from "./contracts.js";
import type { RunTermination } from "../../shared/run-termination.generated.js";

export interface RunStatusPresentation {
  label: string;
  tone: "success" | "error" | "warning" | "neutral";
}

interface RunOutcome {
  status: Run["status"];
  errorCode?: string | null;
  stopReason?: string | null;
  termination?: RunTermination | null;
  reconciliationRequired?: boolean;
}

const messages: Record<string, string> = {
  MODEL_PROTOCOL_ERROR: "Eidos 无法处理模型响应",
  protocol_error: "Eidos 无法处理模型响应",
  invalid_response: "模型返回的内容不符合响应格式",
  provider_control_syntax: "模型将工具调用标记混入了回复正文",
  too_many_tool_calls: "模型单次返回的工具调用超过数量上限",
  invalid_tool_arguments: "模型返回的工具参数不是有效的 JSON 对象",
  invalid_tool_call: "模型返回的工具调用不符合要求",
  invalid_tool_results: "Eidos 无法将工具结果发送给模型",
  invalid_turn_signal: "模型返回的结束标记无效",
  empty_response: "模型未返回文字或工具调用",
  length: "模型输出达到 Token 上限，回复未完成",
  content_filter: "模型服务因内容限制终止了回复",
  incomplete: "模型回复不完整",
  incomplete_response: "模型回复不完整",
  error: "模型服务报告回复失败",
  MODEL_STREAM_INTERRUPTED: "模型回复在接收过程中中断",
  model_stream_interrupted: "模型回复在接收过程中中断",
  MODEL_REQUEST_FAILED: "Eidos 未能取得模型回复",
  model_request_failed: "Eidos 未能取得模型回复",
  provider_unavailable: "模型服务连接失败或暂时不可用",
  provider_timeout: "模型服务响应超时",
  MODEL_RATE_LIMITED: "模型服务限制了请求频率或用量",
  rate_limited: "模型服务限制了请求频率或用量",
  MODEL_AUTHENTICATION_FAILED: "模型服务拒绝认证，请检查 API Key 或账号权限",
  authentication_failed: "模型服务拒绝认证，请检查 API Key 或账号权限",
  MODEL_INVALID_REQUEST: "模型服务拒绝了请求参数",
  invalid_request: "模型服务拒绝了请求参数",
  CONTEXT_INPUT_TOO_LARGE: "请求内容超过模型上下文上限",
  context_exceeded: "请求内容超过模型上下文上限",
  CONTEXT_LIMIT_EXCEEDED: "Eidos 无法将请求缩减至模型上下文上限内",
  CONTEXT_PROJECTION_OVERFLOW: "Eidos 整理的请求内容超过处理上限",
  context_still_over_budget: "请求内容仍超过模型上下文上限",
  repeated_tool_call: "模型重复相同操作且无新进展，任务已停止",
  no_progress: "模型重试后仍无新进展，任务已停止",
  repeated_empty_response: "模型连续未返回文字或工具调用，任务已停止",
  repeated_sensitive_tool_input: "模型连续提交被安全规则拒绝的工具参数，任务已停止",
  SENSITIVE_SCAN_FAILED: "Eidos 无法完成内容安全检查",
  SENSITIVE_CONTENT_REJECTED: "内容未通过 Eidos 安全检查",
  TOOL_INFRASTRUCTURE_FAILURE: "Eidos 执行或清理工具失败",
  RUNTIME_STATE_CONFLICT: "任务执行状态冲突",
  SKILL_REFERENCE_AMBIGUOUS: "技能名称对应多个技能，Eidos 无法确定目标",
  SKILL_SNAPSHOT_INVALID: "任务使用的技能已变化或无法读取",
  INTERNAL_ERROR: "Eidos 内部错误导致任务失败",
  RUNTIME_INTERRUPTED: "任务执行已中断",
  HOOK_REQUESTED_STOP: "已信任的 Hook 已停止本次任务",
};

const toolNames: Record<string, string> = {
  read_file: "读取文件", read_file_range: "读取文件", list_files: "列出文件",
  search_text: "搜索文件内容", run_shell: "执行命令", write_stdin: "跟进命令",
  apply_patch: "修改文件", declare_outputs: "登记交付文件",
};
const parameterNames: Record<string, string> = {
  path: "文件路径", cwd: "工作目录", command: "命令", patch: "文件修改内容",
};

function failureMessage(run: RunOutcome): string {
  const fact = run.termination;
  if (fact?.reason === "TOOL_ARGUMENT_CONTRACT_VIOLATION") {
    const target = fact.toolName ? `传给“${toolNames[fact.toolName] ?? fact.toolName}”` : "提供";
    const field = fact.validationPath ? `“${parameterNames[fact.validationPath] ?? fact.validationPath}”参数` : "工具参数";
    return `模型${target}的${field}不符合要求`;
  }
  if (fact?.reason === "authentication_failed" && fact.httpStatus === 403) {
    return "模型服务拒绝访问，请检查账号权限";
  }
  if (fact?.reason === "provider_unavailable" && [408, 504].includes(fact.httpStatus ?? 0)) {
    return messages.provider_timeout!;
  }
  return (fact && messages[fact.reason])
    || (run.errorCode && messages[run.errorCode])
    || "任务执行失败，暂无详细说明";
}

export function terminalRunPresentation(run: RunOutcome): RunStatusPresentation | undefined {
  switch (run.status) {
    case "succeeded":
      return { label: "已完成", tone: "success" };
    case "failed":
      return { label: failureMessage(run), tone: "error" };
    case "stopped":
      return { label: messages[run.termination?.reason ?? run.stopReason ?? ""] ?? "任务已停止", tone: "warning" };
    case "interrupted":
      return { label: messages[run.termination?.reason ?? run.stopReason ?? ""] ?? "任务执行已中断", tone: "warning" };
    case "canceled":
      return { label: "任务已取消", tone: "neutral" };
    default:
      return undefined;
  }
}

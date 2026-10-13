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
  MODEL_PROTOCOL_ERROR: "Eidos 未能正常处理这次模型请求，任务无法继续。",
  protocol_error: "Eidos 未能正常处理这次模型请求，任务无法继续。",
  invalid_response: "Eidos 无法处理模型返回的内容，任务无法继续。",
  invalid_tool_arguments: "模型返回的工具参数格式不正确，任务无法继续。",
  invalid_tool_call: "模型返回的工具调用不符合要求，任务无法继续。",
  invalid_tool_results: "Eidos 无法将工具结果发送给模型，任务无法继续。",
  invalid_turn_signal: "模型返回的结束标记不符合要求，任务无法继续。",
  empty_response: "模型没有返回文字或可执行操作，任务无法继续。",
  length: "模型答复达到了长度上限，任务无法继续。",
  content_filter: "模型服务因内容限制停止了这次答复，任务无法继续。",
  incomplete: "模型没有完整返回这次答复，任务无法继续。",
  incomplete_response: "模型没有完整返回这次答复，任务无法继续。",
  MODEL_STREAM_INTERRUPTED: "模型答复在接收过程中中断，任务无法继续。",
  model_stream_interrupted: "模型答复在接收过程中中断，任务无法继续。",
  MODEL_REQUEST_FAILED: "Eidos 未能取得模型答复，任务无法继续。",
  model_request_failed: "Eidos 未能取得模型答复，任务无法继续。",
  provider_unavailable: "Eidos 未能连接模型服务，或模型服务暂时无法响应，任务无法继续。",
  provider_timeout: "模型服务没有在等待时间内返回结果，任务无法继续。",
  MODEL_RATE_LIMITED: "模型服务限制了本次请求，任务无法继续。",
  rate_limited: "模型服务限制了本次请求，任务无法继续。",
  MODEL_AUTHENTICATION_FAILED: "模型服务拒绝了访问，你需要检查 API Key 或账号权限。",
  authentication_failed: "模型服务拒绝了访问，你需要检查 API Key 或账号权限。",
  MODEL_INVALID_REQUEST: "模型服务拒绝了本次请求，任务无法继续。",
  invalid_request: "模型服务拒绝了本次请求，任务无法继续。",
  CONTEXT_INPUT_TOO_LARGE: "本次请求内容超过了模型可接收的长度，任务无法继续。",
  context_exceeded: "本次请求内容超过了模型可接收的长度，任务无法继续。",
  CONTEXT_LIMIT_EXCEEDED: "Eidos 无法将对话内容缩减到模型可接收的长度，任务无法继续。",
  CONTEXT_PROJECTION_OVERFLOW: "Eidos 整理的请求内容超过了处理上限，任务无法继续。",
  context_still_over_budget: "Eidos 无法将对话内容缩减到模型可接收的长度，任务已停止。",
  repeated_tool_call: "模型多次重复相同操作，任务没有取得新进展，Eidos 已停止执行。",
  no_progress: "模型再次尝试后仍没有取得新进展，Eidos 已停止执行。",
  repeated_empty_response: "模型连续没有返回文字或可执行操作，Eidos 已停止执行。",
  repeated_sensitive_tool_input: "模型连续提供了被安全规则拒绝的工具输入，Eidos 已停止执行。",
  SENSITIVE_SCAN_FAILED: "Eidos 无法完成内容安全检查，任务无法继续。",
  SENSITIVE_CONTENT_REJECTED: "Eidos 的安全规则拒绝了本次内容，任务无法继续。",
  TOOL_INFRASTRUCTURE_FAILURE: "Eidos 执行或清理工具时遇到错误，任务无法继续。",
  RUNTIME_STATE_CONFLICT: "任务状态在执行过程中发生冲突，Eidos 已停止执行。",
  SKILL_REFERENCE_AMBIGUOUS: "Eidos 找到了多个同名技能，无法确定本次任务要使用哪一个。",
  SKILL_SNAPSHOT_INVALID: "本次任务使用的技能已变化或无法读取，任务无法继续。",
  INTERNAL_ERROR: "Eidos 执行任务时遇到内部错误，任务无法继续。",
  RUNTIME_INTERRUPTED: "任务执行已中断。",
  active_shell_stopped: "任务已结束，Eidos 已停止仍在运行的命令。",
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
    const target = fact.toolName ? `“${toolNames[fact.toolName] ?? fact.toolName}”` : "工具";
    const field = fact.validationPath ? `的“${parameterNames[fact.validationPath] ?? fact.validationPath}”参数` : "的参数";
    return `模型调用${target}时提供${field}不符合要求，任务无法继续。`;
  }
  if (fact?.reason === "authentication_failed" && fact.httpStatus === 403) {
    return "模型服务拒绝了当前账号的访问，你需要检查账号是否有权使用该模型。";
  }
  if (fact?.reason === "provider_unavailable" && [408, 504].includes(fact.httpStatus ?? 0)) {
    return messages.provider_timeout!;
  }
  return (fact && messages[fact.reason])
    || (run.errorCode && messages[run.errorCode])
    || "任务执行失败，Eidos 没有取得具体原因。";
}

export function terminalRunPresentation(run: RunOutcome): RunStatusPresentation | undefined {
  let presentation: RunStatusPresentation;
  switch (run.status) {
    case "succeeded":
      if (!run.reconciliationRequired) return { label: "已完成", tone: "success" };
      return { label: "Eidos 尚未确认部分操作的结果，任务完成状态仍需检查。", tone: "warning" };
    case "failed":
      presentation = { label: failureMessage(run), tone: "error" };
      break;
    case "stopped":
      presentation = { label: messages[run.termination?.reason ?? run.stopReason ?? ""] ?? "任务已停止。", tone: "warning" };
      break;
    case "interrupted":
      presentation = { label: messages[run.termination?.reason ?? run.stopReason ?? ""] ?? "任务执行已中断。", tone: "warning" };
      break;
    case "canceled":
      presentation = { label: "任务已取消。", tone: "neutral" };
      break;
    default:
      return undefined;
  }
  if (run.reconciliationRequired) {
    presentation.label += " Eidos 未能确认部分操作的结果，你需要查看对应操作记录。";
  }
  return presentation;
}

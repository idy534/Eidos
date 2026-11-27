import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Item, Run } from "../contracts.js";
import type { InputReference } from "../../../shared/input-context.js";
import { QUERY_CLIPBOARD_MIME, buildQueryClipboardPayload, serializeQueryClipboardPayload } from "../../../shared/query-clipboard.js";
import { clearQueryClipboardMirror, readQueryFromClipboardEvent, writeQueryToClipboard } from "../query-clipboard.js";
import { ArtifactProvider } from "./ArtifactContext.js";
import { ExecutionFeed } from "./ExecutionFeed.js";
import { InputContextProvider } from "./InputContext.js";
import { expandDetails } from "../../test/expand-details.js";

const baseRun: Run = {
  id: "run-shell-feed",
  sessionId: "session-shell-feed",
  status: "running",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 1_000,
  startedAt: 1_000,
  updatedAt: 1_000,
};

function shellItem(overrides: Partial<Item> = {}): Item {
  return {
    id: "shell-item",
    sessionId: baseRun.sessionId,
    runId: baseRun.id,
    ordinal: 1,
    kind: "command_execution",
    status: "in_progress",
    createdAt: 1_000,
    content: "first line\n",
    toolCall: {
      id: "tool-shell-feed",
      itemId: "shell-item",
      modelStepIndex: 1,
      batchOrder: 0,
      providerCallId: "provider-shell-feed",
      toolName: "run_shell",
      status: "running",
      startedAt: 1_000,
      argumentsJson: JSON.stringify({ command: "pnpm test:fast" }),
    },
    ...overrides,
  };
}

function renderFeed(item: Item, run: Run = baseRun) {
  return render(
    <ExecutionFeed
      items={[item]}
      runs={[run]}
      approvals={[]}
    />,
  );
}

describe("Shell display intent", () => {
  it("shows the intent with real status and keeps the command inside details", async () => {
    const shell = shellItem();
    const toolCall = { ...shell.toolCall!, displayIntent: "检查项目测试是否通过" };
    const { container, rerender } = renderFeed({ ...shell, toolCall });
    const summary = screen.getByText("运行中 · 检查项目测试是否通过");
    const details = summary.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    await expandDetails(container);
    expect(within(details).getByText("pnpm test:fast")).toBeVisible();
    expect(within(details).getByRole("button", { name: /复制/ })).toBeVisible();

    rerender(<ExecutionFeed runs={[baseRun]} approvals={[]} items={[{
      ...shell, status: "failed", toolCall: { ...toolCall, status: "failed" },
    }]} />);
    expect(screen.getByText("失败 · 检查项目测试是否通过")).toBeVisible();
  });

  it("uses the existing command summary for absent and blank intents", () => {
    const shell = shellItem();
    const { rerender } = renderFeed(shell);
    expect(screen.getByText("正在运行 pnpm test:fast")).toBeVisible();
    rerender(<ExecutionFeed runs={[baseRun]} approvals={[]} items={[{
      ...shell, toolCall: { ...shell.toolCall!, displayIntent: "   " },
    }]} />);
    expect(screen.getByText("正在运行 pnpm test:fast")).toBeVisible();
  });
});

describe("response model label", () => {
  it("uses the configured name, then the saved remote ID, then the legacy ID", () => {
    const run: Run = {
      ...baseRun, status: "succeeded", modelId: "custom-deleted", remoteModelId: "company/model-v1",
    };
    const item = shellItem({
      id: "answer", kind: "assistant_message", status: "completed", content: "回答", toolCall: undefined,
    });
    const model = {
      id: run.modelId, name: "公司模型", vendor: "公司", provider: "custom", url: "https://example.com",
      supportsToolCall: true, supportsImages: false, supportsReasoning: false,
    };
    const { rerender } = render(<ExecutionFeed items={[item]} runs={[run]} models={[model]} approvals={[]} />);
    expect(screen.getByTitle("本次回复模型：公司模型")).toHaveTextContent("公司模型");

    rerender(<ExecutionFeed items={[item]} runs={[run]} models={[]} approvals={[]} />);
    expect(screen.getByTitle("本次回复模型：company/model-v1")).toHaveTextContent("company/model-v1");

    const { remoteModelId: _remoteModelId, ...legacyRun } = run;
    rerender(<ExecutionFeed items={[item]} runs={[legacyRun]} models={[]} approvals={[]} />);
    expect(screen.getByTitle("本次回复模型：custom-deleted")).toHaveTextContent("custom-deleted");
  });
});

describe("automation source and tool results", () => {
  it("keeps steering input inside the running process and updates its receipt in place", () => {
    const initial = shellItem({ id: "user", kind: "user_message", ordinal: 1, content: "原任务", status: "completed", toolCall: undefined });
    const steering = shellItem({ id: "steer", kind: "user_message", ordinal: 3, content: "补充要求", toolCall: undefined });
    const props = { runs: [baseRun], approvals: [], onApprove: vi.fn(), onReject: vi.fn() };
    const { rerender } = render(<ExecutionFeed {...props} items={[initial, shellItem({ ordinal: 2 }), steering]} />);
    const process = screen.getByText(/^正在处理/).closest("details")!;
    expect(within(process).getByText("补充要求")).toBeInTheDocument();
    expect(within(process).getByText("引导对话")).toBeInTheDocument();
    expect(process.querySelector(".process-steering .user-message-bubble")).toBeInTheDocument();
    expect(process.querySelector(".process-steering")?.getAttribute("data-status")).toBe("in_progress");
    expect(screen.getAllByText("原任务")).toHaveLength(1);
    rerender(<ExecutionFeed {...props} items={[initial, shellItem({ ordinal: 2 }), { ...steering, status: "completed", completedAt: 2_000 }]} />);
    expect(within(process).getByText("引导对话")).toBeInTheDocument();
    expect(process.querySelector(".process-steering")?.getAttribute("data-status")).toBe("completed");
    expect(screen.getAllByText("补充要求")).toHaveLength(1);
  });

  it("identifies scheduled input in the conversation", () => {
    const run = { ...baseRun, automationOrigin: { automationId: "task", revision: 1, occurrenceId: "occurrence", scheduledFor: "2026-10-15T01:00:00Z" } };
    renderFeed(shellItem({ kind: "user_message", content: "检查状态。", toolCall: undefined }), run);
    expect(screen.getByText("定时任务自动执行")).toBeInTheDocument();
    expect(screen.getByText("检查状态。")).toBeInTheDocument();
  });

  it("identifies channel origin input with DingTalk, Feishu, and WeCom message labels", () => {
    const dingtalkRun = {
      ...baseRun,
      channelOrigin: { channel: "dingtalk" as const, messageId: "m1", senderId: "u1", conversationId: "c1" },
    };
    const { rerender } = renderFeed(shellItem({ kind: "user_message", content: "钉钉来的消息", toolCall: undefined }), dingtalkRun);
    expect(screen.getByText("钉钉消息")).toBeInTheDocument();

    const feishuRun = {
      ...baseRun,
      channelOrigin: { channel: "feishu" as const, messageId: "m2", senderId: "u2", conversationId: "c2" },
    };
    rerender(
      <ExecutionFeed
        items={[shellItem({ kind: "user_message", content: "飞书来的消息", toolCall: undefined })]}
        runs={[feishuRun]}
        approvals={[]}
      />,
    );
    expect(screen.getByText("飞书消息")).toBeInTheDocument();

    const wecomRun = {
      ...baseRun,
      channelOrigin: { channel: "wecom" as const, messageId: "m3", senderId: "u3", conversationId: "c3" },
    };
    rerender(
      <ExecutionFeed
        items={[shellItem({ kind: "user_message", content: "企微来的消息", toolCall: undefined })]}
        runs={[wecomRun]}
        approvals={[]}
      />,
    );
    expect(screen.getByText("企业微信消息")).toBeInTheDocument();
  });

  it("renders an empty feed without showing empty-history hint and transitions to populated items safely", () => {
    const props = { runs: [baseRun], approvals: [], onApprove: vi.fn(), onReject: vi.fn() };
    const { container, rerender } = render(<ExecutionFeed {...props} items={[]} />);
    expect(screen.queryByText(/还没有执行记录/)).not.toBeInTheDocument();
    expect(screen.queryByText(/暂时是没有执行记录/)).not.toBeInTheDocument();
    expect(container.querySelector(".feed-empty")).toBeNull();
    expect(container.querySelector(".feed-shell")).not.toBeNull();

    const userItem = shellItem({ id: "user", kind: "user_message", ordinal: 1, content: "新任务", status: "completed", toolCall: undefined });
    rerender(<ExecutionFeed {...props} items={[userItem]} />);
    expect(screen.getByText("新任务")).toBeInTheDocument();
    expect(screen.queryByText(/还没有执行记录/)).not.toBeInTheDocument();
  });

  it("renders the automation card only for the trusted builtin tool", () => {
    const item = shellItem({ kind: "tool_call", status: "completed", toolCall: {
      ...shellItem().toolCall!, toolName: "automation", status: "completed",
      argumentsJson: JSON.stringify({ mode: "preview" }),
      provenance: { kind: "builtin", sourceId: "eidos.automation", sourceVersion: "v1", contentHash: "hash" },
      resultJson: JSON.stringify({ outcome: "success", code: "ok", data: { response: { kind: "preview", nextRuns: ["2026-10-16T01:00:00Z"] } } }),
    } });
    const { rerender } = renderFeed(item);
    expect(screen.getByText("执行时间预览")).toBeInTheDocument();
    rerender(<ExecutionFeed items={[{ ...item, toolCall: { ...item.toolCall!, provenance: { kind: "mcp", sourceId: "external", sourceVersion: "v1", contentHash: "hash" } } }]}
      runs={[baseRun]} approvals={[]} />);
    expect(screen.queryByText("执行时间预览")).not.toBeInTheDocument();
  });

  it("renders the automation card with AutomationsIcon and task name", () => {
    const item = shellItem({ kind: "tool_call", status: "completed", toolCall: {
      ...shellItem().toolCall!, toolName: "automation", status: "completed",
      argumentsJson: JSON.stringify({ mode: "create" }),
      provenance: { kind: "builtin", sourceId: "eidos.automation", sourceVersion: "v1", contentHash: "hash" },
      resultJson: JSON.stringify({
        outcome: "success",
        code: "ok",
        data: {
          response: {
            kind: "mutation",
            changed: true,
            automation: {
              id: "11111111-1111-1111-1111-111111111111",
              revision: 1,
              name: "每小时喝水提醒",
              prompt: "提醒喝水。",
              kind: "cron",
              schedule: { expression: "0 * * * *", timezone: "Asia/Shanghai", runAt: null },
              status: "ACTIVE",
              model: { modelId: "test", reasoningSelection: null },
              approvalMode: "auto_review",
              target: { kind: "new_session", projectId: null, execution: { executionMode: "local", baseRef: null } },
              createdAt: 1,
              updatedAt: 1,
            },
          },
        },
      }),
    } });
    const { container } = renderFeed(item);
    const summary = container.querySelector("details.tool-item > summary");
    expect(summary).not.toBeNull();
    const icon = summary?.querySelector(".tool-icon svg circle");
    expect(icon).toHaveAttribute("r", "6.2");
  });
});

describe("conversation fork actions", () => {
  it("automatically loads earlier history when scrolling up near the top without a manual button", () => {
    const onLoadOlder = vi.fn();
    const items: Item[] = [{ id: "recent", runId: baseRun.id, sessionId: baseRun.sessionId,
      kind: "user_message", ordinal: 1, status: "completed", content: "最近输入", createdAt: 1 }];
    const { container, rerender } = render(<ExecutionFeed items={items} runs={[baseRun]} approvals={[]}
      onLoadOlder={onLoadOlder} />);
    expect(screen.queryByRole("button", { name: "加载更早的记录" })).not.toBeInTheDocument();
    const feed = container.querySelector(".feed") as HTMLElement;
    fireEvent.wheel(feed, { deltaY: -100 });
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
    rerender(<ExecutionFeed items={items} runs={[baseRun]} approvals={[]}
      onLoadOlder={onLoadOlder} loadingOlder />);
    expect(screen.getByText("正在读取历史…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /历史|记录/ })).not.toBeInTheDocument();
    fireEvent.wheel(feed, { deltaY: -100 });
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
  });
  const answer = (run: Run, overrides: Partial<Item> = {}): Item => ({
    id: "answer", sessionId: run.sessionId, runId: run.id, ordinal: 2,
    kind: "assistant_message", status: "completed", createdAt: 1_000, content: "回答内容", ...overrides,
  });

  it("forks an older ended answer even when a later Run is active", () => {
    const oldRun = { ...baseRun, id: "old", status: "succeeded" as const };
    const selected = answer(oldRun);
    const onFork = vi.fn();
    render(<ExecutionFeed items={[selected, answer(baseRun, { id: "stream", status: "in_progress" })]}
      runs={[oldRun, baseRun]} approvals={[]} onFork={onFork} />);
    expect(screen.getAllByRole("button", { name: "从这里分叉" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "从这里分叉" }));
    expect(onFork).toHaveBeenCalledWith(selected);
  });

  it("keeps inherited answers forkable without exposing execution revision or feedback", () => {
    const historical = { ...baseRun, status: "succeeded" as const, inherited: true };
    const selected = answer(historical, { inherited: true });
    render(<ExecutionFeed items={[selected]} runs={[historical]} approvals={[]}
      onFork={() => {}} allowRegenerate allowEditResend allowFeedback />);
    expect(screen.getByRole("button", { name: "从这里分叉" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "重新生成" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "点赞" })).not.toBeInTheDocument();
  });

  it("does not offer a fork for incomplete answers or progress inside a tool round", () => {
    const ended = { ...baseRun, status: "stopped" as const };
    render(<ExecutionFeed items={[answer(ended, { incomplete: true })]} runs={[ended]} approvals={[]}
      onFork={() => {}} />);
    expect(screen.queryByRole("button", { name: "从这里分叉" })).not.toBeInTheDocument();
  });
});

describe("rejected drafts", () => {
  const draft = (ordinal: number, overrides: Partial<Item> = {}): Item => ({
    id: `draft-${ordinal}`, sessionId: baseRun.sessionId, runId: baseRun.id, ordinal,
    kind: "assistant_message", status: "failed", incomplete: true, createdAt: 1_000 + ordinal,
    content: `重新打开浏览器 ${ordinal}`, ...overrides,
  });
  const user: Item = {
    id: "user", sessionId: baseRun.sessionId, runId: baseRun.id, ordinal: 1,
    kind: "user_message", status: "completed", createdAt: 1_000, content: "去浏览器看看",
  };

  it("marks only the draft the Run stopped on as unfinished", async () => {
    const canceled = { ...baseRun, status: "canceled" as const };
    const { container } = render(<ExecutionFeed items={[user, draft(2), draft(3), draft(4)]} runs={[canceled]} approvals={[]} />);
    expect(screen.getAllByText("回答未完成")).toHaveLength(1);
    expect(screen.getByText("重新打开浏览器 4").closest(".feed-item--assistant")).not.toBeNull();
    await expandDetails(container);
    expect(screen.getByText("重新打开浏览器 2").closest(".process-text")).not.toBeNull();
    expect(screen.getAllByText("回答未完成")).toHaveLength(1);
  });

  it("drops the marker once a later attempt replaces the draft", () => {
    render(<ExecutionFeed items={[user, draft(2), draft(3, { status: "completed", incomplete: false, content: "浏览器插件未安装" })]}
      runs={[{ ...baseRun, status: "succeeded" }]} approvals={[]} />);
    expect(screen.queryByText("回答未完成")).not.toBeInTheDocument();
    expect(screen.getByText("浏览器插件未安装").closest(".feed-item--assistant")).not.toBeNull();
  });
});

describe("Run failure presentation", () => {
  it("shows a concrete cause without technical diagnostics in the Session body", () => {
    const run: Run = { ...baseRun, status: "failed", errorCode: "MODEL_PROTOCOL_ERROR",
      termination: { stage: "tool_validation", reason: "TOOL_ARGUMENT_CONTRACT_VIOLATION", toolName: "read_file", validationPath: "path" },
    };
    renderFeed({ id: "user", ordinal: 0, sessionId: run.sessionId, runId: run.id,
      kind: "user_message", status: "completed", createdAt: 1_000, content: "读取文件",
    }, run);
    expect(screen.getByRole("alert")).toHaveTextContent("模型传给“读取文件”的“文件路径”参数不符合要求");
    expect(screen.getByRole("alert")).not.toHaveTextContent("MODEL_PROTOCOL_ERROR");
    expect(screen.queryByText("诊断信息")).not.toBeInTheDocument();
    expect(screen.queryByText("错误码")).not.toBeInTheDocument();
    expect(screen.queryByText("MODEL_PROTOCOL_ERROR")).not.toBeInTheDocument();
    expect(screen.queryByText("TOOL_ARGUMENT_CONTRACT_VIOLATION")).not.toBeInTheDocument();
  });

  it("renders standalone turn results with proper standalone styling before run notice when canceled during editing", () => {
    const run: Run = { ...baseRun, status: "canceled" };
    const diff = "diff --git a/test.ts b/test.ts\n--- a/test.ts\n+++ b/test.ts\n@@ -1,1 +1,2 @@\n-old\n+new\n+added\n";
    const editItem: Item = {
      id: "edit-item",
      sessionId: run.sessionId,
      runId: run.id,
      ordinal: 1,
      kind: "file_change",
      status: "completed",
      createdAt: 1_000,
      toolCall: {
        id: "tool-edit-feed",
        itemId: "edit-item",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "provider-edit-feed",
        toolName: "apply_patch",
        status: "completed",
        startedAt: 1_000,
        completedAt: 1_100,
        changeDiff: diff,
      },
    };
    const { container } = render(
      <ExecutionFeed
        items={[
          { id: "user", ordinal: 0, sessionId: run.sessionId, runId: run.id, kind: "user_message", status: "completed", createdAt: 1_000, content: "请修改代码" },
          editItem,
        ]}
        runs={[run]}
        approvals={[]}
      />,
    );
    const turnResultsSection = container.querySelector(".turn-results");
    expect(turnResultsSection).toBeInTheDocument();
    expect(turnResultsSection).toHaveClass("turn-results--standalone");
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("任务已取消");
    expect(notice).toHaveClass("run-notice", "run-notice--neutral");
  });
});

describe("ExecutionFeed chat typography scope", () => {
  it("scopes user, progress and final prose without including tool controls", () => {
    const message = (id: string, ordinal: number, kind: Item["kind"], content: string): Item => ({
      id, ordinal, kind, content, sessionId: baseRun.sessionId, runId: baseRun.id,
      status: "completed", createdAt: 1_000,
    });
    const { container } = render(
      <ExecutionFeed
        items={[
          message("user", 0, "user_message", "第一行\n第二行"),
          message("progress", 1, "assistant_message", "我正在检查。"),
          shellItem({ ordinal: 2 }),
          message("answer", 3, "assistant_message", "**结论**\n\n- 第一项\n- 第二项"),
        ]}
        runs={[baseRun]}
        approvals={[]}
      />,
    );

    expect(container.querySelector(".user-message-bubble p.eidos-chat-prose")?.textContent).toBe("第一行\n第二行");
    expect(container.querySelector(".process-text > .markdown-body.eidos-chat-prose")).not.toBeNull();
    expect(container.querySelector(".feed-item--assistant > .markdown-body.eidos-chat-prose strong")).toHaveTextContent("结论");
    expect(container.querySelectorAll(".eidos-chat-prose")).toHaveLength(3);
    expect(container.querySelector(".eidos-chat-prose .tool-item, .eidos-chat-prose .feed-item-footer")).toBeNull();
  });
});

describe("ExecutionFeed shell output", () => {
  it("updates from cumulative live output to a completed result without duplicating it", async () => {
    const activeItem = shellItem();
    const { container, rerender } = renderFeed(activeItem);

    await expandDetails(container);
    expect(screen.getByText("first line")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();

    const completedItem = shellItem({
      status: "completed",
      completedAt: 2_000,
      content: "first line\nsecond line\n",
      toolCall: {
        ...activeItem.toolCall!,
        status: "completed",
        completedAt: 2_000,
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Command completed",
          data: {
            stdout: "first line\nsecond line\n",
            stderr: "",
            exitCode: 0,
          },
        }),
      },
    });
    rerender(
      <ExecutionFeed
        items={[completedItem]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
      />,
    );

    const output = container.querySelector(".shell-output");
    expect(output?.textContent).toBe("first line\nsecond line\n");
    expect(container.querySelectorAll(".shell-output")).toHaveLength(1);
    expect(screen.getByText("✓ 成功")).toBeInTheDocument();
  });

  it("renders capsule exit code badge next to 失败 and hides shell_exit_nonzero on command failure", async () => {
    const failedItem = shellItem({
      status: "failed",
      completedAt: 2_000,
      content: "zsh:1: no matches found: --include=*.py\n",
      toolCall: {
        ...shellItem().toolCall!,
        status: "completed",
        completedAt: 2_000,
        resultJson: JSON.stringify({
          outcome: "error",
          code: "shell_exit_nonzero",
          summary: "Command did not succeed (termination=exit)",
          data: {
            stdout: "",
            stderr: "zsh:1: no matches found: --include=*.py\n",
            exitCode: 1,
          },
        }),
      },
    });
    const { container } = render(
      <ExecutionFeed
        items={[failedItem]}
        runs={[{ ...baseRun, status: "failed", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
      />,
    );

    await expandDetails(container);
    expect(screen.queryByText(/shell_exit_nonzero/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Command did not succeed/)).not.toBeInTheDocument();
    const pill = container.querySelector(".shell-exit-code-pill");
    expect(pill).toBeInTheDocument();
    expect(pill?.textContent).toBe("退出码 1");
    expect(screen.getByText("失败")).toBeInTheDocument();
  });

  it("copies only the raw shell command when the copy button is clicked", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    const item = shellItem({
      status: "completed",
      toolCall: {
        ...shellItem().toolCall!,
        argumentsJson: JSON.stringify({ command: 'grep -rn "TODO" .' }),
      },
    });

    const { container } = renderFeed(item);
    await expandDetails(container);
    const copyButton = container.querySelector(".shell-copy-button") as HTMLButtonElement | null;
    expect(copyButton).toBeInTheDocument();
    expect(copyButton?.title).toBe("复制命令");

    fireEvent.click(copyButton!);

    expect(writeTextMock).toHaveBeenCalledWith('grep -rn "TODO" .');
    await waitFor(() => {
      expect(copyButton?.title).toBe("已复制命令");
    });
  });

  it("highlights the shell command with bash syntax tokens while keeping output plain text", async () => {
    const item = shellItem({
      status: "completed",
      content: "output line\n",
      toolCall: {
        ...shellItem().toolCall!,
        status: "completed",
        argumentsJson: JSON.stringify({ command: 'cd "/workspace" && git checkout -q HEAD' }),
      },
    });

    const { container } = renderFeed(item);
    await expandDetails(container);

    const prompt = container.querySelector(".shell-command-prompt");
    expect(prompt?.textContent).toBe("$ ");

    await waitFor(() => {
      const coloredTokens = container.querySelectorAll(".shell-command-code span[style*='color']");
      expect(coloredTokens.length).toBeGreaterThan(0);
    }, { timeout: 3000 });

    const output = container.querySelector(".shell-output");
    expect(output?.textContent).toBe("output line\n");
    expect(output?.querySelectorAll("span[style*='color']").length ?? 0).toBe(0);
  });

  it("starts a shell details group collapsed and preserves manual expansion", () => {
    const { container, rerender } = renderFeed(shellItem());
    const detailsBefore = container.querySelector("details.tool-item--shell");
    expect(detailsBefore).not.toBeNull();
    expect(detailsBefore!.open).toBe(false);
    fireEvent.click(detailsBefore!.querySelector("summary")!);
    expect(detailsBefore!.open).toBe(true);

    rerender(
      <ExecutionFeed
        items={[shellItem({
          status: "completed",
          completedAt: 2_000,
          toolCall: {
            ...shellItem().toolCall!,
            status: "completed",
            completedAt: 2_000,
            resultJson: JSON.stringify({
              outcome: "success",
              code: "ok",
              summary: "Command completed",
              data: { stdout: "first line\n", stderr: "", exitCode: 0 },
            }),
          },
        })]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
      />,
    );

    const detailsAfter = container.querySelector("details.tool-item--shell");
    expect(detailsAfter).not.toBeNull();
    expect(detailsAfter!.open).toBe(true);
  });

  it("keeps a shell details group collapsed after the user closes it during streaming", () => {
    const { container, rerender } = renderFeed(shellItem());
    const detailsBefore = container.querySelector("details.tool-item--shell");
    expect(detailsBefore).not.toBeNull();
    fireEvent.click(detailsBefore!.querySelector("summary")!);
    expect(detailsBefore!.open).toBe(true);
    fireEvent.click(detailsBefore!.querySelector("summary")!);
    expect(detailsBefore!.open).toBe(false);

    rerender(
      <ExecutionFeed
        items={[shellItem({ content: "first line\nsecond line\n" })]}
        runs={[baseRun]}
        approvals={[]}
      />,
    );
    const detailsDuring = container.querySelector("details.tool-item--shell");
    expect(detailsDuring).not.toBeNull();
    expect(detailsDuring!.open).toBe(false);

    rerender(
      <ExecutionFeed
        items={[shellItem({
          status: "completed",
          completedAt: 2_000,
          content: "first line\nsecond line\n",
          toolCall: {
            ...shellItem().toolCall!,
            status: "completed",
            completedAt: 2_000,
            resultJson: JSON.stringify({
              outcome: "success",
              code: "ok",
              summary: "Command completed",
              data: { stdout: "first line\nsecond line\n", stderr: "", exitCode: 0 },
            }),
          },
        })]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
      />,
    );
    const detailsAfter = container.querySelector("details.tool-item--shell");
    expect(detailsAfter).not.toBeNull();
    expect(detailsAfter!.open).toBe(false);
  });
});

describe("ExecutionFeed streaming follow", () => {
  function assistantItem(content: string): Item {
    return {
      id: "streaming-answer",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "assistant_message",
      status: "in_progress",
      createdAt: 1_000,
      content,
    };
  }

  function renderStreamingFeed(content: string) {
    return render(
      <ExecutionFeed
        items={[assistantItem(content)]}
        runs={[baseRun]}
        approvals={[]}
      />,
    );
  }

  function mockFeedMetrics(feed: HTMLElement, metrics: { scrollHeight: number; scrollTop: number; clientHeight: number }) {
    Object.defineProperty(feed, "scrollHeight", { value: metrics.scrollHeight, configurable: true });
    Object.defineProperty(feed, "clientHeight", { value: metrics.clientHeight, configurable: true });
    feed.scrollTop = metrics.scrollTop;
  }

  it("coalesces rapid deltas into a single frame follow while at the bottom", () => {
    const rafQueue: FrameRequestCallback[] = [];
    const requestSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      rafQueue.push(callback);
      return rafQueue.length;
    });
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    try {
      const { container, rerender } = renderStreamingFeed("hello");
      const feed = container.querySelector(".feed") as HTMLElement;
      mockFeedMetrics(feed, { scrollHeight: 1_000, scrollTop: 400, clientHeight: 600 });
      while (rafQueue.length > 0) rafQueue.shift()!(16);
      expect(feed.scrollTop).toBe(1_000);
      requestSpy.mockClear();

      mockFeedMetrics(feed, { scrollHeight: 1_200, scrollTop: 1_000, clientHeight: 600 });
      rerender(
        <ExecutionFeed
          items={[assistantItem("hello wo")]}
          runs={[baseRun]}
          approvals={[]}
        />,
      );
      rerender(
        <ExecutionFeed
          items={[assistantItem("hello world")]}
          runs={[baseRun]}
          approvals={[]}
        />,
      );
      expect(requestSpy).toHaveBeenCalledTimes(1);

      mockFeedMetrics(feed, { scrollHeight: 1_200, scrollTop: 1_000, clientHeight: 600 });
      while (rafQueue.length > 0) rafQueue.shift()!(16);
      expect(feed.scrollTop).toBe(1_200);
      expect(cancelSpy).not.toHaveBeenCalled();
    } finally {
      requestSpy.mockRestore();
      cancelSpy.mockRestore();
    }
  });

  it("does not yank the viewport after the user scrolls up", () => {
    const rafQueue: FrameRequestCallback[] = [];
    const requestSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      rafQueue.push(callback);
      return rafQueue.length;
    });
    try {
      const { container, rerender } = renderStreamingFeed("hello");
      const feed = container.querySelector(".feed") as HTMLElement;
      mockFeedMetrics(feed, { scrollHeight: 1_000, scrollTop: 400, clientHeight: 600 });
      while (rafQueue.length > 0) rafQueue.shift()!(16);
      requestSpy.mockClear();

      mockFeedMetrics(feed, { scrollHeight: 2_000, scrollTop: 0, clientHeight: 600 });
      fireEvent.scroll(feed);
      expect(screen.getByRole("button", { name: "滚动到最新内容" })).not.toHaveAttribute("hidden");

      mockFeedMetrics(feed, { scrollHeight: 2_200, scrollTop: 0, clientHeight: 600 });
      rerender(
        <ExecutionFeed
          items={[assistantItem("hello world, still streaming")]}
          runs={[baseRun]}
          approvals={[]}
        />,
      );
      expect(requestSpy).not.toHaveBeenCalled();
      while (rafQueue.length > 0) rafQueue.shift()!(16);
      expect(feed.scrollTop).toBe(0);
    } finally {
      requestSpy.mockRestore();
    }
  });

  it("cancels a pending follow when unmounted", () => {
    const rafQueue: FrameRequestCallback[] = [];
    const requestSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      rafQueue.push(callback);
      return rafQueue.length;
    });
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {
      rafQueue.length = 0;
    });
    try {
      const { unmount } = renderStreamingFeed("hello");
      expect(requestSpy).toHaveBeenCalled();
      unmount();
      expect(cancelSpy).toHaveBeenCalled();
    } finally {
      requestSpy.mockRestore();
      cancelSpy.mockRestore();
    }
  });
});

describe("ExecutionFeed process group collapse behavior", () => {
  it("starts expanded while running and automatically collapses when transitioning to terminal status", () => {
    const activeItem = shellItem();
    const { container, rerender } = renderFeed(activeItem);

    const processGroup = container.querySelector("details.process-group");
    expect(processGroup).not.toBeNull();
    expect(processGroup).toHaveAttribute("open");
    expect(screen.getByText(/正在处理/)).toBeInTheDocument();

    const completedItem = shellItem({
      status: "completed",
      completedAt: 2_000,
      toolCall: {
        ...activeItem.toolCall!,
        status: "completed",
        completedAt: 2_000,
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Command completed",
          data: { stdout: "first line\n", stderr: "", exitCode: 0 },
        }),
      },
    });

    rerender(
      <ExecutionFeed
        items={[
          completedItem,
          {
            id: "final-response",
            sessionId: baseRun.sessionId,
            runId: baseRun.id,
            ordinal: 2,
            kind: "assistant_message",
            status: "completed",
            createdAt: 2_000,
            completedAt: 2_000,
            content: "这是工作结果",
          },
        ]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
      />,
    );

    // Process group should now be collapsed
    const processGroupAfter = container.querySelector("details.process-group");
    expect(processGroupAfter).not.toBeNull();
    expect(processGroupAfter).not.toHaveAttribute("open");
    expect(screen.getByText(/已处理/)).toBeInTheDocument();

    // Result should be visible
    expect(screen.getByText("这是工作结果")).toBeInTheDocument();

    // Clicking summary manually re-expands the process group
    fireEvent.click(processGroupAfter!.querySelector("summary")!);
    expect(processGroupAfter).toHaveAttribute("open");

    // Subsequent re-render preserves the user's manual expansion
    rerender(
      <ExecutionFeed
        items={[
          completedItem,
          {
            id: "final-response",
            sessionId: baseRun.sessionId,
            runId: baseRun.id,
            ordinal: 2,
            kind: "assistant_message",
            status: "completed",
            createdAt: 2_000,
            completedAt: 2_000,
            content: "这是工作结果",
          },
        ]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_500 }]}
        approvals={[]}
      />,
    );
    expect(processGroupAfter).toHaveAttribute("open");
  });

  it("starts collapsed when loaded with an already-completed run", () => {
    const completedItem = shellItem({
      status: "completed",
      completedAt: 2_000,
      toolCall: {
        ...shellItem().toolCall!,
        status: "completed",
        completedAt: 2_000,
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Command completed",
          data: { stdout: "first line\n", stderr: "", exitCode: 0 },
        }),
      },
    });

    const { container } = render(
      <ExecutionFeed
        items={[completedItem]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
      />,
    );

    const processGroup = container.querySelector("details.process-group");
    expect(processGroup).not.toBeNull();
    expect(processGroup).not.toHaveAttribute("open");
    expect(screen.getByText(/已处理/)).toBeInTheDocument();
  });
});

describe("ExecutionFeed plan and clarification rendering", () => {
  function toolCallItem(toolName: string, overrides: Partial<Item> = {}, toolCallOverrides: Partial<ToolCall> = {}): Item {
    return {
      id: "tool-item-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "command_execution",
      status: "in_progress",
      createdAt: 1_000,
      content: "",
      toolCall: {
        id: "tool-call-1",
        itemId: "tool-item-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "provider-call-1",
        toolName,
        status: "running",
        startedAt: 1_000,
        argumentsJson: "{}",
        ...toolCallOverrides,
      },
      ...overrides,
    };
  }

  it.each(["completed", "failed", "canceled"] as const)("shows clarification errors with %s item status without inventing a skipped answer", async (status) => {
    const item = toolCallItem("request_user_input", {status}, {
      status: status === "completed" ? "completed" : status,
      argumentsJson: JSON.stringify({questions: [{id: "q", question: "Constraints?", type: "text"}]}),
      resultJson: JSON.stringify({outcome: "error", code: "invalid_arguments", summary: "No question was submitted"}),
    });
    render(<ExecutionFeed items={[item]} runs={[baseRun]} approvals={[]}
    />);
    await expandDetails();
    expect(screen.queryByText(/已询问/)).toBeNull();
    expect(screen.queryByText("已跳过")).toBeNull();
    expect(screen.getByText("No question was submitted")).toBeInTheDocument();
  });

  it("renders active request_user_input in progress with question count and waiting prompt", () => {
    const item = toolCallItem(
      "request_user_input",
      { status: "in_progress" },
      {
        argumentsJson: JSON.stringify({
          questions: [
            { id: "q1", question: "第一题？" },
            { id: "q2", question: "第二题？" },
          ],
        }),
      },
    );

    render(
      <ExecutionFeed
        items={[item]}
        runs={[baseRun]}
        approvals={[]}
      />,
    );

    expect(screen.getByText("正在询问 2 个问题")).toBeInTheDocument();
    expect(screen.getByText("正在等待你的回答")).toBeInTheDocument();
  });

  it("renders completed request_user_input as collapsible summary with questions and answers", async () => {
    const item = toolCallItem(
      "request_user_input",
      { status: "completed", completedAt: 2_000 },
      {
        status: "completed",
        completedAt: 2_000,
        argumentsJson: JSON.stringify({
          questions: [
            {
              id: "scope",
              question: "选择范围？",
              options: [
                { id: "frontend", label: "前端" },
                { id: "backend", label: "后端" },
              ],
            },
          ],
        }),
        resultJson: JSON.stringify({
          outcome: "success",
          data: {
            response: {
              status: "answered",
              answers: [
                {
                  questionId: "scope",
                  optionIds: ["frontend"],
                  text: "补充说明",
                },
              ],
            },
          },
        }),
      },
    );

    render(
      <ExecutionFeed
        items={[item]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000 }]}
        approvals={[]}
      />,
    );

    await expandDetails();
    expect(screen.getByText("已询问 1 个问题")).toBeInTheDocument();
    expect(screen.getByText("选择范围？")).toBeInTheDocument();
    expect(screen.getByText("前端；补充说明")).toBeInTheDocument();
  });

  it("renders write_plan in progress with running indicator", () => {
    const item = toolCallItem(
      "write_plan",
      { status: "in_progress" },
      {
        argumentsJson: JSON.stringify({ title: "重构计划" }),
      },
    );

    render(
      <ExecutionFeed
        items={[item]}
        runs={[baseRun]}
        approvals={[]}
      />,
    );

    expect(screen.getByText("正在制定计划…")).toBeInTheDocument();
  });

  it.each(["failed", "canceled", "declined", "completed"] as const)("shows an unsuccessful write_plan with %s item status as a tool result", async (status) => {
    const item = toolCallItem("write_plan", {status}, {
      status: status === "declined" ? "failed" : status,
      argumentsJson: JSON.stringify({title: "Rejected plan"}),
      resultJson: JSON.stringify({outcome: "error", code: "plan_write_rejected", summary: "plan_not_found"}),
    });
    render(<ExecutionFeed items={[item]} runs={[baseRun]} approvals={[]}
    />);
    await expandDetails();
    expect(screen.queryByText("查看计划 →")).toBeNull();
    expect(screen.getByText("plan_not_found")).toBeInTheDocument();
  });

  it("renders completed write_plan card and invokes onOpenPlan when clicked", () => {
    const onOpenPlan = vi.fn();
    const item = toolCallItem(
      "write_plan",
      { status: "completed", completedAt: 2_000 },
      {
        status: "completed",
        completedAt: 2_000,
        argumentsJson: JSON.stringify({ title: "系统重构方案" }),
        resultJson: JSON.stringify({ outcome: "success" }),
      },
    );

    render(
      <ExecutionFeed
        items={[item]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000 }]}
        approvals={[]}
        onOpenPlan={onOpenPlan}
      />,
    );

    expect(screen.getByText("系统重构方案")).toBeInTheDocument();
    expect(screen.getByText("查看计划 →")).toBeInTheDocument();

    const planCard = screen.getByRole("button", { name: /系统重构方案/ });
    fireEvent.click(planCard);
    expect(onOpenPlan).toHaveBeenCalledTimes(1);
  });

  it("renders write_plan in the response area outside process group, even when other tools exist", async () => {
    const readFileItem = toolCallItem(
      "read_file",
      { id: "read-1", ordinal: 1, status: "completed", completedAt: 1_500 },
      {
        id: "call-read-1",
        toolName: "read_file",
        status: "completed",
        argumentsJson: JSON.stringify({ path: "src/index.ts" }),
        resultJson: JSON.stringify({ outcome: "success", data: { content: "code" } }),
      },
    );

    const planItem = toolCallItem(
      "write_plan",
      { id: "plan-1", ordinal: 2, status: "completed", completedAt: 2_000 },
      {
        id: "call-plan-1",
        toolName: "write_plan",
        status: "completed",
        // Title in resultJson data (simulating fallback or snapshot)
        argumentsJson: "{}",
        resultJson: JSON.stringify({
          outcome: "success",
          data: { title: "从结果中读取的计划标题", planId: "p1" },
        }),
      },
    );

    const { container } = render(
      <ExecutionFeed
        items={[readFileItem, planItem]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000 }]}
        approvals={[]}
      />,
    );

    await expandDetails(container, "details.process-group");
    // Process group should contain read_file
    const processGroup = container.querySelector("details.process-group");
    expect(processGroup).not.toBeNull();
    expect(processGroup?.textContent).toContain("已读取");

    // Process group should NOT contain write_plan
    expect(processGroup?.querySelector(".tool-item--plan-card")).toBeNull();

    // write_plan should be in the response area outside process group, aligned with assistant feed items
    const planWrapper = container.querySelector(".feed-item--assistant.feed-item--plan");
    expect(planWrapper).not.toBeNull();
    const planCard = container.querySelector(".tool-item--plan-card");
    expect(planCard).not.toBeNull();
    expect(planWrapper?.contains(planCard!)).toBe(true);
    expect(planCard?.textContent).toContain("从结果中读取的计划标题");
  });

  it("renders subagent tool as direct button and calls onOpenSubagent on click", async () => {
    const onOpenSubagent = vi.fn();
    const spawnItem: Item = {
      id: "spawn-item",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1_000,
      toolCall: {
        id: "tc-spawn",
        itemId: "spawn-item",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-spawn",
        toolName: "spawn_agent",
        status: "completed",
        startedAt: 1_000,
        completedAt: 1_200,
        argumentsJson: JSON.stringify({ task_name: "reviewer", role: "worker" }),
        resultJson: JSON.stringify({ outcome: "success", data: { agent: { id: "child-42", taskName: "reviewer" } } }),
      },
    };

    const { container } = render(
      <ExecutionFeed
        items={[spawnItem]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000 }]}
        approvals={[]}
        onOpenSubagent={onOpenSubagent}
      />,
    );

    await expandDetails(container, "details.process-group");
    const btn = container.querySelector("button.tool-file-link");
    expect(btn).not.toBeNull();
    expect(btn?.textContent).toBe("reviewer");
    expect(container.textContent).toContain("创建智能体 · reviewer");
    expect(container.querySelector("details.tool-item")).toBeNull();

    fireEvent.click(btn!);
    expect(onOpenSubagent).toHaveBeenCalledWith({ id: "child-42", taskName: "reviewer" });
  });

  it("shows error summary in title attribute on hover when subagent tool fails", async () => {
    const errorItem: Item = {
      id: "spawn-err",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "failed",
      createdAt: 1_000,
      toolCall: {
        id: "tc-err",
        itemId: "spawn-err",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-err",
        toolName: "spawn_agent",
        status: "failed",
        startedAt: 1_000,
        completedAt: 1_200,
        argumentsJson: JSON.stringify({ task_name: "failing-worker" }),
        resultJson: JSON.stringify({ outcome: "error", summary: "Resource quota exceeded" }),
      },
    };

    const { container } = render(
      <ExecutionFeed
        items={[errorItem]}
        runs={[{ ...baseRun, status: "failed", completedAt: 2_000 }]}
        approvals={[]}
        onOpenSubagent={() => {}}
      />,
    );

    await expandDetails(container, "details.process-group");
    const toolRow = container.querySelector(".tool-item--read-done");
    expect(toolRow).not.toBeNull();
    expect(toolRow?.getAttribute("title")).toBe("Resource quota exceeded");
    expect(container.querySelector(".tool-summary--error")).toBeNull();
  });
});

describe("ExecutionFeed regenerate confirmation", () => {
  const terminalRun: Run = {
    id: "run-terminal",
    sessionId: "session-terminal",
    status: "succeeded",
    modelId: "deepseek-v4-flash",
    modelStepCount: 1,
    createdAt: 1_000,
    startedAt: 1_000,
    updatedAt: 2_000,
    completedAt: 2_000,
  };

  const userItem: Item = {
    id: "user-msg",
    sessionId: terminalRun.sessionId,
    runId: terminalRun.id,
    ordinal: 1,
    kind: "user_message",
    status: "completed",
    createdAt: 1_000,
    content: "请帮我写代码",
  };

  const assistantItem: Item = {
    id: "assistant-msg",
    sessionId: terminalRun.sessionId,
    runId: terminalRun.id,
    ordinal: 2,
    kind: "assistant_message",
    status: "completed",
    createdAt: 1_500,
    completedAt: 2_000,
    content: "这是回答内容",
  };

  it("requires secondary confirmation before calling onRegenerate", async () => {
    const onRegenerate = vi.fn().mockResolvedValue(undefined);
    render(
      <ExecutionFeed
        items={[userItem, assistantItem]}
        runs={[terminalRun]}
        approvals={[]}
        onRegenerate={onRegenerate}
      />,
    );

    const retryBtn = screen.getByRole("button", { name: "重新回答" });
    expect(retryBtn).toBeInTheDocument();

    // 1. Click retry button -> dialog appears, onRegenerate not yet called
    fireEvent.click(retryBtn);
    expect(onRegenerate).not.toHaveBeenCalled();

    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("重新回答？")).toBeInTheDocument();
    expect(screen.getByText("重新回答将放弃当前的回答并重新生成。确定要继续吗？")).toBeInTheDocument();

    // 2. Click cancel button -> dialog closes, onRegenerate still not called, and tooltip does not linger
    const cancelBtn = screen.getByRole("button", { name: "取消" });
    fireEvent.click(cancelBtn);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onRegenerate).not.toHaveBeenCalled();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    // 3. Open dialog again, click confirm button -> dialog closes, onRegenerate called with run
    fireEvent.click(retryBtn);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    const dialogConfirmBtn = within(screen.getByRole("alertdialog")).getByRole("button", { name: "重新回答" });
    fireEvent.click(dialogConfirmBtn);
    expect(onRegenerate).toHaveBeenCalledTimes(1);
    expect(onRegenerate).toHaveBeenCalledWith(terminalRun);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("cancels confirmation on Escape key without triggering onRegenerate", () => {
    const onRegenerate = vi.fn().mockResolvedValue(undefined);
    render(
      <ExecutionFeed
        items={[userItem, assistantItem]}
        runs={[terminalRun]}
        approvals={[]}
        onRegenerate={onRegenerate}
      />,
    );

    const retryBtn = screen.getByRole("button", { name: "重新回答" });
    fireEvent.click(retryBtn);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onRegenerate).not.toHaveBeenCalled();
  });

  it("cancels confirmation on backdrop click without triggering onRegenerate", () => {
    const onRegenerate = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <ExecutionFeed
        items={[userItem, assistantItem]}
        runs={[terminalRun]}
        approvals={[]}
        onRegenerate={onRegenerate}
      />,
    );

    const retryBtn = screen.getByRole("button", { name: "重新回答" });
    fireEvent.click(retryBtn);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    const backdrop = container.querySelector(".modal-backdrop");
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop!);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onRegenerate).not.toHaveBeenCalled();
  });
});

describe("ExecutionFeed view_image tool rendering and lightbox", () => {
  it.each([
    { status: "failed" as const, path: "missing.png" },
    { status: "completed" as const, path: "/tmp/outside.png" },
  ])("does not request a preview for $status image at $path", async ({ status, path }) => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const prepare = vi.fn().mockResolvedValue("eidos-preview://unexpected");
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { prepareWorkspacePreview: prepare } });
    const imageItem: Item = {
      id: "image-item", sessionId: baseRun.sessionId, runId: baseRun.id, ordinal: 1,
      kind: "tool_call", status: status === "failed" ? "failed" : "completed", createdAt: 1000,
      toolCall: {
        id: "image-tool", itemId: "image-item", modelStepIndex: 1, batchOrder: 0,
        providerCallId: "image-call", toolName: "view_image", status,
        argumentsJson: JSON.stringify({ path }),
        resultJson: JSON.stringify({ outcome: status === "failed" ? "error" : "success", code: status === "failed" ? "file_not_found" : "ok", summary: "Image", data: { path, mime: "image/png" } }),
      },
    };
    try {
      const { container } = render(<ArtifactProvider value={{ sessionId: baseRun.sessionId, executionRoot: "/workspace", openFile: vi.fn(), openBrowser: vi.fn() }}>
        <ExecutionFeed items={[imageItem]} runs={[baseRun]} approvals={[]} />
      </ArtifactProvider>);
      const details = container.querySelector<HTMLDetailsElement>("details.tool-item")!;
      details.open = true;
      fireEvent(details, new Event("toggle"));
      if (status === "completed") expect(await screen.findByText("暂无预览")).toBeInTheDocument();
      else await waitFor(() => expect(container.querySelector(".tool-image-error")).not.toBeNull());
      expect(prepare).not.toHaveBeenCalled();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("renders view_image and opens fullscreen preview when thumbnail is clicked", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        onNotification: vi.fn().mockReturnValue(vi.fn()),
        prepareWorkspacePreview: vi.fn().mockResolvedValue("eidos-preview://test-token/assets/flowchart.png"),
        releaseWorkspacePreview: vi.fn().mockResolvedValue(undefined),
      },
    });

    const openFile = vi.fn();
    const imageItem: Item = {
      id: "view-image-item",
      sessionId: "session-view-image",
      runId: "run-view-image",
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-img-1",
        itemId: "view-image-item",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-img-1",
        toolName: "view_image",
        status: "completed",
        startedAt: 1000,
        completedAt: 1200,
        argumentsJson: JSON.stringify({ path: "assets/flowchart.png" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Image loaded",
          data: {
            path: "assets/flowchart.png",
            mime: "image/png",
            size: 30720,
            sha256: "hash123",
          },
        }),
      },
    };

    const finishedRun: Run = {
      ...baseRun,
      id: "run-view-image",
      sessionId: "session-view-image",
      status: "succeeded",
      completedAt: 1200,
    };

    try {
      render(
        <ArtifactProvider
          value={{
            sessionId: "session-view-image",
            executionRoot: "/workspace",
            openFile,
            openBrowser: vi.fn(),
          }}
        >
          <ExecutionFeed
            items={[imageItem]}
            runs={[finishedRun]}
            approvals={[]}
            onOpenFile={openFile}
          />
        </ArtifactProvider>,
      );

      await expandDetails(document.body, "details.process-group");
      // Summary label should show completed status and filename
      expect(screen.getByText("已查看图像 · flowchart.png")).toBeInTheDocument();

      // Open details
      const details = screen.getByText("已查看图像 · flowchart.png").closest("details");
      expect(details).not.toBeNull();
      fireEvent.click(details!.querySelector("summary")!);
      if (!details!.open) {
        details!.open = true;
        fireEvent(details!, new Event("toggle"));
      }

      // Thumbnail with preview image should be rendered
      const thumbnailBtn = await screen.findByRole("button", { name: "预览 flowchart.png" });
      expect(thumbnailBtn).toBeInTheDocument();

      // Hover title should show absolute path and size, without appearing in body text
      expect(thumbnailBtn).toHaveAttribute(
        "title",
        expect.stringMatching(/assets\/flowchart\.png.*30\.0 KB/),
      );
      expect(screen.queryByRole("button", { name: "assets/flowchart.png" })).not.toBeInTheDocument();

      // Click thumbnail to open lightbox modal
      fireEvent.click(thumbnailBtn);
      const dialog = await screen.findByRole("dialog", { name: "flowchart.png 图片预览" });
      expect(dialog).toBeInTheDocument();

      // Close lightbox
      const closeBtn = screen.getByRole("button", { name: "关闭图片预览" });
      fireEvent.click(closeBtn);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("renders 暂无预览 fallback with neutral styling when preview fails", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        onNotification: vi.fn().mockReturnValue(vi.fn()),
        prepareWorkspacePreview: vi.fn().mockRejectedValue(new Error("preview error")),
        releaseWorkspacePreview: vi.fn().mockResolvedValue(undefined),
      },
    });

    const openFile = vi.fn();
    const imageItem: Item = {
      id: "view-image-item-err",
      sessionId: "session-view-image",
      runId: "run-view-image",
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-img-err",
        itemId: "view-image-item-err",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-img-err",
        toolName: "view_image",
        status: "completed",
        startedAt: 1000,
        completedAt: 1200,
        argumentsJson: JSON.stringify({ path: "assets/corrupted.png" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Image loaded",
          data: {
            path: "assets/corrupted.png",
            mime: "image/png",
            size: 30720,
          },
        }),
      },
    };

    const finishedRun: Run = {
      ...baseRun,
      id: "run-view-image",
      sessionId: "session-view-image",
      status: "succeeded",
      completedAt: 1200,
    };

    try {
      render(
        <ArtifactProvider
          value={{
            sessionId: "session-view-image",
            executionRoot: "/workspace",
            openFile,
            openBrowser: vi.fn(),
          }}
        >
          <ExecutionFeed
            items={[imageItem]}
            runs={[finishedRun]}
            approvals={[]}
            onOpenFile={openFile}
          />
        </ArtifactProvider>,
      );

      await expandDetails(document.body, "details.process-group");
      const details = screen.getByText("已查看图像 · corrupted.png").closest("details");
      details!.open = true;
      fireEvent(details!, new Event("toggle"));

      const fallback = await screen.findByText("暂无预览");
      expect(fallback).toBeInTheDocument();
      expect(fallback).toHaveClass("tool-image-empty");
      expect(screen.queryByText(/预览不可用/)).not.toBeInTheDocument();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  function viewImageItem(name: string, ordinal: number, options: { saved?: boolean; failed?: boolean } = {}): Item {
    const failed = options.failed === true;
    return {
      id: `image-${name}`, sessionId: baseRun.sessionId, runId: baseRun.id, ordinal,
      kind: "tool_call", status: failed ? "failed" : "completed", createdAt: 1000,
      toolCall: {
        id: `tool-${name}`, itemId: `image-${name}`, modelStepIndex: 1, batchOrder: ordinal,
        providerCallId: `call-${name}`, toolName: "view_image", status: failed ? "failed" : "completed",
        argumentsJson: JSON.stringify({ path: `shots/${name}.png` }),
        resultJson: JSON.stringify(failed
          ? { outcome: "error", code: "image_not_found", summary: "Image file was not found", data: {} }
          : {
            outcome: "success", code: "ok", summary: "Image loaded",
            data: { path: `/workspace/shots/${name}.png`, mime: "image/png", size: 2048 },
            ...(options.saved === false ? {} : { attachments: [{ index: 0, kind: "image", mime: "image/png", bytes: 2048 }] }),
          }),
      },
    };
  }

  it("shows the saved image for a single view_image whose file is outside the workspace", async () => {
    const prepareToolContentPreview = vi.fn(async () => "eidos-preview://token/tool/0.png");
    const prepareWorkspacePreview = vi.fn();
    vi.stubGlobal("eidosRuntime", { prepareToolContentPreview, prepareWorkspacePreview, releaseInputPreview: vi.fn(async () => undefined) });
    const item = viewImageItem("outside", 1);
    item.toolCall!.argumentsJson = JSON.stringify({ path: "/skills/demo/outside.png" });
    try {
      const { container } = render(<ArtifactProvider value={{ sessionId: baseRun.sessionId, executionRoot: "/workspace", openFile: vi.fn(), openBrowser: vi.fn() }}>
        <ExecutionFeed items={[item]} runs={[baseRun]} approvals={[]} />
      </ArtifactProvider>);
      await expandDetails(container, "details.tool-item");
      expect(await screen.findByRole("button", { name: "预览 outside.png" })).toBeInTheDocument();
      expect(prepareToolContentPreview).toHaveBeenCalledWith(baseRun.sessionId, "tool-outside", 0);
      expect(prepareWorkspacePreview).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("groups adjacent view_image calls into a strip and steps through them in the preview", async () => {
    const prepareToolContentPreview = vi.fn(async (_sessionId: string, toolCallId: string) => `eidos-preview://token/${toolCallId}.png`);
    const prepareWorkspacePreview = vi.fn(async (_sessionId: string, path: string) => `eidos-preview://file/${path}`);
    vi.stubGlobal("eidosRuntime", {
      onNotification: vi.fn().mockReturnValue(vi.fn()),
      prepareToolContentPreview,
      prepareWorkspacePreview,
      releaseInputPreview: vi.fn(async () => undefined),
      releaseWorkspacePreview: vi.fn(async () => undefined),
    });
    const items = [
      viewImageItem("one", 1),
      viewImageItem("two", 2),
      viewImageItem("missing", 3, { failed: true }),
      // Recorded before results carried a saved image: falls back to the Workspace file.
      viewImageItem("legacy", 4, { saved: false }),
    ];
    try {
      const { container } = render(<ArtifactProvider value={{ sessionId: baseRun.sessionId, executionRoot: "/workspace", openFile: vi.fn(), openBrowser: vi.fn() }}>
        <ExecutionFeed items={items} runs={[baseRun]} approvals={[]} />
      </ArtifactProvider>);

      // The group starts collapsed: only the summary is visible and no preview is requested.
      expect(screen.getByText("已查看 3 张图像")).toBeInTheDocument();
      expect(screen.getByText("1 失败")).toBeInTheDocument();
      expect(container.querySelector<HTMLDetailsElement>("details.tool-image-group")?.open).toBe(false);
      expect(container.querySelectorAll("details.tool-item")).toHaveLength(0);
      expect(container.querySelectorAll(".tool-image-tile")).toHaveLength(0);
      expect(prepareToolContentPreview).not.toHaveBeenCalled();
      expect(prepareWorkspacePreview).not.toHaveBeenCalled();

      await expandDetails(container, "details.tool-image-group");
      expect(container.querySelector<HTMLDetailsElement>("details.tool-image-group")?.open).toBe(true);
      expect(container.querySelectorAll(".tool-image-tile")).toHaveLength(4);
      expect(screen.getByRole("img", { name: "missing.png 查看失败" })).toHaveAttribute("title", "missing.png · Image file was not found");

      const second = await screen.findByRole("button", { name: "预览 two.png" });
      expect(await screen.findByRole("button", { name: "预览 legacy.png" })).toBeInTheDocument();
      expect(prepareToolContentPreview).toHaveBeenCalledWith(baseRun.sessionId, "tool-one", 0);
      expect(prepareToolContentPreview).toHaveBeenCalledWith(baseRun.sessionId, "tool-two", 0);
      expect(prepareToolContentPreview).toHaveBeenCalledTimes(2);
      expect(prepareWorkspacePreview.mock.calls.map((call) => call[1])).toEqual(["shots/legacy.png"]);

      fireEvent.click(second);
      expect(screen.getByRole("dialog", { name: "two.png 图片预览" })).toBeInTheDocument();
      expect(screen.getByText("2 / 3")).toBeInTheDocument();
      expect(within(screen.getByRole("dialog")).getByRole("img", { name: "two.png" })).toHaveAttribute("src", "eidos-preview://token/tool-two.png");

      // The failed call is skipped: the next image is the legacy one.
      fireEvent.click(screen.getByRole("button", { name: "下一张" }));
      expect(screen.getByRole("dialog", { name: "legacy.png 图片预览" })).toBeInTheDocument();
      expect(screen.getByText("3 / 3")).toBeInTheDocument();
      expect(within(screen.getByRole("dialog")).getByRole("img", { name: "legacy.png" })).toHaveAttribute("src", "eidos-preview://file/shots/legacy.png");
      expect(screen.getByRole("button", { name: "下一张" })).toHaveAttribute("aria-disabled", "true");

      fireEvent.keyDown(window, { key: "ArrowLeft" });
      fireEvent.keyDown(window, { key: "ArrowLeft" });
      expect(screen.getByRole("dialog", { name: "one.png 图片预览" })).toBeInTheDocument();
      expect(screen.getByText("1 / 3")).toBeInTheDocument();

      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      // Stepping reused the thumbnails' previews instead of requesting them again.
      expect(prepareToolContentPreview).toHaveBeenCalledTimes(2);
      expect(prepareWorkspacePreview).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("ExecutionFeed memory tools static rendering", () => {
  it("renders memory tools as static rows inside a tool group", async () => {
    const memoryItems: Item[] = [
      {
        id: "mem-search-1",
        sessionId: "session-mem",
        runId: "run-mem",
        ordinal: 1,
        kind: "tool_call",
        status: "in_progress",
        createdAt: 1000,
        toolCall: {
          id: "tc-mem-search",
          itemId: "mem-search-1",
          modelStepIndex: 1,
          batchOrder: 0,
          providerCallId: "p-mem-s",
          toolName: "memory_search",
          status: "running",
          startedAt: 1000,
          argumentsJson: JSON.stringify({ query: "2026-10-13|最近|latest|rollout" }),
        },
      },
      {
        id: "mem-read-1",
        sessionId: "session-mem",
        runId: "run-mem",
        ordinal: 2,
        kind: "tool_call",
        status: "completed",
        createdAt: 1000,
        toolCall: {
          id: "tc-mem-read",
          itemId: "mem-read-1",
          modelStepIndex: 1,
          batchOrder: 1,
          providerCallId: "p-mem-r",
          toolName: "memory_read",
          status: "completed",
          startedAt: 1000,
          completedAt: 1100,
          argumentsJson: JSON.stringify({ entryId: "entry-1" }),
          resultJson: JSON.stringify({ outcome: "success", code: "ok" }),
        },
      },
    ];

    render(
      <ExecutionFeed
        items={memoryItems}
        runs={[{ ...baseRun, id: "run-mem", status: "running" }]}
        approvals={[]}
      />,
    );

    // Adjacent tool calls fold into one group that shows the live action.
    await expandDetails(document.body, "details.tool-group");
    const searchRow = screen.getAllByText('正在搜索记忆 "2026-10-13|最近|latest|rollout"')
      .find((element) => element.closest(".tool-item--read-done"))!;
    const readRow = screen.getByText("已读取记忆");

    expect(searchRow).toBeInTheDocument();
    expect(readRow).toBeInTheDocument();

    // Verify neither item is inside a <details> element
    expect(searchRow.closest("details.tool-item")).toBeNull();
    expect(readRow.closest("details.tool-item")).toBeNull();

    // Verify they are rendered inside .tool-item--read-done
    expect(searchRow.closest(".tool-item--read-done")).not.toBeNull();
    expect(readRow.closest(".tool-item--read-done")).not.toBeNull();
  });
});

describe("ExecutionFeed skill and read_tool_output rendering", () => {
  it("renders read_tool_output as static row with 正在查看工具输出 and no details toggle", () => {
    const item: Item = {
      id: "tool-rto",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-rto",
        itemId: "tool-rto",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-rto",
        toolName: "read_tool_output",
        status: "completed",
        startedAt: 1000,
        completedAt: 1100,
        argumentsJson: JSON.stringify({ callId: "c1" }),
        resultJson: JSON.stringify({ outcome: "success", code: "ok" }),
      },
    };

    render(
      <ExecutionFeed
        items={[item]}
        runs={[baseRun]}
        approvals={[]}
      />,
    );

    const row = screen.getByText("正在查看工具输出");
    expect(row).toBeInTheDocument();
    expect(row.closest("details.tool-item")).toBeNull();
    expect(row.closest(".tool-item--read-done")).not.toBeNull();
  });

  it.each([
    ["running", "in_progress", '正在搜索会话历史 "deploy token"'],
    ["completed", "completed", '已搜索会话历史 "deploy token"'],
  ] as const)("labels a %s search_history call with its query", (toolStatus, itemStatus, label) => {
    const item: Item = {
      id: `tool-sh-${toolStatus}`,
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: itemStatus,
      createdAt: 1000,
      toolCall: {
        id: `tc-sh-${toolStatus}`,
        itemId: `tool-sh-${toolStatus}`,
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: `p-sh-${toolStatus}`,
        toolName: "search_history",
        status: toolStatus === "running" ? "running" : "completed",
        startedAt: 1000,
        ...(toolStatus === "completed" ? { completedAt: 1100 } : {}),
        argumentsJson: JSON.stringify({ query: "deploy token" }),
        ...(toolStatus === "completed"
          ? { resultJson: JSON.stringify({ outcome: "success", code: "ok", data: { query: "deploy token", matches: [], complete: true } }) }
          : {}),
      },
    };

    const { container } = render(
      <ExecutionFeed
        items={[item]}
        runs={[toolStatus === "running" ? { ...baseRun, status: "running" } : baseRun]}
        approvals={[]}
      />,
    );

    expect(screen.getByText(label)).toBeInTheDocument();
    const iconSvg = container.querySelector(".tool-icon svg");
    expect(iconSvg).not.toBeNull();
    expect(iconSvg?.innerHTML).toContain("M5.5 6.5h5M5.5 9h3");
    expect(iconSvg?.innerHTML).not.toContain("M8 2L14 8L8 14L2 8Z");
  });

  it("renders skill_read and opens skill detail dialog when skill name button is clicked", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const readSkillDetail = vi.fn().mockResolvedValue({
      qualifiedId: "system:code-review",
      name: "code-review",
      description: "Review your code",
      content: "# Code Review Skill",
      directory: "/skills/code-review",
      resources: [],
    });
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readSkillDetail,
        listSkills: vi.fn().mockResolvedValue({ skills: [] }),
      },
    });

    try {
      const item: Item = {
        id: "tool-sr",
        sessionId: baseRun.sessionId,
        runId: baseRun.id,
        ordinal: 1,
        kind: "tool_call",
        status: "completed",
        createdAt: 1000,
        toolCall: {
          id: "tc-sr",
          itemId: "tool-sr",
          modelStepIndex: 1,
          batchOrder: 0,
          providerCallId: "p-sr",
          toolName: "skill_read",
          status: "completed",
          startedAt: 1000,
          completedAt: 1100,
          argumentsJson: JSON.stringify({ qualifiedId: "system:code-review" }),
          resultJson: JSON.stringify({ outcome: "success", code: "ok" }),
        },
      };

      render(
        <ExecutionFeed
          items={[item]}
          runs={[baseRun]}
          approvals={[]}
        />,
      );

      // Verify static row
      const button = screen.getByRole("button", { name: "code-review" });
      expect(button).toBeInTheDocument();
      expect(button.closest("details.tool-item")).toBeNull();
      expect(button.closest(".tool-item--read-done")).not.toBeNull();
      expect(screen.getByText(/已读取.*技能 SKILL\.md/)).toBeInTheDocument();

      // Click skill name button to open detail dialog
      fireEvent.click(button);

      const heading = await screen.findByRole("heading", { name: "code-review" });
      expect(heading).toBeInTheDocument();
      expect(await screen.findByText("Review your code")).toBeInTheDocument();
      expect(screen.queryByText("未提供技能说明")).not.toBeInTheDocument();

      // Close dialog
      const closeBtn = screen.getByRole("button", { name: "关闭技能详情" });
      fireEvent.click(closeBtn);
      expect(screen.queryByRole("heading", { name: "code-review" })).not.toBeInTheDocument();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("renders skill_read_resource and displays skill description from listSkills when previewed", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const readSkillDetail = vi.fn().mockResolvedValue({
      qualifiedId: "user:translator",
      content: "---\nname: translator\ndescription: Translate text between languages.\n---\n# Translator\n\nPrompt details.\n",
      body: "# Translator\n\nPrompt details.\n",
      directory: "/skills/translator",
    });
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readSkillDetail,
        listSkills: vi.fn().mockResolvedValue({
          skills: [
            {
              qualifiedId: "user:translator",
              name: "translator",
              description: "Translate text between languages.",
              sourceKind: "user",
              enabled: true,
              available: true,
              schemaVersion: 1,
              pluginId: "",
              pluginVersion: "",
              pluginHash: "",
              contentHash: "hash123",
            },
          ],
        }),
      },
    });

    try {
      const item: Item = {
        id: "tool-srr",
        sessionId: baseRun.sessionId,
        runId: baseRun.id,
        ordinal: 2,
        kind: "tool_call",
        status: "completed",
        createdAt: 1000,
        toolCall: {
          id: "tc-srr",
          itemId: "tool-srr",
          modelStepIndex: 1,
          batchOrder: 0,
          providerCallId: "p-srr",
          toolName: "skill_read_resource",
          status: "completed",
          startedAt: 1000,
          completedAt: 1100,
          argumentsJson: JSON.stringify({ qualifiedId: "user:translator", resourcePath: "prompt.md" }),
          resultJson: JSON.stringify({ outcome: "success", code: "ok" }),
        },
      };

      const { container } = render(
        <ExecutionFeed
          items={[item]}
          runs={[baseRun]}
          approvals={[]}
        />,
      );

      const button = screen.getByRole("button", { name: "translator" });
      expect(button).toBeInTheDocument();
      expect(screen.getByText(/已读取.*技能包/)).toBeInTheDocument();
      expect(screen.queryByText(/整个技能包/)).not.toBeInTheDocument();

      button.focus();
      fireEvent.click(button);

      const dialog = await screen.findByRole("dialog", { name: "translator" });
      expect(dialog.closest(".modal-backdrop")?.parentElement).toBe(document.body);
      expect(container.querySelector(".feed-shell")?.contains(dialog)).toBe(false);
      expect(await screen.findByRole("heading", { name: "translator" })).toBeInTheDocument();
      expect(await screen.findByText("Translate text between languages.")).toBeInTheDocument();
      expect(screen.queryByText("未提供技能说明")).not.toBeInTheDocument();
      expect(await screen.findByText("Prompt details.")).toBeInTheDocument();

      const closeBtn = screen.getByRole("button", { name: "关闭技能详情" });
      fireEvent.click(closeBtn);
      expect(screen.queryByRole("heading", { name: "translator" })).not.toBeInTheDocument();
      expect(button).toHaveFocus();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("portals SkillDetailDialog outside scrollable tool-group when previewing skill_read_resource", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const readSkillDetail = vi.fn().mockResolvedValue({
      qualifiedId: "user:ppt-master",
      content: "---\nname: ppt-master\ndescription: Build slide decks.\n---\n# PPT Master\n\nRouting workflow.\n",
      body: "# PPT Master\n\nRouting workflow.\n",
      directory: "/skills/ppt-master",
    });
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readSkillDetail,
        listSkills: vi.fn().mockResolvedValue({
          skills: [
            {
              qualifiedId: "user:ppt-master",
              name: "ppt-master",
              description: "Build slide decks.",
              sourceKind: "user",
              enabled: true,
              available: true,
              schemaVersion: 1,
              pluginId: "",
              pluginVersion: "",
              pluginHash: "",
              contentHash: "hash-ppt",
            },
          ],
        }),
      },
    });

    try {
      const items: Item[] = [
        {
          id: "tool-srr-group-0",
          sessionId: baseRun.sessionId,
          runId: baseRun.id,
          ordinal: 1,
          kind: "tool_call",
          status: "completed",
          createdAt: 1000,
          toolCall: {
            id: "tc-srr-group-0",
            itemId: "tool-srr-group-0",
            modelStepIndex: 1,
            batchOrder: 0,
            providerCallId: "p-srr-0",
            toolName: "skill_read_resource",
            status: "completed",
            startedAt: 1000,
            completedAt: 1100,
            argumentsJson: JSON.stringify({ qualifiedId: "user:ppt-master", resourcePath: "workflows/routing.md" }),
            resultJson: JSON.stringify({ outcome: "success", code: "ok" }),
          },
        },
        ...Array.from({ length: 8 }, (_, index) =>
          shellItem({
            id: `tool-srr-shell-${index}`,
            ordinal: index + 2,
            status: "completed",
            toolCall: {
              ...shellItem().toolCall!,
              id: `tc-srr-shell-${index}`,
              itemId: `tool-srr-shell-${index}`,
              status: "completed",
              argumentsJson: JSON.stringify({ command: `echo step-${index}` }),
            },
          }),
        ),
      ];

      const { container } = render(
        <ExecutionFeed
          items={items}
          runs={[baseRun]}
          approvals={[]}
        />,
      );

      await expandDetails(container, "details.tool-group");
      const groupList = container.querySelector(".tool-group-list");
      expect(groupList).toHaveAttribute("data-scrollable", "true");

      const trigger = screen.getByRole("button", { name: "ppt-master" });
      trigger.focus();
      fireEvent.click(trigger);

      const dialog = await screen.findByRole("dialog", { name: "ppt-master" });
      const backdrop = dialog.closest(".modal-backdrop");
      expect(backdrop).toHaveClass("skill-detail-backdrop");
      expect(backdrop?.parentElement).toBe(document.body);
      expect(container.querySelector("details.tool-group")?.contains(dialog)).toBe(false);
      expect(container.querySelector(".feed-shell")?.contains(dialog)).toBe(false);
      expect(await screen.findByText("Routing workflow.")).toBeInTheDocument();

      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "ppt-master" })).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("extracts frontmatter description from SKILL.md content when listSkills has no match", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const readSkillDetail = vi.fn().mockResolvedValue({
      qualifiedId: "user:helper",
      content: "---\nname: helper\ndescription: Frontmatter extracted description.\n---\n# Helper\n\nHelper body.\n",
      body: "# Helper\n\nHelper body.\n",
      directory: "/skills/helper",
    });
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readSkillDetail,
        listSkills: vi.fn().mockResolvedValue({ skills: [] }),
      },
    });

    try {
      const item: Item = {
        id: "tool-sr-fm",
        sessionId: baseRun.sessionId,
        runId: baseRun.id,
        ordinal: 3,
        kind: "tool_call",
        status: "completed",
        createdAt: 1000,
        toolCall: {
          id: "tc-sr-fm",
          itemId: "tool-sr-fm",
          modelStepIndex: 1,
          batchOrder: 0,
          providerCallId: "p-sr-fm",
          toolName: "skill_read",
          status: "completed",
          startedAt: 1000,
          completedAt: 1100,
          argumentsJson: JSON.stringify({ qualifiedId: "user:helper" }),
          resultJson: JSON.stringify({ outcome: "success", code: "ok" }),
        },
      };

      render(
        <ExecutionFeed
          items={[item]}
          runs={[baseRun]}
          approvals={[]}
        />,
      );

      const button = screen.getByRole("button", { name: "helper" });
      fireEvent.click(button);

      expect(await screen.findByRole("heading", { name: "helper" })).toBeInTheDocument();
      expect(await screen.findByText("Frontmatter extracted description.")).toBeInTheDocument();
      expect(screen.queryByText("未提供技能说明")).not.toBeInTheDocument();
      expect(await screen.findByText("Helper body.")).toBeInTheDocument();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });
});




describe("saved output projection", () => {
  it("does not offer a system capacity pause button for old notices", () => {
    renderFeed(shellItem({ kind: "assistant_message", status: "completed", toolCall: undefined,
      content: "当前请求超过处理预算，任务已暂停。系统保留了执行记录，你可以调整任务后继续或取消" }));
    expect(screen.queryByRole("button", { name: "继续任务" })).not.toBeInTheDocument();
  });

  it("pages the full assistant answer through its Session and original hash", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const sha256 = "a".repeat(64);
    const readToolText = vi.fn()
      .mockResolvedValueOnce({ content: "first page", nextOffset: 10, totalCharacters: 19, sha256 })
      .mockResolvedValueOnce({ content: "last page", nextOffset: 19, totalCharacters: 19, sha256 });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readToolText } });
    try {
      renderFeed(shellItem({ kind: "assistant_message", status: "completed", toolCall: undefined,
        content: "preview", contentBytes: 70000, contentHash: sha256 }), { ...baseRun, status: "succeeded" });
      fireEvent.click(screen.getByRole("button", { name: "查看完整回答" }));
      expect(await screen.findByText("first page")).toBeInTheDocument();
      expect(readToolText).toHaveBeenLastCalledWith(baseRun.sessionId, "shell-item", "content", sha256, 0);
      fireEvent.click(screen.getByRole("button", { name: "下一页" }));
      expect(await screen.findByText("last page")).toBeInTheDocument();
      expect(readToolText).toHaveBeenLastCalledWith(baseRun.sessionId, "shell-item", "content", sha256, 10);
      expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("renders user message inline reference tokens at their exact mid-sentence positions and copies clean plain text", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });
    const lunaRef = {
      id: "111122223333" + "0".repeat(52),
      kind: "agent" as const,
      label: "luna-work",
      source: "luna-work",
      sha256: "a".repeat(64),
      status: "selection" as const,
      size: 16,
    };
    const docxRef = {
      id: "aabbccddeeff" + "9".repeat(52),
      kind: "skill" as const,
      label: "docx",
      source: "skills/docx",
      sha256: "b".repeat(64),
      status: "content" as const,
      size: 24,
    };
    const userItem = shellItem({
      id: "user-msg-inline",
      kind: "user_message",
      status: "completed",
      toolCall: undefined,
      content:
        "[@luna-work](ref:111122223333) 用这个帮我调研对比。[$docx](ref:aabbccddeeff) 并且输出文档。",
      references: [lunaRef, docxRef],
    });

    const { container } = renderFeed(userItem, { ...baseRun, status: "succeeded" });
    const prose = container.querySelector(".user-message-bubble p.eidos-inline-reference-prose");
    expect(prose).not.toBeNull();
    expect(prose?.textContent).toBe("luna-work 用这个帮我调研对比。docx 并且输出文档。");

    const buttons = prose?.querySelectorAll("button.input-reference__btn");
    expect(buttons).toHaveLength(2);
    expect(buttons?.[0]?.textContent).toBe("luna-work");
    expect(buttons?.[1]?.textContent).toBe("docx");

    const copyBtn = screen.getByRole("button", { name: "复制内容" });
    fireEvent.click(copyBtn);
    await waitFor(() => {
      expect(writeTextMock).toHaveBeenCalledWith(
        "@luna-work 用这个帮我调研对比。$docx 并且输出文档。",
      );
      expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();
    });
  });
});

describe("ExecutionFeed user message collapsing", () => {
  it("does not show expand button when user input is short", () => {
    const userItem: Item = {
      id: "user-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 0,
      kind: "user_message",
      status: "completed",
      createdAt: 1_000,
      content: "短消息",
    };
    const { container } = renderFeed(userItem);
    expect(screen.queryByRole("button", { name: "显示更多" })).not.toBeInTheDocument();
    expect(container.querySelector(".user-message-fade-mask")).toBeNull();
    expect(container.querySelector(".user-message-collapsible")?.classList.contains("is-collapsed")).toBe(false);
  });

  it("collapses long user message and shows '显示更多' button, toggles on click", () => {
    const scrollHeightSpy = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(600);
    try {
      const userItem: Item = {
        id: "user-long",
        sessionId: baseRun.sessionId,
        runId: baseRun.id,
        ordinal: 0,
        kind: "user_message",
        status: "completed",
        createdAt: 1_000,
        content: "非常非常长的提问内容...".repeat(20),
      };
      const { container } = renderFeed(userItem);

      // 默认处于折叠状态
      const expandBtn = screen.getByRole("button", { name: "显示更多" });
      expect(expandBtn).toBeInTheDocument();
      expect(expandBtn).toHaveAttribute("aria-expanded", "false");
      expect(container.querySelector(".user-message-collapsible")?.classList.contains("is-collapsed")).toBe(true);
      expect(container.querySelector(".user-message-fade-mask")).not.toBeNull();
      expect(container.querySelector(".user-message-expand-icon")?.classList.contains("is-expanded")).toBe(false);

      // 点击展开
      fireEvent.click(expandBtn);
      expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
      expect(expandBtn).toHaveAttribute("aria-expanded", "true");
      expect(container.querySelector(".user-message-collapsible")?.classList.contains("is-collapsed")).toBe(false);
      expect(container.querySelector(".user-message-fade-mask")).toBeNull();
      expect(container.querySelector(".user-message-expand-icon")?.classList.contains("is-expanded")).toBe(true);

      // 点击收起
      fireEvent.click(expandBtn);
      expect(screen.getByRole("button", { name: "显示更多" })).toBeInTheDocument();
      expect(container.querySelector(".user-message-collapsible")?.classList.contains("is-collapsed")).toBe(true);
      expect(container.querySelector(".user-message-fade-mask")).not.toBeNull();
    } finally {
      scrollHeightSpy.mockRestore();
    }
  });

  it("automatically expands collapsed user message when it is the searchTarget", () => {
    const scrollHeightSpy = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(600);
    try {
      const userItem: Item = {
        id: "user-target",
        sessionId: baseRun.sessionId,
        runId: baseRun.id,
        ordinal: 0,
        kind: "user_message",
        status: "completed",
        createdAt: 1_000,
        content: "文档比对长内容",
      };
      const { container } = render(
        <ExecutionFeed
          items={[userItem]}
          runs={[baseRun]}
          approvals={[]}
          searchTarget={{ itemId: "user-target", query: "比对", occurrence: 0, revision: 1 }}
        />
      );

      // 由于是 searchTarget，自动处于展开状态
      expect(container.querySelector(".user-message-collapsible")?.classList.contains("is-collapsed")).toBe(false);
      expect(container.querySelector(".user-message-fade-mask")).toBeNull();
      expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
    } finally {
      scrollHeightSpy.mockRestore();
    }
  });

  it("hides expand button and does not collapse when user enters edit mode", () => {
    const scrollHeightSpy = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(600);
    try {
      const userItem: Item = {
        id: "user-edit",
        sessionId: baseRun.sessionId,
        runId: baseRun.id,
        ordinal: 0,
        kind: "user_message",
        status: "completed",
        createdAt: 1_000,
        content: "想要编辑的长消息",
      };
      const terminalRun: Run = { ...baseRun, status: "succeeded" };
      const { container } = render(
        <ExecutionFeed
          items={[userItem]}
          runs={[terminalRun]}
          approvals={[]}
          allowEditResend={true}
        />
      );

      expect(screen.getByRole("button", { name: "显示更多" })).toBeInTheDocument();
      const editBtn = screen.getByRole("button", { name: "编辑并重新发送" });
      fireEvent.click(editBtn);

      // 进入编辑状态，编辑框出现，折叠按钮和遮罩消失
      expect(screen.getByRole("textbox", { name: "编辑最近一次提问" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "显示更多" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "收起" })).not.toBeInTheDocument();
      expect(container.querySelector(".user-message-fade-mask")).toBeNull();
    } finally {
      scrollHeightSpy.mockRestore();
    }
  });
});

describe("Browser tool rows", () => {
  function browserItem(toolName: string, args: object, status: Item["status"] = "completed", text = "- Page URL: https://shop.example.com/cart\n") {
    return shellItem({ kind: "tool_call", status, toolCall: {
      ...shellItem().toolCall!, toolName: `mcp__browser__${toolName}`, status: status === "in_progress" ? "running" : "completed",
      argumentsJson: JSON.stringify(args),
      provenance: { kind: "mcp", sourceId: "eidos-browser:browser", sourceVersion: "1.0.0", contentHash: "hash" },
      resultJson: status === "in_progress" ? undefined : JSON.stringify({ outcome: "success", code: "ok", summary: "MCP tool completed", data: { text, isError: false } }),
    } });
  }

  it("names the action, element and page host instead of the raw MCP tool", () => {
    renderFeed(browserItem("browser_click", { target: "e12", element: "登录按钮" }));
    expect(screen.getByText("点击 · 登录按钮 · shop.example.com")).toBeInTheDocument();
    expect(screen.queryByText("MCP tool completed")).not.toBeInTheDocument();
    expect(screen.queryByText(/mcp__browser__/)).not.toBeInTheDocument();
  });

  it("shows navigation targets, uploads and running state", () => {
    const { rerender } = renderFeed(browserItem("browser_navigate", { url: "https://docs.example.org/start" }));
    expect(screen.getByText("打开网页 · docs.example.org")).toBeInTheDocument();
    rerender(<ExecutionFeed items={[browserItem("browser_file_upload", { paths: ["/workspace/report.pdf"] })]} runs={[baseRun]} approvals={[]} />);
    expect(screen.getByText("上传文件 · report.pdf · shop.example.com")).toBeInTheDocument();
    rerender(<ExecutionFeed items={[browserItem("browser_snapshot", {}, "in_progress")]} runs={[baseRun]} approvals={[]} />);
    // The standalone row names its own action; the live line must not repeat it.
    expect(screen.getByText("正在读取页面")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在执行");
  });
});

describe("ExecutionFeed collapsed bodies", () => {
  const finishedRun: Run = { ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 };
  const message = (id: string, ordinal: number, kind: Item["kind"], content: string): Item => ({
    id, ordinal, kind, content, sessionId: baseRun.sessionId, runId: baseRun.id,
    status: "completed", createdAt: 1_000,
  });
  const finishedItems = () => [
    message("user", 0, "user_message", "检查一下"),
    message("progress", 1, "assistant_message", "我正在检查目录。"),
    shellItem({ ordinal: 2, status: "completed", completedAt: 1_500 }),
    message("answer", 3, "assistant_message", "检查完成。"),
  ];

  it("mounts a finished Run's process and tool bodies only after they are opened", async () => {
    const { container } = render(<ExecutionFeed items={finishedItems()} runs={[finishedRun]} approvals={[]} />);

    const group = container.querySelector<HTMLDetailsElement>("details.process-group")!;
    expect(group.open).toBe(false);
    expect(group.querySelector(".process-content")).toBeNull();
    expect(container.querySelector(".shell-result")).toBeNull();
    expect(screen.getByText("检查完成。")).toBeInTheDocument();

    await expandDetails(container, "details.process-group");
    expect(screen.getByText("我正在检查目录。")).toBeInTheDocument();
    expect(container.querySelector(".shell-result")).toBeNull();

    await expandDetails(container);
    expect(container.querySelector(".shell-output")?.textContent).toBe("first line\n");

    // Closing keeps the body mounted so its state survives reopening.
    fireEvent.click(group.querySelector(":scope > summary")!);
    await waitFor(() => expect(group.open).toBe(false));
    expect(group.querySelector(".process-content")).not.toBeNull();
  });

  it("opens a collapsed process group that holds the search target in the same render", () => {
    const { container } = render(
      <ExecutionFeed
        items={finishedItems()}
        runs={[finishedRun]}
        approvals={[]}
        searchTarget={{ itemId: "progress", query: "目录", occurrence: 0, revision: 1 }}
      />,
    );

    const group = container.querySelector<HTMLDetailsElement>("details.process-group")!;
    expect(group.open).toBe(true);
    expect(container.querySelector("#item-progress")).toHaveTextContent("我正在检查目录。");
  });

  it("keeps a tool group collapsed until opened, but opens it for a pending approval", async () => {
    const items = [
      shellItem({ id: "g1", ordinal: 1, status: "completed", toolCall: { ...shellItem().toolCall!, id: "g1-tool", itemId: "g1", status: "completed", argumentsJson: JSON.stringify({ command: "git status" }) } }),
      shellItem({ id: "g2", ordinal: 2, status: "completed", toolCall: { ...shellItem().toolCall!, id: "g2-tool", itemId: "g2", status: "completed", argumentsJson: JSON.stringify({ command: "git diff" }) } }),
    ];
    const { container, unmount } = render(<ExecutionFeed items={items} runs={[baseRun]} approvals={[]} />);
    const group = container.querySelector<HTMLDetailsElement>("details.tool-group")!;
    expect(group.open).toBe(false);
    expect(container.querySelector(".tool-group-list")).toBeNull();
    expect(group.querySelector(":scope > summary")).toHaveTextContent("运行 2 条命令");
    await expandDetails(container, "details.tool-group");
    expect(container.querySelector(".tool-group-list")).toHaveTextContent("git diff");
    unmount();

    const pending = render(
      <ExecutionFeed
        items={items}
        runs={[baseRun]}
        approvals={[{
          id: "approval-1", sessionId: baseRun.sessionId, runId: baseRun.id, itemId: "g2", toolCallId: "g2-tool",
          summary: "git diff", kind: "command_execution",
        } as never]}
      />,
    );
    expect(pending.container.querySelector<HTMLDetailsElement>("details.tool-group")!.open).toBe(true);
    expect(pending.container).toHaveTextContent("待批准");
    expect(pending.container.querySelector(".feed-label")).toBeNull();
  });
});

describe("Targeted tool feed improvements", () => {
  const completedRun: Run = { ...baseRun, status: "succeeded", completedAt: 2_000 };

  it("renders list_files entries with clickable workspace files and non-clickable directories", async () => {
    const onOpenFile = vi.fn();
    const item: Item = {
      id: "list-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-list-1",
        itemId: "list-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-list-1",
        toolName: "list_files",
        status: "completed",
        startedAt: 1000,
        completedAt: 1100,
        argumentsJson: JSON.stringify({ path: "src" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Listed files",
          data: {
            paths: ["src/components/", "src/index.ts", "/external/skill/SKILL.md"],
            truncated: true,
          },
        }),
      },
    };

    const { container } = render(
      <ArtifactProvider value={{ sessionId: baseRun.sessionId, executionRoot: "/workspace", openFile: onOpenFile, openBrowser: vi.fn() }}>
        <ExecutionFeed items={[item]} runs={[completedRun]} approvals={[]} onOpenFile={onOpenFile} />
      </ArtifactProvider>,
    );

    await expandDetails(container, "details.process-group");
    expect(screen.getByText("已列出 src")).toBeInTheDocument();
    await expandDetails(container, "details.tool-item");

    const fileBtn = screen.getByRole("button", { name: "src/index.ts" });
    fireEvent.click(fileBtn);
    expect(onOpenFile).toHaveBeenCalledWith("src/index.ts");

    expect(screen.queryByRole("button", { name: "src/components/" })).toBeNull();
    expect(screen.getByText("src/components/")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "/external/skill/SKILL.md" })).toBeNull();
    expect(screen.getByText("/external/skill/SKILL.md")).toBeInTheDocument();
    expect(screen.getByText(/更多条目已截断/)).toBeInTheDocument();
  });

  it("renders search_text and search_text_wait with search icon, Chinese summary, and clickable line matches", async () => {
    const onOpenFile = vi.fn();
    const searchItem: Item = {
      id: "st-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-st-1",
        itemId: "st-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-st-1",
        toolName: "search_text",
        status: "completed",
        startedAt: 1000,
        completedAt: 1050,
        argumentsJson: JSON.stringify({ query: "needle" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "search_running",
          summary: "Search is still running",
          data: { matches: [], truncationReason: "search_running", sessionId: "s-1" },
        }),
      },
    };
    const waitItem: Item = {
      id: "stw-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 2,
      kind: "tool_call",
      status: "completed",
      createdAt: 1100,
      toolCall: {
        id: "tc-stw-1",
        itemId: "stw-1",
        modelStepIndex: 2,
        batchOrder: 0,
        providerCallId: "p-stw-1",
        toolName: "search_text_wait",
        status: "completed",
        startedAt: 1100,
        completedAt: 1200,
        argumentsJson: JSON.stringify({ sessionId: "s-1" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Searched text",
          data: {
            matches: [{ path: "src/app.ts", line: 42, column: 7, preview: "const needle = true;" }],
            truncated: false,
          },
        }),
      },
    };

    const { container } = render(
      <ExecutionFeed items={[searchItem, waitItem]} runs={[completedRun]} approvals={[]} onOpenFile={onOpenFile} />,
    );

    await expandDetails(container, "details.process-group");
    const groupSummary = container.querySelector("details.tool-group > summary");
    expect(groupSummary).toHaveTextContent("搜索 2 次");
    await expandDetails(container, "details.tool-group");

    const toolDetails = Array.from(container.querySelectorAll<HTMLDetailsElement>("details.tool-item"));
    expect(toolDetails).toHaveLength(2);
    expect(toolDetails[0]!.querySelector("summary")).toHaveTextContent(/^已搜索 needle$/);
    expect(toolDetails[1]!.querySelector("summary")).toHaveTextContent("已搜索 needle · 1 处 · 1 个文件");
    expect(toolDetails[1]!.querySelector(".tool-icon svg")?.innerHTML).toContain("M10 10l3.75 3.75");
    expect(toolDetails[1]!.querySelector(".tool-icon svg")?.innerHTML).not.toContain("M8 2L14 8L8 14L2 8Z");

    toolDetails[0]!.open = true;
    fireEvent(toolDetails[0]!, new Event("toggle"));
    expect(screen.getByText(/搜索仍在后台继续/)).toBeInTheDocument();

    toolDetails[1]!.open = true;
    fireEvent(toolDetails[1]!, new Event("toggle"));
    const hit = screen.getByRole("button", { name: "第 42 行 const needle = true;" });
    expect(hit).toHaveTextContent("42const needle = true;");
    expect(hit.querySelector("mark")).toHaveTextContent(/^needle$/);
    fireEvent.click(hit);
    expect(onOpenFile).toHaveBeenCalledWith("src/app.ts", { line: 42, column: 7 });
    fireEvent.click(screen.getByRole("button", { name: "src/app.ts，1 处" }));
    expect(onOpenFile).toHaveBeenLastCalledWith("src/app.ts");
  });

  it("groups search_text hits by file under one shared root and caps long result lists", async () => {
    const root = "/Users/me/Documents/AF/appforge-service";
    const files = ["a.go", "b.go", "c.go", "d.go", "e.go", "f.go", "g.go"];
    const matches = files.flatMap((name, fileIndex) => Array.from(
      { length: fileIndex === 0 ? 7 : 5 },
      (_, hitIndex) => ({
        path: `${root}/internal/${fileIndex < 3 ? "handler" : "service"}/${name}`,
        line: hitIndex * 10 + 1,
        column: 5,
        preview: `    GetWorkflowReference${hitIndex}()`,
      }),
    ));
    const item: Item = {
      id: "st-group",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-st-group",
        itemId: "st-group",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-st-group",
        toolName: "search_text",
        status: "completed",
        startedAt: 1000,
        completedAt: 1050,
        argumentsJson: JSON.stringify({ query: "getworkflowreference", path: root }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Searched text",
          data: { matches, scannedBytes: 1, truncated: true, truncationReason: "max_results" },
        }),
      },
    };

    const { container } = render(
      <ExecutionFeed items={[item]} runs={[completedRun]} approvals={[]} onOpenFile={vi.fn()} />,
    );
    await expandDetails(container, "details.process-group");
    expect(container.querySelector("details.tool-item > summary"))
      .toHaveTextContent("已搜索 getworkflowreference · 37+ 处 · 7 个文件");
    await expandDetails(container, "details.tool-item");

    expect(container.querySelector(".tool-text-search-root")).toHaveTextContent(`${root}/internal`);
    const heads = () => Array.from(container.querySelectorAll(".tool-text-search-file-head"));
    expect(heads().map((head) => head.textContent)).toEqual([
      "a.gohandler7", "b.gohandler5", "c.gohandler5", "d.goservice5", "e.goservice5", "f.goservice5",
    ]);
    const firstFile = container.querySelector(".tool-text-search-file")!;
    expect(firstFile.querySelectorAll(".tool-text-search-hit")).toHaveLength(5);
    expect(firstFile.querySelector(".tool-text-search-hit mark")).toHaveTextContent(/^GetWorkflowReference$/);
    expect(firstFile.querySelector(".tool-text-search-preview")).toHaveTextContent(/^GetWorkflowReference0\(\)$/);

    fireEvent.click(screen.getByRole("button", { name: "还有 2 处" }));
    expect(firstFile.querySelectorAll(".tool-text-search-hit")).toHaveLength(7);
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(firstFile.querySelectorAll(".tool-text-search-hit")).toHaveLength(5);

    fireEvent.click(screen.getByRole("button", { name: "显示其余 1 个文件" }));
    expect(heads()).toHaveLength(7);
    expect(screen.getByText("命中结果已截断")).toBeInTheDocument();
  });

  it("renders search_history matched sessions and jumps with itemId/query or falls back to navigateToSession", async () => {
    const onOpenHistoryMatch = vi.fn();
    const navigateToSession = vi.fn();
    const historyItem: Item = {
      id: "sh-proj-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-sh-proj-1",
        itemId: "sh-proj-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-sh-proj-1",
        toolName: "search_history",
        status: "completed",
        startedAt: 1000,
        completedAt: 1100,
        argumentsJson: JSON.stringify({ query: "deploy token", scope: "project" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Found 2 saved history match(es)",
          data: {
            query: "deploy token",
            complete: true,
            matches: [
              {
                itemId: "msg-old-1",
                kind: "assistant",
                snippet: "…rotate the deploy token in vault…",
                readCallId: "msg-old-1",
                stream: "context",
                sessionId: "sess-old-1",
                sessionTitle: "发布流程梳理",
                date: "2026-11-10",
              },
              {
                itemId: "msg-old-2",
                kind: "user",
                snippet: "…where is the deploy token stored…",
                readCallId: "msg-old-2",
                stream: "context",
                sessionId: "sess-old-1",
                sessionTitle: "发布流程梳理",
                date: "2026-11-10",
              },
            ],
          },
        }),
      },
    };

    const view = render(
      <InputContextProvider
        sessionId={baseRun.sessionId}
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
        onNavigateToSession={navigateToSession}
      >
        <ExecutionFeed items={[historyItem]} runs={[completedRun]} approvals={[]} onOpenHistoryMatch={onOpenHistoryMatch} />
      </InputContextProvider>,
    );

    await expandDetails(view.container, "details.process-group");
    await expandDetails(view.container, "details.tool-item");

    expect(screen.getByText("2026-11-10 · 2 条匹配")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "发布流程梳理" }));
    expect(onOpenHistoryMatch).toHaveBeenCalledWith({
      sessionId: "sess-old-1",
      itemId: "msg-old-1",
      query: "deploy token",
    });

    fireEvent.click(screen.getByText("…where is the deploy token stored…").closest("button")!);
    expect(onOpenHistoryMatch).toHaveBeenCalledWith({
      sessionId: "sess-old-1",
      itemId: "msg-old-2",
      query: "deploy token",
    });

    view.rerender(
      <InputContextProvider
        sessionId={baseRun.sessionId}
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
        onNavigateToSession={navigateToSession}
      >
        <ExecutionFeed items={[historyItem]} runs={[completedRun]} approvals={[]} />
      </InputContextProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "发布流程梳理" }));
    expect(navigateToSession).toHaveBeenCalledWith("sess-old-1");
  });

  it("renders current-session search_history matches and jumps only on message matches", async () => {
    const onOpenHistoryMatch = vi.fn();
    const localHistoryItem: Item = {
      id: "sh-local-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-sh-local-1",
        itemId: "sh-local-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-sh-local-1",
        toolName: "search_history",
        status: "completed",
        startedAt: 1000,
        completedAt: 1100,
        argumentsJson: JSON.stringify({ query: "vault", scope: "session" }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Found 2 saved history match(es)",
          data: {
            query: "vault",
            complete: false,
            matches: [
              { itemId: "msg-local-1", kind: "user", snippet: "…read from vault…", readCallId: "msg-local-1", stream: "context" },
              { itemId: "tool-local-1", kind: "tool", toolName: "run_shell", snippet: "vault status", readCallId: "call-1", stream: "result" },
            ],
          },
        }),
      },
    };

    const { container } = render(
      <ExecutionFeed items={[localHistoryItem]} runs={[completedRun]} approvals={[]} onOpenHistoryMatch={onOpenHistoryMatch} />,
    );

    await expandDetails(container, "details.process-group");
    await expandDetails(container, "details.tool-item");

    fireEvent.click(screen.getByText("…read from vault…").closest("button")!);
    expect(onOpenHistoryMatch).toHaveBeenCalledWith({
      sessionId: baseRun.sessionId,
      itemId: "msg-local-1",
      query: "vault",
    });
    expect(screen.getByText("vault status").closest("button")).toBeNull();
    expect(screen.getByText("仅显示部分最近匹配结果")).toBeInTheDocument();
  });

  it("scrolls to searchTarget element when highlightText finds no match in prose", () => {
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      const targetItem: Item = {
        id: "msg-scroll-target",
        sessionId: baseRun.sessionId,
        runId: baseRun.id,
        ordinal: 1,
        kind: "assistant_message",
        status: "completed",
        createdAt: 1000,
        content: "普通回复内容",
      };
      render(
        <ExecutionFeed
          items={[targetItem]}
          runs={[completedRun]}
          approvals={[]}
          searchTarget={{ itemId: "msg-scroll-target", query: "unmatched-query", occurrence: 0, revision: 1 }}
        />,
      );
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "center", inline: "nearest" });
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("renders update_goal with GoalMarkIcon and declared status and summary", async () => {
    const goalItem: Item = {
      id: "goal-tool-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-goal-1",
        itemId: "goal-tool-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-goal-1",
        toolName: "update_goal",
        status: "completed",
        startedAt: 1000,
        completedAt: 1100,
        argumentsJson: JSON.stringify({
          status: "complete",
          summary: "所有测试与文档均已验证完成。",
        }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "goal_declaration_recorded",
          summary: "目标声明已记录。Run 通过最终检查后，Runtime 才会确认目标状态。",
          data: { pending: true },
        }),
      },
    };

    const { container } = render(
      <ExecutionFeed items={[goalItem]} runs={[completedRun]} approvals={[]} />,
    );

    await expandDetails(container, "details.process-group");
    expect(screen.getByText("已声明目标完成")).toBeInTheDocument();
    const iconSvg = container.querySelector("details.tool-item .tool-icon svg");
    expect(iconSvg).not.toBeNull();
    expect(iconSvg?.innerHTML).toContain('r="3.1"');
    expect(iconSvg?.innerHTML).not.toContain("M8 2L14 8L8 14L2 8Z");

    await expandDetails(container, "details.tool-item");
    expect(screen.getByText("声明状态：已完成")).toBeInTheDocument();
    expect(screen.getByText("所有测试与文档均已验证完成。")).toBeInTheDocument();
    expect(screen.getByText(/目标声明已记录/)).toBeInTheDocument();
  });

  it("renders read_file_range with line numbers and passes start line when opened", async () => {
    const onOpenFile = vi.fn();
    const rangeDone: Item = {
      id: "rfr-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "tool_call",
      status: "completed",
      createdAt: 1000,
      toolCall: {
        id: "tc-rfr-1",
        itemId: "rfr-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "p-rfr-1",
        toolName: "read_file_range",
        status: "completed",
        startedAt: 1000,
        completedAt: 1100,
        argumentsJson: JSON.stringify({ path: "src/index.ts", startLine: 10, endLine: 80 }),
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Read file range",
          data: { path: "src/index.ts", startLine: 10, endLine: 50, content: "code", sizeBytes: 100, sha256: "a".repeat(64) },
        }),
      },
    };
    const rangeRunning: Item = {
      id: "rfr-2",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 2,
      kind: "tool_call",
      status: "in_progress",
      createdAt: 1200,
      toolCall: {
        id: "tc-rfr-2",
        itemId: "rfr-2",
        modelStepIndex: 1,
        batchOrder: 1,
        providerCallId: "p-rfr-2",
        toolName: "read_file_range",
        status: "running",
        startedAt: 1200,
        argumentsJson: JSON.stringify({ path: "src/other.ts", startLine: 42, endLine: 42 }),
      },
    };

    const { container } = render(
      <ExecutionFeed items={[rangeDone, rangeRunning]} runs={[baseRun]} approvals={[]} onOpenFile={onOpenFile} />,
    );

    expect(screen.getByText("正在读取 src/other.ts · 第 42 行")).toBeInTheDocument();
    await expandDetails(container, "details.tool-group");

    expect(screen.getByText(/第 10–50 行/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "index.ts" }));
    expect(onOpenFile).toHaveBeenCalledWith("src/index.ts", { line: 10 });
  });

  it("virtualizes offscreen run groups when session exceeds 24 runs while keeping search targets mounted", () => {
    const runs: Run[] = Array.from({ length: 30 }, (_, index) => ({
      id: `run-v-${index}`,
      sessionId: "session-virtual",
      status: "succeeded",
      modelId: "deepseek-v4-flash",
      modelStepCount: 1,
      createdAt: index + 1,
      startedAt: index + 1,
      updatedAt: index + 1,
      completedAt: index + 1,
    }));
    const items: Item[] = runs.flatMap((run, index) => [
      {
        id: `user-v-${index}`,
        sessionId: "session-virtual",
        runId: run.id,
        ordinal: 1,
        kind: "user_message" as const,
        status: "completed" as const,
        createdAt: index + 1,
        content: `Question ${index}`,
      },
      {
        id: `answer-v-${index}`,
        sessionId: "session-virtual",
        runId: run.id,
        ordinal: 2,
        kind: "assistant_message" as const,
        status: "completed" as const,
        createdAt: index + 2,
        content: `Answer ${index}`,
      },
    ]);

    const originalClientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 400 });
    try {
      const { container } = render(
        <ExecutionFeed
          items={items}
          runs={runs}
          approvals={[]}
          searchTarget={{ itemId: "answer-v-10", query: "Answer", occurrence: 0, revision: 1 }}
        />,
      );
      expect(container.querySelectorAll('[data-virtualized="true"]').length).toBeGreaterThan(0);
      expect(document.getElementById("item-answer-v-10")).toBeInTheDocument();
      expect(container.querySelectorAll('[data-run-anchor]').length).toBe(30);
    } finally {
      if (originalClientHeight) Object.defineProperty(HTMLElement.prototype, "clientHeight", originalClientHeight);
      else Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
    }
  });

  it("copies mixed shelf and inline references from CopyButton and reference-only messages", async () => {
    clearQueryClipboardMirror();
    const clipboardDesc = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    const runtimeDesc = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: writeTextMock } });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readInput: vi.fn().mockResolvedValue({ text: "x" }) } });

    try {
      const fileRef: InputReference = {
        id: "aaaa1111bbbb" + "0".repeat(52),
        kind: "file",
        label: "report.md",
        source: "/ws/report.md",
        sha256: "a".repeat(64),
        status: "content",
        size: 16,
      };
      const agentRef: InputReference = {
        id: "cccc2222dddd" + "1".repeat(52),
        kind: "agent",
        label: "researcher",
        source: "researcher",
        sha256: "b".repeat(64),
        status: "selection",
        size: 16,
      };

      const { rerender } = renderFeed(
        shellItem({
          id: "user-mixed",
          kind: "user_message",
          status: "completed",
          toolCall: undefined,
          content: `[@researcher](ref:${agentRef.id.slice(0, 12)}) 查看附件`,
          references: [fileRef, agentRef],
        }),
        { ...baseRun, status: "succeeded" },
      );

      fireEvent.click(screen.getByRole("button", { name: "复制内容" }));
      await waitFor(() => {
        expect(writeTextMock).toHaveBeenCalledWith("[文件: report.md]\n@researcher 查看附件");
      });

      const store = new Map<string, string>([["text/plain", "[文件: report.md]\n@researcher 查看附件"]]);
      const restored = readQueryFromClipboardEvent({
        preventDefault: vi.fn(),
        clipboardData: {
          getData: (type: string) => store.get(type) ?? "",
          setData: (type: string, data: string) => { store.set(type, data); },
        },
      });
      expect(restored?.references.map((r) => r.id)).toEqual([fileRef.id, agentRef.id]);

      // Reference-only message with empty content still renders CopyButton
      writeTextMock.mockClear();
      rerender(
        <ExecutionFeed
          items={[
            shellItem({
              id: "user-refs-only",
              kind: "user_message",
              status: "completed",
              toolCall: undefined,
              content: "",
              references: [fileRef],
            }),
          ]}
          runs={[{ ...baseRun, status: "succeeded" }]}
          approvals={[]}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "复制内容" }));
      await waitFor(() => {
        expect(writeTextMock).toHaveBeenCalledWith("[文件: report.md]");
      });
    } finally {
      clearQueryClipboardMirror();
      if (clipboardDesc) Object.defineProperty(navigator, "clipboard", clipboardDesc);
      else delete (navigator as Partial<Navigator>).clipboard;
      if (runtimeDesc) Object.defineProperty(window, "eidosRuntime", runtimeDesc);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("handles bubble selection copy for inline/shelf selections while preserving partial plain-text selection", () => {
    clearQueryClipboardMirror();
    const runtimeDesc = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readInput: vi.fn().mockResolvedValue({ text: "x" }) } });

    try {
      const fileRef: InputReference = {
        id: "aaaa1111bbbb" + "0".repeat(52),
        kind: "file",
        label: "spec.md",
        source: "/ws/spec.md",
        sha256: "a".repeat(64),
        status: "content",
        size: 16,
      };
      const agentRef: InputReference = {
        id: "cccc2222dddd" + "1".repeat(52),
        kind: "agent",
        label: "researcher",
        source: "researcher",
        sha256: "b".repeat(64),
        status: "selection",
        size: 16,
      };
      const { container } = renderFeed(
        shellItem({
          id: "user-sel",
          kind: "user_message",
          status: "completed",
          toolCall: undefined,
          content: `请 [@researcher](ref:${agentRef.id.slice(0, 12)}) 分析`,
          references: [fileRef, agentRef],
        }),
        { ...baseRun, status: "succeeded" },
      );

      const bubble = container.querySelector(".user-message-bubble") as HTMLElement;
      const prose = bubble.querySelector(".eidos-inline-reference-prose") as HTMLElement;
      const card = bubble.querySelector("[data-shelf-ref-id]") as HTMLElement;
      const selection = window.getSelection()!;

      // 1. Select from shelf card through end of prose -> captures both shelf and inline ref
      const fullRange = document.createRange();
      fullRange.setStart(card, 0);
      fullRange.setEnd(prose, prose.childNodes.length);
      selection.removeAllRanges();
      selection.addRange(fullRange);

      const fullStore = new Map<string, string>();
      fireEvent.copy(bubble, {
        clipboardData: {
          getData: (t: string) => fullStore.get(t) ?? "",
          setData: (t: string, v: string) => { fullStore.set(t, v); },
        },
      });
      expect(fullStore.get("text/plain")).toBe("[文件: spec.md]\n请 @researcher 分析");
      expect(JSON.parse(fullStore.get(QUERY_CLIPBOARD_MIME)!).references.map((r: InputReference) => r.id))
        .toEqual([fileRef.id, agentRef.id]);

      // 2. Select partial substring inside the pill label -> does not hijack native copy
      const labelSpan = prose.querySelector(".input-reference__label")!;
      const partialRange = document.createRange();
      partialRange.setStart(labelSpan.firstChild!, 2);
      partialRange.setEnd(labelSpan.firstChild!, 6);
      selection.removeAllRanges();
      selection.addRange(partialRange);

      const partialStore = new Map<string, string>();
      fireEvent.copy(bubble, {
        clipboardData: {
          getData: (t: string) => partialStore.get(t) ?? "",
          setData: (t: string, v: string) => { partialStore.set(t, v); },
        },
      });
      expect(partialStore.has(QUERY_CLIPBOARD_MIME)).toBe(false);

      // 3. Select plain text only ("分析") -> does not hijack native copy
      const textOnlyRange = document.createRange();
      const trailingText = prose.lastChild!;
      textOnlyRange.setStart(trailingText, 0);
      textOnlyRange.setEnd(trailingText, (trailingText.textContent ?? "").length);
      selection.removeAllRanges();
      selection.addRange(textOnlyRange);

      const textOnlyStore = new Map<string, string>();
      fireEvent.copy(bubble, {
        clipboardData: {
          getData: (t: string) => textOnlyStore.get(t) ?? "",
          setData: (t: string, v: string) => { textOnlyStore.set(t, v); },
        },
      });
      expect(textOnlyStore.has(QUERY_CLIPBOARD_MIME)).toBe(false);
    } finally {
      window.getSelection()?.removeAllRanges();
      clearQueryClipboardMirror();
      if (runtimeDesc) Object.defineProperty(window, "eidosRuntime", runtimeDesc);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("restores valid references and degrades stale ones when pasting into UserMessage edit mode", async () => {
    clearQueryClipboardMirror();
    const runtimeDesc = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const validAgent: InputReference = {
      id: "111122223333" + "a".repeat(52),
      kind: "agent",
      label: "researcher",
      source: "researcher",
      sha256: "b".repeat(64),
      status: "selection",
      size: 16,
    };
    const deletedAgent: InputReference = {
      id: "444455556666" + "c".repeat(52),
      kind: "agent",
      label: "retired",
      source: "retired",
      sha256: "d".repeat(64),
      status: "selection",
      size: 16,
    };
    const validFile: InputReference = {
      id: "777788889999" + "e".repeat(52),
      kind: "file",
      label: "data.csv",
      source: "/ws/data.csv",
      sha256: "f".repeat(64),
      status: "content",
      size: 20,
    };
    const refById = new Map([validFile, validAgent, deletedAgent].map((r) => [r.id, r]));
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readInput: vi.fn(async (id: string) => {
          const matched = refById.get(id);
          if (!matched) throw new Error("RESOURCE_NOT_FOUND");
          return { reference: matched, text: "col1,col2" };
        }),
        listAgentTypes: vi.fn().mockResolvedValue({
          agents: [
            { name: "researcher", source: "researcher.toml", enabled: true, available: true, definitionVersion: validAgent.sha256 },
          ],
        }),
      },
    });

    try {
      const onEditResend = vi.fn().mockResolvedValue(true);
      const userItem = shellItem({
        id: "user-edit",
        kind: "user_message",
        status: "completed",
        toolCall: undefined,
        content: "原始问题",
      });
      const { container } = render(
        <ExecutionFeed
          items={[userItem]}
          runs={[{ ...baseRun, status: "succeeded" }]}
          approvals={[]}
          onEditResend={onEditResend}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "编辑并重新发送" }));
      const textarea = screen.getByRole("textbox", { name: "编辑最近一次提问" }) as HTMLTextAreaElement;
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);

      const payload = buildQueryClipboardPayload(
        ` [@researcher](ref:${validAgent.id.slice(0, 12)}) 和 [@retired](ref:${deletedAgent.id.slice(0, 12)})`,
        [validFile, validAgent, deletedAgent],
      );

      fireEvent.paste(textarea, {
        clipboardData: {
          getData: (type: string) => type === QUERY_CLIPBOARD_MIME ? serializeQueryClipboardPayload(payload) : "",
          setData: vi.fn(),
          files: [],
        },
      });

      await waitFor(() => {
        expect(container.querySelector(".user-message-editor .composer-rich-editor")).not.toBeNull();
      });
      expect(screen.getByText("部分引用已更新或不可用，已降级为纯文本。")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "移除 data.csv" })).toBeInTheDocument();
      expect(container.querySelector(`[data-shelf-ref-id="${validFile.id}"]`)).not.toBeNull();
      expect(container.querySelector(".user-message-editor .composer-rich-editor")?.textContent).toContain("@retired");

      fireEvent.click(screen.getByRole("button", { name: "发送" }));
      expect(onEditResend).toHaveBeenCalledWith(
        expect.objectContaining({ id: baseRun.id }),
        `原始问题 [@researcher](ref:${validAgent.id.slice(0, 12)}) 和 @retired`,
        [validFile.id, validAgent.id],
      );
    } finally {
      clearQueryClipboardMirror();
      if (runtimeDesc) Object.defineProperty(window, "eidosRuntime", runtimeDesc);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("does not hijack cross-bubble selections and clears mirror when copying plain messages", async () => {
    clearQueryClipboardMirror();
    const clipboardDesc = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    const runtimeDesc = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: writeTextMock } });

    try {
      const agentRef: InputReference = {
        id: "123412341234" + "a".repeat(52),
        kind: "agent",
        label: "researcher",
        source: "researcher",
        sha256: "b".repeat(64),
        status: "selection",
        size: 16,
      };
      const userItem = shellItem({
        id: "user-cross",
        kind: "user_message",
        status: "completed",
        toolCall: undefined,
        content: `[@researcher](ref:${agentRef.id.slice(0, 12)}) 问题文本`,
        references: [agentRef],
      });
      const assistantItem = shellItem({
        id: "assistant-cross",
        kind: "assistant_message",
        ordinal: 2,
        status: "completed",
        toolCall: undefined,
        content: `[@researcher](ref:${agentRef.id.slice(0, 12)}) 问题文本`,
      });
      // Make assistant content identical to user plain text so mirror-collision would trigger if not cleared
      assistantItem.content = "@researcher 问题文本";

      const { container } = render(
        <ExecutionFeed
          items={[userItem, assistantItem]}
          runs={[{ ...baseRun, status: "succeeded" }]}
          approvals={[]}
        />,
      );

      // 1. Cross-bubble selection from UserMessage bubble into AssistantMessage must not hijack onCopy
      const userBubble = container.querySelector(".user-message-bubble") as HTMLElement;
      const assistantEl = document.getElementById("item-assistant-cross")!;
      const crossRange = document.createRange();
      crossRange.setStart(userBubble, 0);
      crossRange.setEnd(assistantEl, assistantEl.childNodes.length);
      const selection = window.getSelection()!;
      selection.removeAllRanges();
      selection.addRange(crossRange);

      const store = new Map<string, string>();
      fireEvent.copy(userBubble, {
        clipboardData: {
          getData: (t: string) => store.get(t) ?? "",
          setData: (t: string, v: string) => { store.set(t, v); },
        },
      });
      expect(store.has(QUERY_CLIPBOARD_MIME)).toBe(false);

      // 2. Copy user message with CopyButton (populates mirror), then copy plain assistant message -> mirror cleared
      const copyButtons = screen.getAllByRole("button", { name: "复制内容" });
      fireEvent.click(copyButtons[0]!);
      await waitFor(() => expect(writeTextMock).toHaveBeenCalledWith("@researcher 问题文本"));

      fireEvent.click(copyButtons[1]!);
      await waitFor(() => expect(writeTextMock).toHaveBeenCalledTimes(2));

      const readAfterPlain = readQueryFromClipboardEvent({
        preventDefault: vi.fn(),
        clipboardData: {
          getData: (t: string) => t === "text/plain" ? "@researcher 问题文本" : "",
          setData: vi.fn(),
        },
      });
      expect(readAfterPlain).toBeUndefined();
    } finally {
      window.getSelection()?.removeAllRanges();
      clearQueryClipboardMirror();
      if (clipboardDesc) Object.defineProperty(navigator, "clipboard", clipboardDesc);
      else delete (navigator as Partial<Navigator>).clipboard;
      if (runtimeDesc) Object.defineProperty(window, "eidosRuntime", runtimeDesc);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("handles UserMessage edit-mode mid-sentence caret, skill re-validation, submit guard, and cancel/re-enter race", async () => {
    clearQueryClipboardMirror();
    const runtimeDesc = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const updatedSkill: InputReference = {
      id: "aaaa9999bbbb" + "0".repeat(52),
      kind: "skill",
      label: "docx",
      source: "skills/docx",
      sha256: "1".repeat(64),
      status: "selection",
      size: 16,
    };
    const validAgent: InputReference = {
      id: "cccc8888dddd" + "2".repeat(52),
      kind: "agent",
      label: "researcher",
      source: "researcher",
      sha256: "3".repeat(64),
      status: "selection",
      size: 16,
    };
    let resolveAgentList: ((value: unknown) => void) | undefined;
    const refById = new Map([updatedSkill, validAgent].map((r) => [r.id, r]));
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readInput: vi.fn(async (id: string) => {
          const matched = refById.get(id);
          if (!matched) throw new Error("RESOURCE_NOT_FOUND");
          return { reference: matched, text: "ok" };
        }),
        listSkills: vi.fn().mockResolvedValue({
          skills: [{ qualifiedId: "skills/docx", name: "docx", enabled: false, available: true, contentHash: "new" }],
        }),
        listAgentTypes: vi.fn().mockImplementation(
          () => new Promise((resolve) => { resolveAgentList = resolve; }),
        ),
      },
    });

    let firstView: ReturnType<typeof render> | undefined;
    try {
      const onEditResend = vi.fn().mockResolvedValue(true);
      const userItem = shellItem({
        id: "user-edit-race",
        kind: "user_message",
        status: "completed",
        toolCall: undefined,
        content: "前面后面",
        references: [updatedSkill],
      });
      firstView = render(
        <ExecutionFeed
          items={[userItem]}
          runs={[{ ...baseRun, status: "succeeded" }]}
          approvals={[]}
          onEditResend={onEditResend}
        />,
      );
      const { container } = firstView;

      // 1. Paste of disabled skill from item.references degrades it instead of blindly exempting it
      fireEvent.click(screen.getByRole("button", { name: "编辑并重新发送" }));
      const richEditor = container.querySelector(".user-message-editor .composer-rich-editor") as HTMLDivElement;
      const skillPayload = buildQueryClipboardPayload(
        `[$docx](ref:${updatedSkill.id.slice(0, 12)})`,
        [updatedSkill],
      );
      fireEvent.paste(richEditor, {
        clipboardData: {
          getData: (type: string) => type === QUERY_CLIPBOARD_MIME ? serializeQueryClipboardPayload(skillPayload) : "",
          setData: vi.fn(),
          files: [],
        },
      });
      await waitFor(() => {
        expect(screen.getByText("部分引用已更新或不可用，已降级为纯文本。")).toBeInTheDocument();
      });

      // 2. Async agent paste disables submit while in flight, and cancelling/re-entering drops stale result
      const agentPayload = buildQueryClipboardPayload(
        `[@researcher](ref:${validAgent.id.slice(0, 12)})`,
        [validAgent],
      );
      fireEvent.paste(richEditor, {
        clipboardData: {
          getData: (type: string) => type === QUERY_CLIPBOARD_MIME ? serializeQueryClipboardPayload(agentPayload) : "",
          setData: vi.fn(),
          files: [],
        },
      });
      expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();

      // Cancel and re-enter edit mode before agent validation resolves
      fireEvent.click(screen.getByRole("button", { name: "取消" }));
      fireEvent.click(screen.getByRole("button", { name: "编辑并重新发送" }));

      resolveAgentList?.({
        agents: [
          { name: "researcher", source: "r.toml", enabled: true, available: true, definitionVersion: validAgent.sha256 },
        ],
      });
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
      });
      expect(container.querySelector(".user-message-editor .composer-rich-editor")?.textContent).toBe("docx 前面后面");
      fireEvent.click(screen.getByRole("button", { name: "取消" }));
    } finally {
      firstView?.unmount();
      clearQueryClipboardMirror();
      if (runtimeDesc) Object.defineProperty(window, "eidosRuntime", runtimeDesc);
      else delete (window as Partial<Window>).eidosRuntime;
    }

    // 3. Separate plain userItem: mid-sentence paste from textarea -> InlineRichEditor keeps caret right after pasted token
    clearQueryClipboardMirror();
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readInput: vi.fn().mockResolvedValue({ reference: validAgent, text: "ok" }),
        listAgentTypes: vi.fn().mockResolvedValue({
          agents: [
            { name: "researcher", source: "r.toml", enabled: true, available: true, definitionVersion: validAgent.sha256 },
          ],
        }),
      },
    });
    try {
      const plainItem = shellItem({
        id: "user-edit-caret",
        kind: "user_message",
        status: "completed",
        toolCall: undefined,
        content: "前面后面",
      });
      const { container } = render(
        <ExecutionFeed
          items={[plainItem]}
          runs={[{ ...baseRun, status: "succeeded" }]}
          approvals={[]}
          onEditResend={vi.fn().mockResolvedValue(true)}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "编辑并重新发送" }));
      const textarea = screen.getByRole("textbox", { name: "编辑最近一次提问" }) as HTMLTextAreaElement;
      textarea.setSelectionRange(2, 2);

      const agentPayload = buildQueryClipboardPayload(
        `[@researcher](ref:${validAgent.id.slice(0, 12)})`,
        [validAgent],
      );
      fireEvent.paste(textarea, {
        clipboardData: {
          getData: (type: string) => type === QUERY_CLIPBOARD_MIME ? serializeQueryClipboardPayload(agentPayload) : "",
          setData: vi.fn(),
          files: [],
        },
      });

      await waitFor(() => {
        expect(container.querySelector(".user-message-editor .composer-rich-editor")).not.toBeNull();
      });
      const richEditor = container.querySelector(".user-message-editor .composer-rich-editor") as HTMLDivElement;
      const sel = window.getSelection()!;
      expect(sel.rangeCount).toBeGreaterThan(0);
      const range = sel.getRangeAt(0);
      expect(range.collapsed).toBe(true);
      // Caret must sit right before "后面" (NOT at the end of "后面")
      expect(range.startContainer).toBe(richEditor.lastChild);
      expect(range.startOffset).toBe(0);
    } finally {
      window.getSelection()?.removeAllRanges();
      clearQueryClipboardMirror();
      if (runtimeDesc) Object.defineProperty(window, "eidosRuntime", runtimeDesc);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("keeps async paste and submit button alive in UserMessage edit mode when item.references refreshes in flight", async () => {
    clearQueryClipboardMirror();
    const runtimeDesc = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    const existingFile: InputReference = {
      id: "61".repeat(32),
      kind: "file",
      label: "context.md",
      source: "/ws/context.md",
      sha256: "a".repeat(64),
      status: "content",
      size: 32,
    };
    const validAgent: InputReference = {
      id: "62".repeat(32),
      kind: "agent",
      label: "researcher",
      source: "researcher",
      sha256: "b".repeat(64),
      status: "selection",
      size: 16,
    };
    let resolveAgentList: ((value: unknown) => void) | undefined;
    const refById = new Map([existingFile, validAgent].map((r) => [r.id, r]));
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readInput: vi.fn(async (id: string) => {
          const matched = refById.get(id);
          if (!matched) throw new Error("RESOURCE_NOT_FOUND");
          return { reference: matched, text: "ok" };
        }),
        listAgentTypes: vi.fn().mockImplementation(
          () => new Promise((resolve) => { resolveAgentList = resolve; }),
        ),
      },
    });

    try {
      const onEditResend = vi.fn().mockResolvedValue(true);
      const userItem = shellItem({
        id: "user-edit-refresh",
        kind: "user_message",
        status: "completed",
        toolCall: undefined,
        content: "原有提问 ",
        references: [existingFile],
      });
      const { container, rerender } = render(
        <ExecutionFeed
          items={[userItem]}
          runs={[{ ...baseRun, status: "succeeded" }]}
          approvals={[]}
          onEditResend={onEditResend}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "编辑并重新发送" }));
      const textarea = screen.getByRole("textbox", { name: "编辑最近一次提问" }) as HTMLTextAreaElement;
      textarea.setSelectionRange(5, 5);

      const agentPayload = buildQueryClipboardPayload(
        `[@researcher](ref:${validAgent.id.slice(0, 12)})`,
        [validAgent],
      );
      fireEvent.paste(textarea, {
        clipboardData: {
          getData: (type: string) => type === QUERY_CLIPBOARD_MIME ? serializeQueryClipboardPayload(agentPayload) : "",
          setData: vi.fn(),
          files: [],
        },
      });
      expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();

      // Simulate background refreshCompletedSession replacing item.references array identity
      rerender(
        <ExecutionFeed
          items={[{ ...userItem, references: [{ ...existingFile }] }]}
          runs={[{ ...baseRun, status: "succeeded" }]}
          approvals={[]}
          onEditResend={onEditResend}
        />,
      );

      resolveAgentList?.({
        agents: [
          { name: "researcher", source: "r.toml", enabled: true, available: true, definitionVersion: validAgent.sha256 },
        ],
      });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
      });
      expect(container.querySelector(".user-message-editor .composer-rich-editor")?.textContent).toContain("researcher");

      fireEvent.click(screen.getByRole("button", { name: "发送" }));
      expect(onEditResend).toHaveBeenCalledWith(
        expect.objectContaining({ id: baseRun.id }),
        `原有提问 [@researcher](ref:${validAgent.id.slice(0, 12)})`,
        [existingFile.id, validAgent.id],
      );
    } finally {
      window.getSelection()?.removeAllRanges();
      clearQueryClipboardMirror();
      if (runtimeDesc) Object.defineProperty(window, "eidosRuntime", runtimeDesc);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });
});

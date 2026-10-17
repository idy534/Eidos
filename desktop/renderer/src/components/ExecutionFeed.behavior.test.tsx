import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Item, Run } from "../contracts.js";
import { ArtifactProvider } from "./ArtifactContext.js";
import { ExecutionFeed } from "./ExecutionFeed.js";

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
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );
}

describe("automation source and tool results", () => {
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(screen.getByText("企业微信消息")).toBeInTheDocument();
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
      runs={[baseRun]} approvals={[]} onApprove={() => {}} onReject={() => {}} />);
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
    expect(summary?.querySelector(".tool-summary-text")).toHaveTextContent("已创建定时任务 · 每小时喝水提醒");
  });
});

describe("conversation fork actions", () => {
  it("lets the user request an earlier page and disables repeated clicks during loading", () => {
    const onLoadOlder = vi.fn();
    const items: Item[] = [{ id: "recent", runId: baseRun.id, sessionId: baseRun.sessionId,
      kind: "user_message", ordinal: 1, status: "completed", content: "最近输入", createdAt: 1 }];
    const { rerender } = render(<ExecutionFeed items={items} runs={[baseRun]} approvals={[]}
      onApprove={() => {}} onReject={() => {}} onLoadOlder={onLoadOlder} />);
    fireEvent.click(screen.getByRole("button", { name: "加载更早的记录" }));
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
    rerender(<ExecutionFeed items={items} runs={[baseRun]} approvals={[]}
      onApprove={() => {}} onReject={() => {}} onLoadOlder={onLoadOlder} loadingOlder />);
    expect(screen.getByRole("button", { name: "正在读取历史…" })).toBeDisabled();
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
      runs={[oldRun, baseRun]} approvals={[]} onApprove={() => {}} onReject={() => {}} onFork={onFork} />);
    expect(screen.getAllByRole("button", { name: "从这里分叉" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "从这里分叉" }));
    expect(onFork).toHaveBeenCalledWith(selected);
  });

  it("keeps inherited answers forkable without exposing execution revision or feedback", () => {
    const historical = { ...baseRun, status: "succeeded" as const, inherited: true };
    const selected = answer(historical, { inherited: true });
    render(<ExecutionFeed items={[selected]} runs={[historical]} approvals={[]}
      onApprove={() => {}} onReject={() => {}} onFork={() => {}} allowRegenerate allowEditResend allowFeedback />);
    expect(screen.getByRole("button", { name: "从这里分叉" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "重新生成" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "点赞" })).not.toBeInTheDocument();
  });

  it("does not offer a fork for incomplete answers or progress inside a tool round", () => {
    const ended = { ...baseRun, status: "stopped" as const };
    render(<ExecutionFeed items={[answer(ended, { incomplete: true })]} runs={[ended]} approvals={[]}
      onApprove={() => {}} onReject={() => {}} onFork={() => {}} />);
    expect(screen.queryByRole("button", { name: "从这里分叉" })).not.toBeInTheDocument();
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
    expect(screen.getByRole("alert")).toHaveTextContent("模型调用“读取文件”时提供的“文件路径”参数不符合要求");
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    const turnResultsSection = container.querySelector(".turn-results");
    expect(turnResultsSection).toBeInTheDocument();
    expect(turnResultsSection).toHaveClass("turn-results--standalone");
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("任务已取消。");
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
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(container.querySelector(".user-message-bubble > p.eidos-chat-prose")?.textContent).toBe("第一行\n第二行");
    expect(container.querySelector(".process-text > .markdown-body.eidos-chat-prose")).not.toBeNull();
    expect(container.querySelector(".feed-item--assistant > .markdown-body.eidos-chat-prose strong")).toHaveTextContent("结论");
    expect(container.querySelectorAll(".eidos-chat-prose")).toHaveLength(3);
    expect(container.querySelector(".eidos-chat-prose .tool-item, .eidos-chat-prose .feed-item-footer")).toBeNull();
  });
});

describe("ExecutionFeed shell output", () => {
  it("updates from cumulative live output to a completed result without duplicating it", () => {
    const activeItem = shellItem();
    const { container, rerender } = renderFeed(activeItem);

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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    const output = container.querySelector(".shell-output");
    expect(output?.textContent).toBe("first line\nsecond line\n");
    expect(container.querySelectorAll(".shell-output")).toHaveLength(1);
    expect(screen.getByText("✓ 成功")).toBeInTheDocument();
  });

  it("renders capsule exit code badge next to 失败 and hides shell_exit_nonzero on command failure", () => {
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

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
    const copyButton = container.querySelector(".shell-copy-button") as HTMLButtonElement | null;
    expect(copyButton).toBeInTheDocument();
    expect(copyButton?.title).toBe("复制命令");

    fireEvent.click(copyButton!);

    expect(writeTextMock).toHaveBeenCalledWith('grep -rn "TODO" .');
    await waitFor(() => {
      expect(copyButton?.title).toBe("已复制命令");
    });
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
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
          respondingApprovalIds={new Set()}
          respondingKindByApprovalId={{}}
          onApprove={() => {}}
          onReject={() => {}}
        />,
      );
      rerender(
        <ExecutionFeed
          items={[assistantItem("hello world")]}
          runs={[baseRun]}
          approvals={[]}
          respondingApprovalIds={new Set()}
          respondingKindByApprovalId={{}}
          onApprove={() => {}}
          onReject={() => {}}
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
          respondingApprovalIds={new Set()}
          respondingKindByApprovalId={{}}
          onApprove={() => {}}
          onReject={() => {}}
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
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

  it.each(["completed", "failed", "canceled"] as const)("shows clarification errors with %s item status without inventing a skipped answer", (status) => {
    const item = toolCallItem("request_user_input", {status}, {
      status: status === "completed" ? "completed" : status,
      argumentsJson: JSON.stringify({questions: [{id: "q", question: "Constraints?", type: "text"}]}),
      resultJson: JSON.stringify({outcome: "error", code: "invalid_arguments", summary: "No question was submitted"}),
    });
    render(<ExecutionFeed items={[item]} runs={[baseRun]} approvals={[]}
      respondingApprovalIds={new Set()} respondingKindByApprovalId={{}}
      onApprove={() => {}} onReject={() => {}} />);
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(screen.getByText("正在询问 2 个问题")).toBeInTheDocument();
    expect(screen.getByText("正在等待你的回答")).toBeInTheDocument();
  });

  it("renders completed request_user_input as collapsible summary with questions and answers", () => {
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(screen.getByText("正在制定计划…")).toBeInTheDocument();
  });

  it.each(["failed", "canceled", "declined", "completed"] as const)("shows an unsuccessful write_plan with %s item status as a tool result", (status) => {
    const item = toolCallItem("write_plan", {status}, {
      status: status === "declined" ? "failed" : status,
      argumentsJson: JSON.stringify({title: "Rejected plan"}),
      resultJson: JSON.stringify({outcome: "error", code: "plan_write_rejected", summary: "plan_not_found"}),
    });
    render(<ExecutionFeed items={[item]} runs={[baseRun]} approvals={[]}
      respondingApprovalIds={new Set()} respondingKindByApprovalId={{}}
      onApprove={() => {}} onReject={() => {}} />);
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
        onOpenPlan={onOpenPlan}
      />,
    );

    expect(screen.getByText("系统重构方案")).toBeInTheDocument();
    expect(screen.getByText("查看计划 →")).toBeInTheDocument();

    const planCard = screen.getByRole("button", { name: /系统重构方案/ });
    fireEvent.click(planCard);
    expect(onOpenPlan).toHaveBeenCalledTimes(1);
  });

  it("renders write_plan in the response area outside process group, even when other tools exist", () => {
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

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

  it("renders subagent tool as direct button and calls onOpenSubagent on click", () => {
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
        onOpenSubagent={onOpenSubagent}
      />,
    );

    const btn = container.querySelector("button.tool-file-link");
    expect(btn).not.toBeNull();
    expect(btn?.textContent).toBe("reviewer");
    expect(container.textContent).toContain("创建智能体 · reviewer");
    expect(container.querySelector("details.tool-item")).toBeNull();

    fireEvent.click(btn!);
    expect(onOpenSubagent).toHaveBeenCalledWith({ id: "child-42", taskName: "reviewer" });
  });

  it("shows error summary in title attribute on hover when subagent tool fails", () => {
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
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
        onOpenSubagent={() => {}}
      />,
    );

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
        onApprove={() => {}}
        onReject={() => {}}
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

    // 2. Click cancel button -> dialog closes, onRegenerate still not called
    const cancelBtn = screen.getByRole("button", { name: "取消" });
    fireEvent.click(cancelBtn);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onRegenerate).not.toHaveBeenCalled();

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
        onApprove={() => {}}
        onReject={() => {}}
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
        onApprove={() => {}}
        onReject={() => {}}
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
        <ExecutionFeed items={[imageItem]} runs={[baseRun]} approvals={[]} onApprove={() => {}} onReject={() => {}} />
      </ArtifactProvider>);
      const details = container.querySelector("details")!;
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
            onApprove={() => {}}
            onReject={() => {}}
            onOpenFile={openFile}
          />
        </ArtifactProvider>,
      );

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
            onApprove={() => {}}
            onReject={() => {}}
            onOpenFile={openFile}
          />
        </ArtifactProvider>,
      );

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
});

describe("ExecutionFeed memory tools static rendering", () => {
  it("renders memory tools without details/summary and without expanding", () => {
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
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    const searchRow = screen.getByText('正在搜索记忆 "2026-10-13|最近|latest|rollout"');
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
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    const row = screen.getByText("正在查看工具输出");
    expect(row).toBeInTheDocument();
    expect(row.closest("details.tool-item")).toBeNull();
    expect(row.closest(".tool-item--read-done")).not.toBeNull();
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
          onApprove={() => {}}
          onReject={() => {}}
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

      render(
        <ExecutionFeed
          items={[item]}
          runs={[baseRun]}
          approvals={[]}
          onApprove={() => {}}
          onReject={() => {}}
        />,
      );

      const button = screen.getByRole("button", { name: "translator" });
      expect(button).toBeInTheDocument();
      expect(screen.getByText(/已读取.*整个技能包/)).toBeInTheDocument();

      fireEvent.click(button);

      expect(await screen.findByRole("heading", { name: "translator" })).toBeInTheDocument();
      expect(await screen.findByText("Translate text between languages.")).toBeInTheDocument();
      expect(screen.queryByText("未提供技能说明")).not.toBeInTheDocument();
      expect(await screen.findByText("Prompt details.")).toBeInTheDocument();

      const closeBtn = screen.getByRole("button", { name: "关闭技能详情" });
      fireEvent.click(closeBtn);
      expect(screen.queryByRole("heading", { name: "translator" })).not.toBeInTheDocument();
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
          onApprove={() => {}}
          onReject={() => {}}
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



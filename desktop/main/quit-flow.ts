import type { BrowserWindow, MessageBoxOptions, MessageBoxReturnValue } from "electron";

export interface ActiveRunInfo {
  id: string;
  sessionId: string;
  status: string;
}

export type QuitDecision = "quit_immediately" | "confirm_quit" | "cancel_quit";

export interface QuitFlowDeps {
  getActiveRuns: () => Promise<ActiveRunInfo[]>;
  showMessageBox: (window: BrowserWindow | null, options: MessageBoxOptions) => Promise<MessageBoxReturnValue>;
  gracefulShutdown: () => Promise<void>;
  quitApp: () => void;
}

export class QuitFlowManager {
  private isQuitting = false;

  public get quitting(): boolean {
    return this.isQuitting;
  }

  public async evaluateQuit(
    window: BrowserWindow | null,
    deps: QuitFlowDeps,
  ): Promise<QuitDecision> {
    if (this.isQuitting) {
      return "quit_immediately";
    }

    try {
      const activeRuns = await deps.getActiveRuns();
      if (!Array.isArray(activeRuns) || activeRuns.length === 0) {
        this.isQuitting = true;
        await deps.gracefulShutdown();
        return "quit_immediately";
      }

      // Active runs exist — prompt user with confirmation dialog
      const response = await deps.showMessageBox(window, {
        type: "warning",
        buttons: ["终止任务并退出", "取消"],
        defaultId: 1, // Default focus is Cancel button
        cancelId: 1,
        title: "退出 Eidos",
        message: `尚有 ${activeRuns.length} 个运行中的 Agent 任务，确定要退出并终止这些任务吗？`,
        detail: "退出后，正在执行的任务将被中断，未能保存的进度可能会丢失。",
      });

      if (response.response === 0) {
        // User confirmed termination
        this.isQuitting = true;
        await deps.gracefulShutdown();
        return "confirm_quit";
      }

      return "cancel_quit";
    } catch {
      // Fallback on error: abort quit safely to prevent silent task termination
      return "cancel_quit";
    }
  }

  public reset(): void {
    this.isQuitting = false;
  }
}

export interface ActiveRunProjection {
  runIds(): string[];
  count(): number;
}

export interface QuitFlowDependencies {
  hasRuntimeClient(): boolean;
  showQuitDialog(activeRunCount: number): Promise<"return_to_eidos" | "stop_and_exit">;
  onQuitCommitted?(): void;
  cancelRun(runId: string): Promise<unknown>;
  shutdownRuntime(): Promise<unknown>;
  requestFinalQuit(): void;
  log(
    level: "info" | "warn" | "error",
    message: string,
    meta?: Record<string, unknown>,
  ): void;
}

export interface QuitFlowState {
  isQuitting: boolean;
  dialogOpen: boolean;
  quitCommitted: boolean;
  shutdownStarted: boolean;
  quitCanContinue: boolean;
}

export function guardIpcHandlerForQuit<Event, Args extends unknown[], Result>(
  isQuitCommitted: () => boolean,
  handler: (event: Event, ...args: Args) => Result | Promise<Result>,
): (event: Event, ...args: Args) => Promise<Result> {
  return async (event, ...args) => {
    if (isQuitCommitted()) {
      return new Promise<Result>(() => {});
    }
    try {
      return await handler(event, ...args);
    } catch (error) {
      if (isQuitCommitted()) {
        return new Promise<Result>(() => {});
      }
      throw error;
    }
  };
}

export class QuitFlowController {
  private isQuitting = false;
  private dialogOpen = false;
  private quitCommitted = false;
  private shutdownStarted = false;
  private quitCanContinue = false;

  constructor(
    private readonly activeRunProjection: ActiveRunProjection,
    private readonly deps: QuitFlowDependencies,
  ) {}

  getState(): QuitFlowState {
    return {
      isQuitting: this.isQuitting,
      dialogOpen: this.dialogOpen,
      quitCommitted: this.quitCommitted,
      shutdownStarted: this.shutdownStarted,
      quitCanContinue: this.quitCanContinue,
    };
  }

  handleBeforeQuit(event: { preventDefault(): void }): void {
    if (!this.deps.hasRuntimeClient() || this.quitCanContinue) {
      return;
    }

    event.preventDefault();
    this.isQuitting = true;

    if (this.quitCommitted || this.shutdownStarted || this.dialogOpen) {
      return;
    }

    const activeCount = this.activeRunProjection.count();

    if (activeCount === 0) {
      this.commitQuit();
      this.executeShutdownAndQuit();
      return;
    }

    this.dialogOpen = true;
    this.deps
      .showQuitDialog(activeCount)
      .then((choice) => {
        this.dialogOpen = false;

        if (choice === "return_to_eidos") {
          this.deps.log("info", "User chose to return to Eidos");
          this.isQuitting = false;
        } else {
          this.deps.log("info", "User confirmed quit with active runs", {
            activeRunCount: activeCount,
          });
          this.commitQuit();

          const runIdsToCancel = this.activeRunProjection.runIds();
          const cancelPromises = runIdsToCancel.map((id) =>
            this.deps.cancelRun(id).catch((err: unknown) => {
              this.deps.log("warn", "Failed to cancel run before quit", {
                runId: id,
                error: err instanceof Error ? err.message : String(err),
              });
            }),
          );

          void Promise.allSettled(cancelPromises).then(() => {
            this.executeShutdownAndQuit();
          });
        }
      })
      .catch((err: unknown) => {
        this.dialogOpen = false;
        this.deps.log("error", "Quit dialog error", {
          error: err instanceof Error ? err.message : String(err),
        });
        this.isQuitting = false;
      });
  }

  private commitQuit(): void {
    if (this.quitCommitted) {
      return;
    }
    this.quitCommitted = true;
    try {
      this.deps.onQuitCommitted?.();
    } catch (err: unknown) {
      this.deps.log("warn", "Quit teardown hook failed", {
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  private executeShutdownAndQuit(): void {
    if (this.shutdownStarted) {
      return;
    }
    this.shutdownStarted = true;

    this.deps
      .shutdownRuntime()
      .catch((err: unknown) => {
        this.deps.log("error", "Runtime shutdown failed during quit", {
          error: err instanceof Error ? err.message : String(err),
        });
      })
      .finally(() => {
        this.quitCanContinue = true;
        this.deps.requestFinalQuit();
      });
  }
}

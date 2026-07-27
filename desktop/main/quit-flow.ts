export interface ActiveRunProjection {
  runIds(): string[];
  count(): number;
}

export interface QuitFlowDependencies {
  hasRuntimeClient(): boolean;
  showQuitDialog(activeRunCount: number): Promise<"return_to_eidos" | "stop_and_exit">;
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
  shutdownStarted: boolean;
  quitCanContinue: boolean;
}

export class QuitFlowController {
  private isQuitting = false;
  private dialogOpen = false;
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

    if (this.shutdownStarted || this.dialogOpen) {
      return;
    }

    const activeCount = this.activeRunProjection.count();

    if (activeCount === 0) {
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

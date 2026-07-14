export type RuntimeStatus =
  | { state: "starting" }
  | {
      state: "ready";
      protocolVersion: number;
      runtimeVersion: string;
      runShell: boolean;
    }
  | { state: "error"; message: string };

declare global {
  interface Window {
    eidosRuntime: {
      getStatus: () => Promise<RuntimeStatus>;
      onStatus: (callback: (status: RuntimeStatus) => void) => () => void;
    };
  }
}

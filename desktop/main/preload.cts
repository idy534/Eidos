import { contextBridge, ipcRenderer } from "electron";


type RuntimeStatus =
  | { state: "starting" }
  | {
      state: "ready";
      protocolVersion: number;
      runtimeVersion: string;
      runShell: boolean;
    }
  | { state: "error"; message: string };

contextBridge.exposeInMainWorld("eidosRuntime", {
  getStatus: (): Promise<RuntimeStatus> => ipcRenderer.invoke("runtime:get-status"),
  onStatus: (callback: (status: RuntimeStatus) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, status: RuntimeStatus) => {
      callback(status);
    };
    ipcRenderer.on("runtime:status", listener);
    return () => ipcRenderer.removeListener("runtime:status", listener);
  },
});

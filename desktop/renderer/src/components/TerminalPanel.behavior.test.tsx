import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  EidosRuntimeAPI,
  TerminalDataEvent,
  TerminalExitEvent,
} from "../contracts.js";
import { TerminalPanel } from "./TerminalPanel.js";

interface FakeTerminalHandle {
  write: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
  emitData(data: string): void;
  emitResize(cols: number, rows: number): void;
}

const terminalInstances = vi.hoisted(() => [] as FakeTerminalHandle[]);

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    element = document.createElement("div");
    write = vi.fn();
    dispose = vi.fn();
    focus = vi.fn();
    open = vi.fn();
    loadAddon = vi.fn();
    private dataListener?: (data: string) => void;
    private resizeListener?: (size: { cols: number; rows: number }) => void;

    constructor() {
      terminalInstances.push(this);
    }

    onData(listener: (data: string) => void) {
      this.dataListener = listener;
      return { dispose: vi.fn() };
    }

    onResize(listener: (size: { cols: number; rows: number }) => void) {
      this.resizeListener = listener;
      return { dispose: vi.fn() };
    }

    emitData(data: string) {
      this.dataListener?.(data);
    }

    emitResize(cols: number, rows: number) {
      this.resizeListener?.({ cols, rows });
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit = vi.fn();
  },
}));
vi.mock("@xterm/xterm/css/xterm.css", () => ({}));

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("TerminalPanel", () => {
  let dataListener: ((event: TerminalDataEvent) => void) | undefined;
  let exitListener: ((event: TerminalExitEvent) => void) | undefined;
  let api: Partial<EidosRuntimeAPI>;

  beforeEach(() => {
    terminalInstances.length = 0;
    api = {
      createTerminal: vi.fn().mockResolvedValue({ terminalId: "terminal-1", sessionId: "session-a" }),
      writeTerminal: vi.fn().mockResolvedValue(undefined),
      resizeTerminal: vi.fn().mockResolvedValue(undefined),
      closeTerminal: vi.fn().mockResolvedValue(undefined),
      onTerminalData: vi.fn().mockImplementation((listener) => {
        dataListener = listener;
        return vi.fn();
      }),
      onTerminalExit: vi.fn().mockImplementation((listener) => {
        exitListener = listener;
        return vi.fn();
      }),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("bridges xterm input and output through the owned terminal session", async () => {
    const { unmount } = render(<TerminalPanel sessionId="session-a" active={true} />);

    await waitFor(() => expect(api.createTerminal).toHaveBeenCalledWith("session-a"));
    expect(terminalInstances).toHaveLength(1);

    act(() => dataListener?.({ terminalId: "terminal-1", data: "ready\r\n" }));
    expect(terminalInstances[0]?.write).toHaveBeenCalledWith("ready\r\n");

    act(() => terminalInstances[0]?.emitData("pwd\r"));
    expect(api.writeTerminal).toHaveBeenCalledWith("terminal-1", "pwd\r");

    act(() => terminalInstances[0]?.emitResize(120, 36));
    expect(api.resizeTerminal).toHaveBeenCalledWith("terminal-1", 120, 36);

    unmount();
    expect(api.closeTerminal).toHaveBeenCalledWith("terminal-1");
  });

  it("reports terminal exit without accepting data for another terminal", async () => {
    render(<TerminalPanel sessionId="session-a" active={true} />);
    await waitFor(() => expect(api.createTerminal).toHaveBeenCalled());

    act(() => dataListener?.({ terminalId: "terminal-other", data: "ignored" }));
    expect(terminalInstances[0]?.write).not.toHaveBeenCalled();

    act(() => exitListener?.({ terminalId: "terminal-1", exitCode: 0 }));
    expect(screen.getByRole("status")).toHaveTextContent("终端已退出");
  });
});

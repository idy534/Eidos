import { useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import {
  DEFAULT_TERMINAL_SHELL_KEY,
  disposeSessionTerminals,
  disposeTerminal,
  getPreferredTerminalShell,
  registerTerminalDisposer,
  setPreferredTerminalShell,
  unregisterTerminalDisposer,
} from "./terminal-shell-preference.js";

export {
  DEFAULT_TERMINAL_SHELL_KEY,
  disposeSessionTerminals,
  disposeTerminal,
  getPreferredTerminalShell,
  setPreferredTerminalShell,
};

interface LiveTerminalEntry {
  tabKey: string;
  sessionId: string;
  container: HTMLDivElement;
  terminal: Terminal;
  fitAddon: FitAddon;
  terminalId: string | null;
  error: string | undefined;
  exitMessage: string | undefined;
  disposed: boolean;
  listeners: Set<() => void>;
  cleanup: () => void;
}

const liveTerminals = new Map<string, LiveTerminalEntry>();

function notifyEntryListeners(entry: LiveTerminalEntry): void {
  for (const listener of entry.listeners) {
    listener();
  }
}

export function _resetTerminalRegistryForTest(): void {
  for (const tabKey of [...liveTerminals.keys()]) {
    const entry = liveTerminals.get(tabKey);
    if (!entry) continue;
    liveTerminals.delete(tabKey);
    unregisterTerminalDisposer(tabKey);
    entry.disposed = true;
    entry.cleanup();
    entry.listeners.clear();
    if (entry.container.parentNode) {
      entry.container.parentNode.removeChild(entry.container);
    }
  }
}

interface TerminalPanelProps {
  sessionId: string;
  active: boolean;
  workspaceRoot?: string;
  projectId?: string;
  keepAlive?: boolean;
  tabKey?: string;
}

function createXtermInstance(): { terminal: Terminal; fitAddon: FitAddon } {
  const rootStyle = getComputedStyle(document.documentElement);
  const terminal = new Terminal({
    cursorBlink: true,
    fontFamily: rootStyle.getPropertyValue("--font-code").trim() || "ui-monospace, monospace",
    fontSize: 13,
    scrollback: 5_000,
    theme: {
      background: rootStyle.getPropertyValue("--surface").trim() || "#fbfaf7",
      foreground: rootStyle.getPropertyValue("--text").trim() || "#242422",
      cursor: rootStyle.getPropertyValue("--text").trim() || "#242422",
      selectionBackground: rootStyle.getPropertyValue("--surface-selected").trim() || "#e7e5df",
    },
  });
  const fitAddon = new FitAddon();
  terminal.loadAddon(fitAddon);
  return { terminal, fitAddon };
}

function invokeCreateTerminal(
  sessionId: string,
  workspaceRoot: string | undefined,
  projectId: string | undefined,
) {
  const preferred = getPreferredTerminalShell().trim();
  return preferred
    ? window.eidosRuntime.createTerminal(sessionId, workspaceRoot, projectId, preferred)
    : window.eidosRuntime.createTerminal(sessionId, workspaceRoot, projectId);
}

function getOrCreateLiveTerminal(
  tabKey: string,
  sessionId: string,
  workspaceRoot: string | undefined,
  projectId: string | undefined,
): LiveTerminalEntry {
  const existing = liveTerminals.get(tabKey);
  if (existing && !existing.disposed) return existing;

  const container = document.createElement("div");
  container.className = "terminal-panel__inner";
  const { terminal, fitAddon } = createXtermInstance();
  terminal.open(container);

  const entry: LiveTerminalEntry = {
    tabKey,
    sessionId,
    container,
    terminal,
    fitAddon,
    terminalId: null,
    error: undefined,
    exitMessage: undefined,
    disposed: false,
    listeners: new Set(),
    cleanup: () => {},
  };

  const dataSubscription = terminal.onData((data) => {
    if (entry.terminalId) {
      void window.eidosRuntime.writeTerminal(entry.terminalId, data).catch(() => undefined);
    }
  });
  const resizeSubscription = terminal.onResize(({ cols, rows }) => {
    if (entry.terminalId) {
      void window.eidosRuntime.resizeTerminal(entry.terminalId, cols, rows).catch(() => undefined);
    }
  });
  const unsubscribeData = window.eidosRuntime.onTerminalData((event) => {
    if (event.terminalId === entry.terminalId) terminal.write(event.data);
  });
  const unsubscribeExit = window.eidosRuntime.onTerminalExit((event) => {
    if (event.terminalId !== entry.terminalId) return;
    entry.terminalId = null;
    entry.exitMessage = event.signal
      ? `终端已退出（信号 ${event.signal}）`
      : `终端已退出（状态 ${event.exitCode}）`;
    notifyEntryListeners(entry);
  });

  entry.cleanup = () => {
    dataSubscription.dispose();
    resizeSubscription.dispose();
    unsubscribeData();
    unsubscribeExit();
    const terminalId = entry.terminalId;
    entry.terminalId = null;
    if (terminalId) {
      void window.eidosRuntime?.closeTerminal?.(terminalId)?.catch?.(() => undefined);
    }
    terminal.dispose();
  };

  liveTerminals.set(tabKey, entry);
  registerTerminalDisposer(tabKey, sessionId, () => {
    liveTerminals.delete(tabKey);
    entry.disposed = true;
    entry.cleanup();
    entry.listeners.clear();
    if (entry.container.parentNode) {
      entry.container.parentNode.removeChild(entry.container);
    }
  });

  void invokeCreateTerminal(sessionId, workspaceRoot, projectId).then((created) => {
    if (entry.disposed) {
      void window.eidosRuntime?.closeTerminal?.(created.terminalId)?.catch?.(() => undefined);
      return;
    }
    entry.terminalId = created.terminalId;
    notifyEntryListeners(entry);
  }).catch((cause: unknown) => {
    if (entry.disposed) return;
    entry.error = cause instanceof Error ? cause.message : "终端启动失败";
    notifyEntryListeners(entry);
  });

  return entry;
}

export function TerminalPanel({
  sessionId,
  active,
  workspaceRoot,
  projectId,
  keepAlive = false,
  tabKey,
}: TerminalPanelProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const terminalIdRef = useRef<string | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;
  const [error, setError] = useState<string | undefined>(undefined);
  const [exitMessage, setExitMessage] = useState<string | undefined>(undefined);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const fit = () => {
      if (!activeRef.current) return;
      try {
        fitAddonRef.current?.fit();
      } catch {
        // The panel can be hidden while a tab changes. The next visible resize retries.
      }
    };

    if (keepAlive && tabKey) {
      const entry = getOrCreateLiveTerminal(tabKey, sessionId, workspaceRoot, projectId);
      host.replaceChildren(entry.container);
      terminalRef.current = entry.terminal;
      fitAddonRef.current = entry.fitAddon;
      terminalIdRef.current = entry.terminalId;
      setError(entry.error);
      setExitMessage(entry.exitMessage);

      const syncEntry = () => {
        terminalIdRef.current = entry.terminalId;
        setError(entry.error);
        setExitMessage(entry.exitMessage);
        if (entry.terminalId && activeRef.current) {
          fit();
          if (entry.terminal.cols > 0 && entry.terminal.rows > 0) {
            void window.eidosRuntime.resizeTerminal(
              entry.terminalId,
              entry.terminal.cols,
              entry.terminal.rows,
            ).catch(() => undefined);
          }
          entry.terminal.focus();
        }
      };
      entry.listeners.add(syncEntry);

      const resizeObserver = typeof ResizeObserver === "undefined"
        ? undefined
        : new ResizeObserver(fit);
      resizeObserver?.observe(host);
      if (activeRef.current) {
        fit();
        entry.terminal.focus();
      }

      return () => {
        resizeObserver?.disconnect();
        entry.listeners.delete(syncEntry);
        if (entry.container.parentNode === host) {
          host.removeChild(entry.container);
        }
        terminalRef.current = null;
        fitAddonRef.current = null;
        terminalIdRef.current = null;
      };
    }

    let disposed = false;
    const { terminal, fitAddon } = createXtermInstance();
    terminal.open(host);
    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;

    const safeFit = () => {
      if (disposed || !activeRef.current) return;
      fit();
    };

    const dataSubscription = terminal.onData((data) => {
      const terminalId = terminalIdRef.current;
      if (terminalId) void window.eidosRuntime.writeTerminal(terminalId, data).catch(() => undefined);
    });
    const resizeSubscription = terminal.onResize(({ cols, rows }) => {
      const terminalId = terminalIdRef.current;
      if (terminalId) {
        void window.eidosRuntime.resizeTerminal(terminalId, cols, rows).catch(() => undefined);
      }
    });
    const unsubscribeData = window.eidosRuntime.onTerminalData((event) => {
      if (event.terminalId === terminalIdRef.current) terminal.write(event.data);
    });
    const unsubscribeExit = window.eidosRuntime.onTerminalExit((event) => {
      if (event.terminalId !== terminalIdRef.current) return;
      terminalIdRef.current = null;
      setExitMessage(event.signal
        ? `终端已退出（信号 ${event.signal}）`
        : `终端已退出（状态 ${event.exitCode}）`);
    });
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? undefined
      : new ResizeObserver(safeFit);
    resizeObserver?.observe(host);

    void invokeCreateTerminal(sessionId, workspaceRoot, projectId).then((created) => {
      if (disposed) {
        void window.eidosRuntime.closeTerminal(created.terminalId).catch(() => undefined);
        return;
      }
      terminalIdRef.current = created.terminalId;
      safeFit();
      if (terminal.cols > 0 && terminal.rows > 0) {
        void window.eidosRuntime.resizeTerminal(
          created.terminalId,
          terminal.cols,
          terminal.rows,
        ).catch(() => undefined);
      }
      if (activeRef.current) terminal.focus();
    }).catch((cause: unknown) => {
      if (!disposed) setError(cause instanceof Error ? cause.message : "终端启动失败");
    });

    return () => {
      disposed = true;
      resizeObserver?.disconnect();
      dataSubscription.dispose();
      resizeSubscription.dispose();
      unsubscribeData();
      unsubscribeExit();
      const terminalId = terminalIdRef.current;
      terminalIdRef.current = null;
      if (terminalId) void window.eidosRuntime?.closeTerminal?.(terminalId)?.catch?.(() => undefined);
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, keepAlive && tabKey ? [keepAlive, tabKey] : [sessionId, workspaceRoot, projectId]);

  useEffect(() => {
    if (!active) return;
    const frame = window.requestAnimationFrame(() => {
      try {
        fitAddonRef.current?.fit();
      } catch {
        // The containing panel may still be measuring during the first frame.
      }
      terminalRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active]);

  return (
    <section className="terminal-panel" aria-label="终端">
      {error && <p className="terminal-panel__message" role="alert">{error}</p>}
      {exitMessage && <p className="terminal-panel__message" role="status">{exitMessage}</p>}
      <div ref={hostRef} className="terminal-panel__host" aria-label="终端输出" />
    </section>
  );
}

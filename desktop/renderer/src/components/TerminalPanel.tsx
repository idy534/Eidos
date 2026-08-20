import { useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

interface TerminalPanelProps {
  sessionId: string;
  active: boolean;
}

export function TerminalPanel({ sessionId, active }: TerminalPanelProps) {
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

    let disposed = false;
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
    terminal.open(host);
    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;

    const fit = () => {
      if (disposed || !activeRef.current) return;
      try {
        fitAddon.fit();
      } catch {
        // The panel can be hidden while a tab changes. The next visible resize retries.
      }
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
      : new ResizeObserver(fit);
    resizeObserver?.observe(host);

    void window.eidosRuntime.createTerminal(sessionId).then((created) => {
      if (disposed) {
        void window.eidosRuntime.closeTerminal(created.terminalId).catch(() => undefined);
        return;
      }
      terminalIdRef.current = created.terminalId;
      fit();
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
      if (terminalId) void window.eidosRuntime.closeTerminal(terminalId).catch(() => undefined);
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, [sessionId]);

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

export const DEFAULT_TERMINAL_SHELL_KEY = "eidos.terminal.defaultShell";

export function getPreferredTerminalShell(): string {
  try {
    return window.localStorage.getItem(DEFAULT_TERMINAL_SHELL_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setPreferredTerminalShell(shell: string): void {
  try {
    const trimmed = shell.trim();
    if (trimmed) {
      window.localStorage.setItem(DEFAULT_TERMINAL_SHELL_KEY, trimmed);
    } else {
      window.localStorage.removeItem(DEFAULT_TERMINAL_SHELL_KEY);
    }
  } catch {
    // Ignore storage failures.
  }
}

interface TerminalDisposerEntry {
  sessionId: string;
  dispose: () => void;
}

const terminalDisposers = new Map<string, TerminalDisposerEntry>();

export function registerTerminalDisposer(tabKey: string, sessionId: string, dispose: () => void): void {
  terminalDisposers.set(tabKey, { sessionId, dispose });
}

export function unregisterTerminalDisposer(tabKey: string): void {
  terminalDisposers.delete(tabKey);
}

export function disposeTerminal(tabKey: string): void {
  const entry = terminalDisposers.get(tabKey);
  if (!entry) return;
  terminalDisposers.delete(tabKey);
  entry.dispose();
}

export function disposeSessionTerminals(sessionId: string): void {
  for (const [tabKey, entry] of [...terminalDisposers.entries()]) {
    if (entry.sessionId === sessionId) {
      terminalDisposers.delete(tabKey);
      entry.dispose();
    }
  }
}

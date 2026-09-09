import { useCallback, useEffect, useRef, useState } from "react";

export interface NavigationHistoryState {
  canGoBack: boolean;
  canGoForward: boolean;
  history: string[];
  currentIndex: number;
}

export interface NavigationHistoryActions {
  init: (sessionId: string) => void;
  navigateTo: (sessionId: string) => void;
  replaceCurrent: (sessionId: string) => void;
  pushSession: (sessionId: string) => void;
  goBack: () => string | undefined;
  goForward: () => string | undefined;
  removeSession: (sessionId: string) => void;
}

const MAX_HISTORY_LENGTH = 100;

export function useNavigationHistory(
  onNavigate?: (sessionId: string) => void,
): [NavigationHistoryState, NavigationHistoryActions] {
  const [history, setHistory] = useState<string[]>([]);
  const [currentIndex, setCurrentIndex] = useState<number>(-1);

  const historyRef = useRef<string[]>([]);
  const currentIndexRef = useRef<number>(-1);
  const onNavigateRef = useRef(onNavigate);
  onNavigateRef.current = onNavigate;

  const init = useCallback((sessionId: string) => {
    if (!sessionId || !sessionId.trim()) return;
    if (historyRef.current.length > 0) return;

    historyRef.current = [sessionId];
    currentIndexRef.current = 0;
    setHistory([sessionId]);
    setCurrentIndex(0);
  }, []);

  const navigateTo = useCallback((sessionId: string) => {
    if (!sessionId || !sessionId.trim()) return;

    const curHist = historyRef.current;
    const curIdx = currentIndexRef.current;

    // If history is empty, treat as init
    if (curHist.length === 0 || curIdx === -1) {
      historyRef.current = [sessionId];
      currentIndexRef.current = 0;
      setHistory([sessionId]);
      setCurrentIndex(0);
      return;
    }

    // Do not push consecutive duplicates
    if (curIdx >= 0 && curIdx < curHist.length && curHist[curIdx] === sessionId) {
      return;
    }

    // Truncate any forward history if we navigated back previously
    const baseHistory = curIdx >= 0 ? curHist.slice(0, curIdx + 1) : [];
    const nextHistory = [...baseHistory, sessionId];

    // Cap maximum history length
    if (nextHistory.length > MAX_HISTORY_LENGTH) {
      nextHistory.splice(0, nextHistory.length - MAX_HISTORY_LENGTH);
    }

    const nextIdx = nextHistory.length - 1;
    historyRef.current = nextHistory;
    currentIndexRef.current = nextIdx;
    setHistory(nextHistory);
    setCurrentIndex(nextIdx);
  }, []);

  const replaceCurrent = useCallback((sessionId: string) => {
    if (!sessionId || !sessionId.trim()) return;

    const curHist = historyRef.current;
    const curIdx = currentIndexRef.current;

    if (curIdx < 0 || curIdx >= curHist.length) {
      navigateTo(sessionId);
      return;
    }

    const nextHistory = [...curHist];
    nextHistory[curIdx] = sessionId;

    historyRef.current = nextHistory;
    setHistory(nextHistory);
  }, [navigateTo]);

  const goBack = useCallback((): string | undefined => {
    const curHist = historyRef.current;
    const curIdx = currentIndexRef.current;

    if (curIdx <= 0 || curHist.length === 0) {
      return undefined;
    }

    const targetIdx = curIdx - 1;
    const targetId = curHist[targetIdx];
    currentIndexRef.current = targetIdx;
    setCurrentIndex(targetIdx);

    if (targetId && onNavigateRef.current) {
      onNavigateRef.current(targetId);
    }
    return targetId;
  }, []);

  const goForward = useCallback((): string | undefined => {
    const curHist = historyRef.current;
    const curIdx = currentIndexRef.current;

    if (curIdx < 0 || curIdx >= curHist.length - 1) {
      return undefined;
    }

    const targetIdx = curIdx + 1;
    const targetId = curHist[targetIdx];
    currentIndexRef.current = targetIdx;
    setCurrentIndex(targetIdx);

    if (targetId && onNavigateRef.current) {
      onNavigateRef.current(targetId);
    }
    return targetId;
  }, []);

  const removeSession = useCallback((sessionId: string) => {
    if (!sessionId) return;
    const curHist = historyRef.current;
    const curIdx = currentIndexRef.current;

    if (!curHist.includes(sessionId)) return;

    // Filter out removed session
    const nextHistory: string[] = [];
    let nextIdx = curIdx;

    for (let i = 0; i < curHist.length; i++) {
      if (curHist[i] === sessionId) {
        if (i < curIdx) {
          nextIdx--;
        }
      } else {
        nextHistory.push(curHist[i]!);
      }
    }

    if (nextHistory.length === 0) {
      historyRef.current = [];
      currentIndexRef.current = -1;
      setHistory([]);
      setCurrentIndex(-1);
    } else {
      const clampedIdx = Math.max(0, Math.min(nextIdx, nextHistory.length - 1));
      historyRef.current = nextHistory;
      currentIndexRef.current = clampedIdx;
      setHistory(nextHistory);
      setCurrentIndex(clampedIdx);
    }
  }, []);

  // Keyboard shortcut integration: Cmd+[ (Back) and Cmd+] (Forward)
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // Don't navigate if user is in an input or textarea and presses Alt+Arrow
      const target = event.target as HTMLElement | null;
      const isEditable = Boolean(
        target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable),
      );

      const isCmdOrCtrl = event.metaKey || event.ctrlKey;

      if ((isCmdOrCtrl && event.key === "[") || (!isEditable && event.altKey && event.key === "ArrowLeft")) {
        event.preventDefault();
        goBack();
      } else if ((isCmdOrCtrl && event.key === "]") || (!isEditable && event.altKey && event.key === "ArrowRight")) {
        event.preventDefault();
        goForward();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [goBack, goForward]);

  const canGoBack = currentIndex > 0;
  const canGoForward = currentIndex >= 0 && currentIndex < history.length - 1;

  return [
    {
      canGoBack,
      canGoForward,
      history,
      currentIndex,
    },
    {
      init,
      navigateTo,
      replaceCurrent,
      pushSession: navigateTo,
      goBack,
      goForward,
      removeSession,
    },
  ];
}

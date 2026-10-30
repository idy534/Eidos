import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export type ToastType = "error" | "warning" | "info" | "success";

export interface ToastItem {
  id: string;
  message: string;
  type: ToastType;
  duration?: number;
}

export interface ToastContainerProps {
  toasts: ToastItem[];
  onDismiss?: ((id: string) => void) | undefined;
  portal?: boolean;
}

interface SingleToastProps {
  toast: ToastItem;
  onDismiss?: ((id: string) => void) | undefined;
}

function SingleToast({ toast, onDismiss = () => {} }: SingleToastProps) {
  const [dismissed, setDismissed] = useState(false);
  const duration = toast.duration ?? (toast.type === "error" || toast.type === "warning" ? 5000 : 3500);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const remainingRef = useRef<number>(duration);
  const startTimeRef = useRef<number>(Date.now());
  const onDismissRef = useRef(onDismiss);
  onDismissRef.current = onDismiss;

  const handleDismiss = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setDismissed(true);
    onDismissRef.current?.(toast.id);
  }, [toast.id]);

  useEffect(() => {
    setDismissed(false);
    remainingRef.current = duration;
    startTimeRef.current = Date.now();
    timerRef.current = setTimeout(() => {
      handleDismiss();
    }, remainingRef.current);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [toast.id, toast.message, duration, handleDismiss]);

  const handleMouseEnter = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
      const elapsed = Date.now() - startTimeRef.current;
      remainingRef.current = Math.max(1000, remainingRef.current - elapsed);
    }
  };

  const handleMouseLeave = () => {
    if (!timerRef.current && !dismissed) {
      startTimeRef.current = Date.now();
      timerRef.current = setTimeout(() => {
        handleDismiss();
      }, remainingRef.current);
    }
  };

  if (dismissed) {
    return null;
  }

  const isAlert = toast.type === "error" || toast.type === "warning";

  return (
    <div
      className={`app-toast app-toast--${toast.type}`}
      role={isAlert ? "alert" : "status"}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <span className="app-toast-icon" aria-hidden="true">
        {toast.type === "error" && (
          <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            <path d="M8 1a7 7 0 1 0 7 7A7.008 7.008 0 0 0 8 1zm3.2 9.5l-.7.7L8 8.7l-2.5 2.5-.7-.7L7.3 8 4.8 5.5l.7-.7L8 7.3l2.5-2.5.7.7L8.7 8z" />
          </svg>
        )}
        {toast.type === "warning" && (
          <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            <path d="M7.005 2.1a1.14 1.14 0 0 1 1.99 0l6.71 11.62A1.14 1.14 0 0 1 14.71 15H1.29a1.14 1.14 0 0 1-.995-1.28L7.005 2.1zM8 5a.75.75 0 0 0-.75.75v4.5a.75.75 0 0 0 1.5 0v-4.5A.75.75 0 0 0 8 5zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2z" />
          </svg>
        )}
        {toast.type === "success" && (
          <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            <path d="M8 1a7 7 0 1 0 7 7A7.008 7.008 0 0 0 8 1zm3.3 5.3l-4 4a.7.7 0 0 1-1 0l-2-2a.7.7 0 1 1 1-1l1.5 1.5 3.5-3.5a.7.7 0 1 1 1 1z" />
          </svg>
        )}
        {toast.type === "info" && (
          <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            <path d="M8 1a7 7 0 1 0 7 7A7.008 7.008 0 0 0 8 1zm0 3a1 1 0 1 1-1 1 1 1 0 0 1 1-1zm1 8H7v-4h2z" />
          </svg>
        )}
      </span>
      <span className="app-toast-message">{toast.message}</span>
      <button
        type="button"
        className="app-toast-close"
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          handleDismiss();
        }}
        aria-label="关闭提示"
      >
        ✕
      </button>
    </div>
  );
}

function getGlobalToastRoot(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  let root = document.getElementById("eidos-global-toast-root");
  if (!root) {
    root = document.createElement("div");
    root.id = "eidos-global-toast-root";
    root.className = "app-toast-container";
    root.setAttribute("aria-live", "polite");
    document.body.appendChild(root);
  }
  return root;
}

export function ToastContainer({ toasts, onDismiss, portal = true }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  const content = toasts.map((toast) => (
    <SingleToast key={toast.id} toast={toast} onDismiss={onDismiss} />
  ));

  if (!portal) {
    return (
      <div className="app-toast-container" aria-live="polite">
        {content}
      </div>
    );
  }

  const root = getGlobalToastRoot();
  if (!root) return null;

  return createPortal(<>{content}</>, root);
}

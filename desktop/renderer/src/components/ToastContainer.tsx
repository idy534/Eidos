import { useEffect, useRef } from "react";

export type ToastType = "error" | "warning" | "info" | "success";

export interface ToastItem {
  id: string;
  message: string;
  type: ToastType;
  duration?: number;
}

interface ToastContainerProps {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}

interface SingleToastProps {
  toast: ToastItem;
  onDismiss: (id: string) => void;
}

function SingleToast({ toast, onDismiss }: SingleToastProps) {
  const duration = toast.duration ?? (toast.type === "error" || toast.type === "warning" ? 5000 : 3500);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const remainingRef = useRef<number>(duration);
  const startTimeRef = useRef<number>(Date.now());

  useEffect(() => {
    startTimeRef.current = Date.now();
    timerRef.current = setTimeout(() => {
      onDismiss(toast.id);
    }, remainingRef.current);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, [toast.id, onDismiss]);

  const handleMouseEnter = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
      const elapsed = Date.now() - startTimeRef.current;
      remainingRef.current = Math.max(1000, remainingRef.current - elapsed);
    }
  };

  const handleMouseLeave = () => {
    if (!timerRef.current) {
      startTimeRef.current = Date.now();
      timerRef.current = setTimeout(() => {
        onDismiss(toast.id);
      }, remainingRef.current);
    }
  };

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
        onClick={() => onDismiss(toast.id)}
        aria-label="关闭提示"
      >
        ✕
      </button>
    </div>
  );
}

export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="app-toast-container" aria-live="polite">
      {toasts.map((toast) => (
        <SingleToast key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Button } from "./Button.js";
import { ChromeIcon } from "./ChromeIcon.js";

/** A Chrome icon in the Session header while the browser is granted; clicking it shows the grant and a revoke button. */
export function BrowserGrantIndicator({ sessionId, onShowToast }: {
  sessionId: string;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}) {
  const [granted, setGranted] = useState(false);
  const [userChrome, setUserChrome] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverId = useId();
  const current = useRef(sessionId);
  current.current = sessionId;

  const refresh = useCallback(() => {
    const requested = sessionId;
    window.eidosRuntime.readBrowserAgentStatus({ sessionId: requested }).then((status) => {
      if (current.current === requested) {
        setGranted(status.granted === true);
        // The Playwright Extension also drives the user's own Chrome.
        const effective = status.effectiveTarget ?? status.target;
        setUserChrome(effective === "user_chrome" || effective === "chrome_extension");
      }
    }).catch(() => {
      if (current.current === requested) setGranted(false);
    });
  }, [sessionId]);

  useEffect(() => {
    setGranted(false);
    refresh();
    return window.eidosRuntime.onNotification((notification) => {
      if (notification.method === "browser/grantChanged" && notification.params.sessionId === sessionId) {
        setGranted(notification.params.active);
      } else if (notification.method === "approval/resolved" && notification.params.sessionId === sessionId) {
        refresh();
      }
    });
  }, [refresh, sessionId]);

  useEffect(() => {
    if (!open) return;
    function pointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function keyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("pointerdown", pointerDown);
    window.addEventListener("keydown", keyDown);
    return () => {
      document.removeEventListener("pointerdown", pointerDown);
      window.removeEventListener("keydown", keyDown);
    };
  }, [open]);

  useEffect(() => { if (!granted) setOpen(false); }, [granted]);

  async function revoke() {
    setRevoking(true);
    try {
      await window.eidosRuntime.revokeBrowserAgentGrant({ sessionId });
      setGranted(false);
      onShowToast("已撤销本会话的浏览器授权。之后操作网页前会重新询问。", "success");
    } catch {
      onShowToast("撤销浏览器授权失败，请稍后重试。", "error");
    } finally {
      setRevoking(false);
    }
  }

  if (!granted) return null;
  const label = userChrome ? "Chrome 已授权" : "浏览器已授权";
  return (
    <div className="browser-grant-indicator" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="browser-grant-trigger"
        title={label}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <ChromeIcon className="browser-grant-icon" />
      </button>
      {open && (
        <div className="browser-grant-popover" id={popoverId} role="dialog" aria-label="浏览器授权">
          <span className="browser-grant-label">{label}</span>
          <Button variant="ghost" size="small" className="browser-grant-revoke" loading={revoking} disabled={revoking}
            onClick={() => void revoke()} aria-label="撤销本会话的浏览器授权">
            撤销
          </Button>
        </div>
      )}
    </div>
  );
}

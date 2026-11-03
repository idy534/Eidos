import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./Button.js";
import { GlobeIcon } from "./MenuIcons.js";

/** Shows that this Session may operate the shared Eidos Chrome, with a one-click revoke. */
export function BrowserGrantIndicator({ sessionId, onShowToast }: {
  sessionId: string;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}) {
  const [granted, setGranted] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const current = useRef(sessionId);
  current.current = sessionId;

  const refresh = useCallback(() => {
    const requested = sessionId;
    window.eidosRuntime.readBrowserAgentStatus({ sessionId: requested }).then((status) => {
      if (current.current === requested) setGranted(status.granted === true);
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
  return (
    <div className="browser-grant-indicator" role="status">
      <GlobeIcon className="browser-grant-icon" />
      <span className="browser-grant-label">浏览器已授权</span>
      <Button variant="ghost" size="small" loading={revoking} disabled={revoking} onClick={() => void revoke()}
        aria-label="撤销本会话的浏览器授权">
        撤销
      </Button>
    </div>
  );
}

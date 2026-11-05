import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserGrantIndicator } from "./BrowserGrantIndicator.js";
import type { RuntimeNotification } from "../../../shared/domain-contracts.js";

const status = {
  chromeInstalled: true, chromeVersion: "156.0", running: false, pluginInstalled: true, pluginEnabled: true,
  resource: "ready" as const, granted: true,
};
let listeners: ((notification: RuntimeNotification) => void)[] = [];

function api(value: object) {
  Object.defineProperty(window, "eidosRuntime", {
    configurable: true,
    value: {
      onNotification: (callback: (notification: RuntimeNotification) => void) => {
        listeners.push(callback);
        return () => { listeners = listeners.filter((listener) => listener !== callback); };
      },
      ...value,
    },
  });
}

beforeEach(() => { listeners = []; });

describe("Browser grant indicator", () => {
  it("shows the Session grant, follows notifications and revokes it", async () => {
    const revokeBrowserAgentGrant = vi.fn().mockResolvedValue({ revoked: true });
    const onShowToast = vi.fn();
    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue(status), revokeBrowserAgentGrant });
    render(<BrowserGrantIndicator sessionId="s1" onShowToast={onShowToast} />);
    expect(await screen.findByRole("status")).toHaveTextContent("浏览器已授权");

    act(() => { for (const listener of listeners) listener({ method: "browser/grantChanged", params: { sessionId: "other", active: false } }); });
    expect(screen.getByRole("status")).toBeInTheDocument();
    act(() => { for (const listener of listeners) listener({ method: "browser/grantChanged", params: { sessionId: "s1", active: false } }); });
    expect(screen.queryByRole("status")).toBeNull();
    act(() => { for (const listener of listeners) listener({ method: "browser/grantChanged", params: { sessionId: "s1", active: true } }); });

    await userEvent.click(screen.getByRole("button", { name: "撤销本会话的浏览器授权" }));
    expect(revokeBrowserAgentGrant).toHaveBeenCalledWith({ sessionId: "s1" });
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(onShowToast).toHaveBeenCalledWith(expect.stringContaining("已撤销"), "success");
  });

  it("names the user's own Chrome when the extension is in effect, and the Eidos browser after fallback", async () => {
    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, target: "chrome_extension", effectiveTarget: "chrome_extension" }) });
    const { unmount } = render(<BrowserGrantIndicator sessionId="s1" onShowToast={vi.fn()} />);
    expect(await screen.findByRole("status")).toHaveTextContent("我的 Chrome 已授权");
    unmount();
    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, target: "chrome_extension", effectiveTarget: "eidos" }) });
    render(<BrowserGrantIndicator sessionId="s1" onShowToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/^浏览器已授权/));
  });

  it("stays hidden without a grant", async () => {
    const readBrowserAgentStatus = vi.fn().mockResolvedValue({ ...status, granted: false });
    api({ readBrowserAgentStatus });
    render(<BrowserGrantIndicator sessionId="s1" onShowToast={vi.fn()} />);
    await waitFor(() => expect(readBrowserAgentStatus).toHaveBeenCalledWith({ sessionId: "s1" }));
    expect(screen.queryByRole("status")).toBeNull();
  });
});

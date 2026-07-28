import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act, waitFor } from "@testing-library/react";
import { App } from "../App.js";
import type { EidosRuntimeAPI, RuntimeStatus } from "../contracts.js";

const mockReadyStatus: RuntimeStatus = {
  state: "ready",
  protocolVersion: 1,
  runtimeVersion: "0.3.0",
  storageHealth: { state: "ready" },
};

const mockHealthOnlyStatus: RuntimeStatus = {
  state: "ready",
  protocolVersion: 1,
  runtimeVersion: "0.3.0",
  storageHealth: { state: "health_only", code: "READ_ONLY_STORAGE" },
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("App & Runtime Lifecycle behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupMockRuntime(overrides: Partial<EidosRuntimeAPI> = {}) {
    let statusListener: ((status: RuntimeStatus) => void) | undefined;
    const unsubSpy = vi.fn();
    const onStatusSpy = vi.fn().mockImplementation((cb) => {
      statusListener = cb;
      return unsubSpy;
    });
    const getStatusSpy = vi.fn().mockResolvedValue(mockReadyStatus);

    const api: Partial<EidosRuntimeAPI> = {
      getStatus: getStatusSpy,
      getHealth: vi.fn().mockResolvedValue({ state: "ready" }),
      onStatus: onStatusSpy,
      onShortcut: vi.fn().mockReturnValue(() => {}),
      onNotification: vi.fn().mockReturnValue(() => {}),
      onApprovalRequest: vi.fn().mockReturnValue(() => {}),
      listSessions: vi.fn().mockResolvedValue({ items: [] }),
      getModelStatus: vi.fn().mockResolvedValue({ provider: "deepseek", model: "deepseek-v4-flash", configured: true }),
      listModels: vi.fn().mockResolvedValue({ defaultModelId: "deepseek-v4-flash", models: [{ id: "deepseek-v4-flash", provider: "deepseek", displayName: "Flash", configured: true, selectable: true }] }),
      listPendingApprovals: vi.fn().mockResolvedValue([]),
      readExtensions: vi.fn().mockResolvedValue({ plugins: [], skills: [], servers: [], throughEventId: 0 }),
      readExtensionEvents: vi.fn().mockResolvedValue({ items: [], throughEventId: 0 }),
      ...overrides,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    return {
      api,
      getStatusSpy,
      onStatusSpy,
      unsubSpy,
      emitStatus: (s: RuntimeStatus) => {
        if (statusListener) statusListener(s);
      },
    };
  }

  it("getStatus() called once, onStatus() subscribed once, unsubscribed on unmount", async () => {
    const { getStatusSpy, onStatusSpy, unsubSpy } = setupMockRuntime();

    const { unmount } = render(<App />);

    await waitFor(() => {
      expect(getStatusSpy).toHaveBeenCalledTimes(1);
      expect(onStatusSpy).toHaveBeenCalledTimes(1);
    });

    expect(unsubSpy).not.toHaveBeenCalled();

    unmount();
    expect(unsubSpy).toHaveBeenCalledTimes(1);
  });

  it("starting renders RuntimeGate with status role", () => {
    setupMockRuntime({
      getStatus: vi.fn().mockReturnValue(Promise.withResolvers<RuntimeStatus>().promise),
    });

    const { container } = render(<App />);

    const gate = container.querySelector(".runtime-gate");
    expect(gate).toBeInTheDocument();
    expect(gate).toHaveAttribute("role", "status");
    expect(gate).toHaveTextContent("正在启动 Engine");
  });

  it("error state renders alert role in RuntimeGate", async () => {
    setupMockRuntime({
      getStatus: vi.fn().mockResolvedValue({ state: "error", message: "Failed to bind runtime port" }),
    });

    const { container } = render(<App />);

    await waitFor(() => {
      const gate = container.querySelector(".runtime-gate");
      expect(gate).toBeInTheDocument();
      expect(gate).toHaveAttribute("role", "alert");
      expect(gate).toHaveTextContent("启动失败");
    });
  });

  it("ready state renders AppShell workbench", async () => {
    setupMockRuntime();
    const { container } = render(<App />);

    await waitFor(() => {
      const workbench = container.querySelector(".workspace");
      expect(workbench).toBeInTheDocument();
    });
  });

  it("health-only state remains in ready application and presents read-only warning", async () => {
    setupMockRuntime({
      getStatus: vi.fn().mockResolvedValue(mockHealthOnlyStatus),
    });

    const { container } = render(<App />);

    await waitFor(() => {
      const workbench = container.querySelector(".workspace");
      expect(workbench).toBeInTheDocument();
      expect(container).toHaveTextContent("存储处于只读健康模式");
    });
  });
});

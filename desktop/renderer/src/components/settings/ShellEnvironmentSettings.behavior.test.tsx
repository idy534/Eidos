import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ShellEnvironmentSaveRequest } from "../../../../shared/shell-environment.js";
import { RuntimeSettings } from "./RuntimeSettings.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const initial = { revision: 0, policy: { inherit: "all" } };
const runtime = { state: "ready" as const, runtimeVersion: "0.5.4", protocolVersion: 1 as const,
  runShell: true, modelConfigured: false, storageHealth: { state: "ready" as const } };

describe("Runtime sandbox environment settings", () => {
  let read: ReturnType<typeof vi.fn>;
  let save: ReturnType<typeof vi.fn>;
  let refresh: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    read = vi.fn().mockResolvedValue(structuredClone(initial));
    save = vi.fn().mockImplementation(async (value: ShellEnvironmentSaveRequest) => ({ revision: 1, policy: value.policy }));
    refresh = vi.fn().mockResolvedValue(structuredClone(initial));
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: {
      readShellEnvironment: read, saveShellEnvironment: save, refreshShellEnvironment: refresh,
    } });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("renders inside Runtime with only inheritance and reread controls", async () => {
    const user = userEvent.setup();
    render(<RuntimeSettings runtime={runtime} />);
    expect(screen.getByRole("heading", { name: "Runtime 引擎环境" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "沙盒环境设置" })).toBeInTheDocument();
    expect(await screen.findByLabelText("环境继承方式")).toHaveValue("all");
    expect(screen.queryByRole("button", { name: "保存设置" })).not.toBeInTheDocument();
    expect(screen.queryByText("变量过滤")).not.toBeInTheDocument();
    expect(screen.queryByText("变量覆盖")).not.toBeInTheDocument();
    await user.hover(screen.getByRole("button", { name: "继承方式说明" }));
    expect(await screen.findByRole("tooltip")).toHaveTextContent("修改后自动保存");
  });

  it("automatically saves an inheritance change with the current revision", async () => {
    const user = userEvent.setup();
    const toast = vi.fn();
    render(<RuntimeSettings runtime={runtime} onShowToast={toast} />);
    await waitFor(() => expect(screen.getByLabelText("环境继承方式")).toBeEnabled());
    await user.selectOptions(screen.getByLabelText("环境继承方式"), "none");
    await waitFor(() => expect(save).toHaveBeenCalledWith({ expectedRevision: 0, policy: { inherit: "none" } }));
    expect(toast).toHaveBeenCalledWith("环境继承方式已更新", "success");
    expect(screen.getByLabelText("环境继承方式")).toHaveValue("none");
  });

  it("restores the previous selection if saving fails", async () => {
    const user = userEvent.setup(); save.mockRejectedValue(new Error("fixture"));
    render(<RuntimeSettings runtime={runtime} />);
    await waitFor(() => expect(screen.getByLabelText("环境继承方式")).toBeEnabled());
    await user.selectOptions(screen.getByLabelText("环境继承方式"), "core");
    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
    expect(screen.getByLabelText("环境继承方式")).toHaveValue("all");
  });

  it("rereads the environment without saving a configuration", async () => {
    const user = userEvent.setup();
    render(<RuntimeSettings runtime={runtime} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "重新读取" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(save).not.toHaveBeenCalled();
  });

  it("recovers a failed initial read through reread", async () => {
    const user = userEvent.setup(); read.mockRejectedValueOnce(new Error("fixture"));
    render(<RuntimeSettings runtime={runtime} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取");
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(screen.getByLabelText("环境继承方式")).toBeEnabled());
    expect(screen.getByLabelText("环境继承方式")).toHaveValue("all");
  });
});

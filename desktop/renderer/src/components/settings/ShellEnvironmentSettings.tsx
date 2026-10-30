import React, { useEffect, useRef, useState } from "react";
import type { ShellEnvironmentPolicy, ShellEnvironmentResponse } from "../../../../shared/shell-environment.js";
import { Button } from "../Button.js";
import { Tooltip } from "../Tooltip.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { ToastContainer } from "../ToastContainer.js";

function Help({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Tooltip content={children} placement="top" className="shell-environment-tooltip">
      <button type="button" className="shell-environment-help" aria-label={`${label}说明`}>?</button>
    </Tooltip>
  );
}

function RefreshIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9l1.6 1.4" />
      <path d="M13.5 2.5v3h-3" />
    </svg>
  );
}

export function ShellEnvironmentSettings({ onShowToast }: {
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const [settings, setSettings] = useState<ShellEnvironmentResponse>();
  const [working, setWorking] = useState<"save" | "refresh" | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const busy = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    let active = true;
    void window.eidosRuntime.readShellEnvironment().then((value) => {
      if (active) setSettings(value);
    }).catch(() => {
      if (active) setError("沙盒环境设置无法读取，请点击重新读取。");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; mounted.current = false; };
  }, []);

  async function changeInheritance(inherit: NonNullable<ShellEnvironmentPolicy["inherit"]>) {
    if (!settings || busy.current || settings.policy.inherit === inherit) return;
    const previous = settings;
    busy.current = true; setWorking("save"); setError("");
    setSettings({ ...previous, policy: { inherit } });
    try {
      const saved = await window.eidosRuntime.saveShellEnvironment({ expectedRevision: previous.revision, policy: { inherit } });
      if (mounted.current) { setSettings(saved); onShowToast?.("环境继承方式已更新", "success"); }
    } catch {
      if (mounted.current) {
        setSettings(previous);
        setError("继承方式保存失败，请重新读取后再试。");
        onShowToast?.("继承方式保存失败，请重新读取后再试。", "error");
      }
    } finally {
      busy.current = false;
      if (mounted.current) setWorking(null);
    }
  }

  async function refresh() {
    if (busy.current) return;
    busy.current = true; setWorking("refresh"); setError("");
    try {
      const value = await window.eidosRuntime.refreshShellEnvironment();
      if (mounted.current) { setSettings(value); onShowToast?.("后续命令将重新读取宿主机环境", "info"); }
    } catch {
      if (mounted.current) {
        setError("环境刷新失败，请重试。");
        onShowToast?.("环境刷新失败，请重试。", "error");
      }
    } finally {
      busy.current = false;
      if (mounted.current) setWorking(null);
    }
  }

  return (
    <SettingSection title="沙盒环境设置" className="shell-environment-settings">
      {error && !onShowToast && (
        <ToastContainer
          toasts={[{ id: `shell-env-error-${error}`, message: error, type: "error", duration: 4000 }]}
          onDismiss={() => setError("")}
        />
      )}
      <SettingRow
        title={
          <span className="shell-environment-label">
            继承方式
            <Help label="继承方式">系统默认继承宿主机环境，包括 API Key、Token 等变量。仅基础环境保留路径、用户和语言配置；不继承不透传宿主机变量。修改后自动保存，并作用于后续命令。文件和网络权限保持现状。</Help>
          </span>
        }
        description="沙箱执行命令时继承宿主机环境变量的策略，修改后自动生效于后续命令"
        action={
          <select
            aria-label="环境继承方式"
            className="shell-environment-select"
            value={settings?.policy.inherit ?? "all"}
            disabled={loading || !settings || working !== null}
            onChange={(event) => void changeInheritance(event.target.value as NonNullable<ShellEnvironmentPolicy["inherit"]>)}
          >
            <option value="all">继承全部（默认）</option>
            <option value="core">仅基础环境</option>
            <option value="none">不继承</option>
          </select>
        }
      />
      <SettingRow
        title={
          <span className="shell-environment-label">
            重新读取环境
            <Help label="重新读取环境">系统会清除环境快照，在后续命令启动时重新读取登录 Shell 环境。正在运行的命令不受影响。系统不会读取另一个终端里临时 export 的变量。</Help>
          </span>
        }
        description="清除当前宿主机环境变量快照，下次执行命令时重新从登录 Shell 加载最新变量"
        action={
          <Button
            size="small"
            variant="secondary"
            icon={<RefreshIcon className="shell-environment-btn-icon" />}
            disabled={loading || working !== null}
            loading={working === "refresh"}
            onClick={() => void refresh()}
          >
            重新读取
          </Button>
        }
      />
    </SettingSection>
  );
}

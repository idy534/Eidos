import { ShellEnvironmentSettings } from "./ShellEnvironmentSettings.js";
import { DataSettings } from "./DataSettings.js";
import type { RuntimeStatus } from "../../contracts";
import { deriveRuntimePresentation } from "../../session-state";
import { SettingSection } from "../SettingSection.js";
import { SettingRow } from "../SettingRow.js";
import { StatusBadge } from "../StatusBadge.js";
import { AlertBanner } from "../AlertBanner.js";

interface RuntimeSettingsProps {
  runtime: RuntimeStatus;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}

export function RuntimeSettings({ runtime, onShowToast }: RuntimeSettingsProps) {
  const presentation = deriveRuntimePresentation(runtime);

  if (runtime.state !== "ready") {
    return (
      <div className="settings-panel">
        <div className="settings-panel-header">
          <h1>Runtime 状态</h1>
          <p className="settings-panel-subtitle">系统引擎当前未准备就绪。</p>
        </div>
        <AlertBanner
          tone={presentation.tone === "danger" ? "error" : presentation.tone === "warning" ? "warning" : "info"}
          title={<StatusBadge tone={presentation.tone}>{presentation.label}</StatusBadge>}
        >
          {presentation.description ?? "引擎初始化中…"}
        </AlertBanner>
      </div>
    );
  }

  const isStorageReady = runtime.storageHealth.state === "ready";

  return (
    <div className="settings-panel">
      <div className="settings-panel-header">
        <h1>Runtime 引擎环境</h1>
        <p className="settings-panel-subtitle">
          Eidos 本地沙箱与 Runtime 服务的运行指标与安全自检状态。
        </p>
      </div>

      <SettingSection title="系统状态总览">
        <SettingRow
          title="Runtime 整体运行状态"
          description="本地通信协议、存储数据库与执行沙箱握手状态"
          action={
            <StatusBadge tone={presentation.tone}>{presentation.label}</StatusBadge>
          }
        />
        <SettingRow
          title="Runtime 版本"
          description="当前引擎的二进制软件版本"
          action={
            <code className="runtime-version-tag">{runtime.runtimeVersion}</code>
          }
        />
        <SettingRow
          title="Protocol 协议版本"
          description="IPC 通信与架构契约版本"
          action={
            <code className="runtime-version-tag">v{runtime.protocolVersion}</code>
          }
        />
        <SettingRow
          title="Seatbelt Shell 沙箱"
          description="系统命令安全执行隔离机制"
          action={
            runtime.runShell ? (
              <StatusBadge tone="success">验证通过</StatusBadge>
            ) : (
              <StatusBadge tone="warning">自检未通过</StatusBadge>
            )
          }
        />
        <SettingRow
          title="状态存储健康度"
          description="SQLite 任务数据库与 Read/Write 健康状态"
          action={
            isStorageReady ? (
              <StatusBadge tone="success">Ready</StatusBadge>
            ) : (
              <StatusBadge tone="warning">
                Health Only ({runtime.storageHealth.code ?? "UNKNOWN"})
              </StatusBadge>
            )
          }
        />
      </SettingSection>
      {isStorageReady && <ShellEnvironmentSettings onShowToast={onShowToast} />}
      {isStorageReady && <DataSettings onShowToast={onShowToast} />}
    </div>
  );
}

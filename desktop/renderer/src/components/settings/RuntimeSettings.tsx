import { useState } from "react";
import { ShellEnvironmentSettings } from "./ShellEnvironmentSettings.js";
import { BackgroundGuardrailSettings } from "./BackgroundGuardrailSettings.js";
import { DataSettings } from "./DataSettings.js";
import { ManagedResourceSettings } from "./ManagedResourceSettings.js";
import type { RuntimeStatus } from "../../contracts";
import { deriveRuntimePresentation } from "../../session-state";
import { isWindowsPlatform } from "../../platform.js";
import { SettingSection } from "../SettingSection.js";
import { SettingRow } from "../SettingRow.js";
import { StatusBadge } from "../StatusBadge.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";

interface RuntimeSettingsProps {
  runtime: RuntimeStatus;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}

export function RuntimeSettings({ runtime, onShowToast }: RuntimeSettingsProps) {
  const presentation = deriveRuntimePresentation(runtime);
  const [restarting, setRestarting] = useState(false);
  const [exportingDiagnostics, setExportingDiagnostics] = useState(false);

  async function handleRestartRuntime(): Promise<void> {
    setRestarting(true);
    try {
      const next = await window.eidosRuntime.restartRuntime();
      if (next.state === "ready") {
        onShowToast?.("Runtime 已重启并就绪", "success");
      } else {
        onShowToast?.("Runtime 重启未就绪，请导出诊断包排查。", "error");
      }
    } catch {
      onShowToast?.("Runtime 重启失败，请稍后重试。", "error");
    } finally {
      setRestarting(false);
    }
  }

  async function handleExportDiagnostics(): Promise<void> {
    setExportingDiagnostics(true);
    try {
      const saved = await window.eidosRuntime.exportDiagnostics();
      if (saved) {
        onShowToast?.("已导出脱敏诊断包", "success");
      }
    } catch {
      onShowToast?.("诊断包导出失败，请重试。", "error");
    } finally {
      setExportingDiagnostics(false);
    }
  }

  const maintenanceSection = (
    <SettingSection title="运维与诊断">
      <SettingRow
        title="重启 Runtime"
        description="重新启动本地 Python Runtime 进程并刷新状态连接"
        action={
          <Button variant="secondary" disabled={restarting} onClick={() => void handleRestartRuntime()}>
            {restarting ? "正在重启…" : "重启 Runtime"}
          </Button>
        }
      />
      <SettingRow
        title="导出脱敏诊断包"
        description="将已脱敏的桌面/Runtime 日志、运行摘要与崩溃元数据清单保存为 ZIP"
        action={
          <Button variant="secondary" disabled={exportingDiagnostics} onClick={() => void handleExportDiagnostics()}>
            {exportingDiagnostics ? "正在导出…" : "导出诊断包"}
          </Button>
        }
      />
    </SettingSection>
  );

  if (runtime.state !== "ready") {
    return (
      <div className="settings-panel">
        <div className="settings-panel-header">
          <h1>运行环境状态</h1>
          <p className="settings-panel-subtitle">系统引擎当前未准备就绪。</p>
        </div>
        <AlertBanner
          tone={presentation.tone === "danger" ? "error" : presentation.tone === "warning" ? "warning" : "info"}
          title={<StatusBadge tone={presentation.tone}>{presentation.label}</StatusBadge>}
        >
          {presentation.description ?? "引擎初始化中…"}
        </AlertBanner>
        {maintenanceSection}
      </div>
    );
  }

  const isStorageReady = runtime.storageHealth.state === "ready";

  return (
    <div className="settings-panel">
      <div className="settings-panel-header">
        <h1>运行环境</h1>
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
          title={isWindowsPlatform() ? "命令执行沙箱" : "Seatbelt Shell 沙箱"}
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
              <StatusBadge tone="success">就绪</StatusBadge>
            ) : (
              <StatusBadge tone="warning">
                仅健康检查 ({runtime.storageHealth.code ?? "UNKNOWN"})
              </StatusBadge>
            )
          }
        />
      </SettingSection>
      <ManagedResourceSettings onShowToast={onShowToast} />
      {isStorageReady && <ShellEnvironmentSettings onShowToast={onShowToast} />}
      {isStorageReady && <BackgroundGuardrailSettings onShowToast={onShowToast} />}
      {isStorageReady && <DataSettings onShowToast={onShowToast} />}
      {maintenanceSection}
    </div>
  );
}

import { useState } from "react";
import { Button } from "../Button.js";
import { ToastContainer } from "../ToastContainer.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { ConfirmDialog } from "./ConfirmDialog.js";

export function DataSettings({ onShowToast }: {
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const [busy, setBusy] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState("");
  async function backup() {
    setBusy(true); setError("");
    try {
      await window.eidosRuntime.backupData();
      onShowToast?.("备份已完成", "success");
    } catch {
      setError("备份未完成，请重试。");
      onShowToast?.("备份未完成，请重试。", "error");
    } finally {
      setBusy(false);
    }
  }
  async function confirmRestore() {
    setRestoring(true); setError("");
    try {
      const status = await window.eidosRuntime.restoreData();
      setRestoreOpen(false);
      if (status.state === "ready" && status.storageHealth.state === "ready") {
        onShowToast?.("备份恢复已完成", "success");
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "备份恢复失败，请重试。";
      setRestoreOpen(false);
      setError(message);
      onShowToast?.(message, "error");
    } finally {
      setRestoring(false);
    }
  }
  return (
    <SettingSection title="本地数据">
      {error && !onShowToast && (
        <ToastContainer
          toasts={[{ id: `data-settings-error-${error}`, message: error, type: "error", duration: 4000 }]}
          onDismiss={() => setError("")}
        />
      )}
      <SettingRow title="完整备份" description="将聊天、记忆和执行记录保存为未加密 ZIP。备份不包含项目文件和模型凭据，请妥善保管。"
        action={(
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Button variant="secondary" disabled={busy || restoring} onClick={() => void backup()}>{busy ? "正在备份…" : "备份本地数据"}</Button>
            <Button variant="secondary" disabled={busy || restoring} onClick={() => setRestoreOpen(true)}>{restoring ? "正在恢复…" : "恢复备份"}</Button>
          </div>
        )} />
      <ConfirmDialog
        open={restoreOpen}
        title="确认从备份恢复？"
        description="恢复前会在临时目录先解包并校验备份；随后停止 Runtime 并替换本地数据目录，若校验或重启失败将自动回滚原数据目录。"
        confirmLabel="选择备份并恢复"
        isDestructive
        busy={restoring}
        onCancel={() => setRestoreOpen(false)}
        onConfirm={() => void confirmRestore()}
      />
    </SettingSection>
  );
}

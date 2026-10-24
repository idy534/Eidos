import { useState } from "react";
import { Button } from "../Button.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";

export function DataSettings({ onShowToast }: {
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function backup() {
    setBusy(true); setError("");
    try { await window.eidosRuntime.backupData(); }
    catch { setError("备份未完成，请重试。"); onShowToast?.("备份未完成", "error"); }
    finally { setBusy(false); }
  }
  return <SettingSection title="本地数据">
    <SettingRow title="完整备份" description="将聊天、记忆和执行记录保存为未加密 ZIP。备份不包含项目文件和模型凭据，请妥善保管。"
      action={<Button variant="secondary" disabled={busy} onClick={() => void backup()}>{busy ? "正在备份…" : "备份本地数据"}</Button>} />
    {error && <p role="alert">{error}</p>}
  </SettingSection>;
}

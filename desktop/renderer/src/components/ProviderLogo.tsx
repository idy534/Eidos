import deepseekLogo from "../assets/providers/deepseek.svg";
import kimiLogo from "../assets/providers/kimi.svg";
import minimaxLogo from "../assets/providers/minimax.svg";
import volcengineLogo from "../assets/providers/volcengine.svg";
import qoderLogo from "../assets/providers/qoder.svg";

const providerLogos: Record<string, string> = {
  deepseek: deepseekLogo,
  minimax: minimaxLogo,
  kimi: kimiLogo,
  volcengine: volcengineLogo,
  qoder: qoderLogo,
};

const providerNames: Record<string, string> = {
  custom: "自定义",
  qoder: "Qoder",
  deepseek: "深度求索",
  minimax: "MiniMax",
  kimi: "月之暗面",
  volcengine: "火山引擎",
  openai: "OpenAI",
  anthropic: "Anthropic",
  gemini: "Google Gemini",
};

export function getProviderName(provider: string): string {
  return providerNames[provider] ?? provider;
}

export function ProviderLogo({ provider, className }: { provider: string; className?: string }) {
  const src = providerLogos[provider];
  const badgeClass = className ? `provider-logo ${className}` : "provider-logo";
  if (provider === "qoder") return <span className={`provider-logo provider-logo--monochrome${className ? ` ${className}` : ""}`} style={{ maskImage: `url("${src}")` }} aria-hidden="true" />;
  if (provider === "custom") return <svg className={badgeClass} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01M12 10v4"/></svg>;
  if (provider === "openai") return <svg className={badgeClass} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M8 12h8M12 8v8"/></svg>;
  if (provider === "anthropic") return <svg className={badgeClass} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M6 19 12 5l6 14M8.5 14h7"/></svg>;
  if (provider === "gemini") return <svg className={badgeClass} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M12 3c1.8 4.8 4.2 7.2 9 9-4.8 1.8-7.2 4.2-9 9-1.8-4.8-4.2-7.2-9-9 4.8-1.8 7.2-4.2 9-9Z"/></svg>;
  return src ? <img className={badgeClass} src={src} alt="" aria-hidden="true" /> : null;
}

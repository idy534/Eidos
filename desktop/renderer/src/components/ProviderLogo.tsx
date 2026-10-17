import deepseekLogo from "../assets/providers/deepseek.svg";
import kimiLogo from "../assets/providers/kimi.svg";
import minimaxLogo from "../assets/providers/minimax.svg";
import volcengineLogo from "../assets/providers/volcengine.svg";

const providerLogos: Record<string, string> = {
  deepseek: deepseekLogo,
  minimax: minimaxLogo,
  kimi: kimiLogo,
  volcengine: volcengineLogo,
};

const providerNames: Record<string, string> = {
  custom: "自定义",
  deepseek: "深度求索",
  minimax: "MiniMax",
  kimi: "月之暗面",
  volcengine: "火山引擎",
};

export function getProviderName(provider: string): string {
  return providerNames[provider] ?? provider;
}

export function ProviderLogo({ provider, className }: { provider: string; className?: string }) {
  const src = providerLogos[provider];
  if (provider === "custom") return <svg className={className ? `provider-logo ${className}` : "provider-logo"} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01M12 10v4"/></svg>;
  return src ? <img className={className ? `provider-logo ${className}` : "provider-logo"} src={src} alt="" aria-hidden="true" /> : null;
}

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
  return src ? <img className={className ? `provider-logo ${className}` : "provider-logo"} src={src} alt="" aria-hidden="true" /> : null;
}

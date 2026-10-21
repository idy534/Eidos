import { useState } from "react";
import type { DeepSeekSearchOptions, LangSearchOptions, Provider } from "../../../../shared/web-search.generated.js";

export const searchOptionDefaults = {
  defaultCount: 5,
  timeoutSeconds: 30,
  includePageContent: false,
  maxExcerptCharacters: 3000,
};

export function WebSearchProviderFields({
  provider,
  options,
  onChange,
}: {
  provider: Provider;
  options: DeepSeekSearchOptions | LangSearchOptions;
  onChange: (options: DeepSeekSearchOptions | LangSearchOptions) => void;
}) {
  const values = { ...searchOptionDefaults, ...options };
  const lang = options as LangSearchOptions;
  const [includeText, setIncludeText] = useState((lang.includeDomains ?? []).join(", "));
  const [excludeText, setExcludeText] = useState((lang.excludeDomains ?? []).join(", "));
  const update = (change: Partial<DeepSeekSearchOptions & LangSearchOptions>) =>
    onChange({ ...options, ...change });

  return (
    <div className="web-search-options-grid">
      <div className="web-search-options-section">
        <div className="web-search-options-title">常规参数</div>
        <div className="web-search-num-row">
          <label className="web-search-field-item">
            <span className="web-search-field-label">默认结果条数</span>
            <input
              type="number"
              min={1}
              max={50}
              required
              value={values.defaultCount}
              onChange={(event) => update({ defaultCount: Number(event.target.value) })}
            />
          </label>
          <label className="web-search-field-item">
            <span className="web-search-field-label">请求超时（秒）</span>
            <input
              type="number"
              min={5}
              max={120}
              required
              value={values.timeoutSeconds}
              onChange={(event) => update({ timeoutSeconds: Number(event.target.value) })}
            />
          </label>
          <label className="web-search-field-item">
            <span className="web-search-field-label">每条结果最多字符数</span>
            <input
              type="number"
              min={100}
              max={10000}
              required
              value={values.maxExcerptCharacters}
              onChange={(event) => update({ maxExcerptCharacters: Number(event.target.value) })}
            />
          </label>
        </div>

        <div className="web-search-option-check-wrap">
          <label className="web-search-check">
            <input
              type="checkbox"
              checked={values.includePageContent}
              onChange={(event) => update({ includePageContent: event.target.checked })}
            />
            <span>返回网页正文</span>
          </label>
          <span className="web-search-hint">提取完整正文有助于理解复杂页面，但会消耗更多上下文 Token。</span>
        </div>
      </div>

      <div className="web-search-options-section">
        <div className="web-search-options-title">高级控制</div>
        {provider === "deepseek" ? (
          <label className="web-search-field-item web-search-wide">
            <span className="web-search-field-label">本地化 IP（可选）</span>
            <input
              value={(options as DeepSeekSearchOptions).localization ?? ""}
              placeholder="例如 203.0.113.1"
              maxLength={45}
              onChange={(event) => update({ localization: event.target.value || null })}
            />
            <small className="web-search-hint">按目标 IP 优化区域搜索结果，不会主动探测你的公网真实 IP。</small>
          </label>
        ) : (
          <div className="web-search-langsearch-fields">
            <label className="web-search-field-item">
              <span className="web-search-field-label">时间范围</span>
              <input
                list="web-search-freshness"
                value={lang.freshness ?? "noLimit"}
                required
                maxLength={22}
                placeholder="noLimit / oneDay / oneWeek 等"
                onChange={(event) => update({ freshness: event.target.value })}
              />
              <datalist id="web-search-freshness">
                {["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"].map((value) => (
                  <option key={value} value={value} />
                ))}
              </datalist>
            </label>
            <label className="web-search-field-item web-search-wide">
              <span className="web-search-field-label">仅搜索这些域名（可选）</span>
              <input
                aria-label="仅搜索这些域名（可选）"
                value={includeText}
                placeholder="例如 github.com, python.org（逗号分隔）"
                onChange={(event) => {
                  setIncludeText(event.target.value);
                  update({
                    includeDomains: event.target.value
                      .split(/[,，\n]/)
                      .map((v) => v.trim())
                      .filter(Boolean),
                  });
                }}
              />
            </label>
            <label className="web-search-field-item web-search-wide">
              <span className="web-search-field-label">排除这些域名（可选）</span>
              <input
                aria-label="排除这些域名（可选）"
                value={excludeText}
                placeholder="例如 adsite.com, spam.org"
                onChange={(event) => {
                  setExcludeText(event.target.value);
                  update({
                    excludeDomains: event.target.value
                      .split(/[,，\n]/)
                      .map((v) => v.trim())
                      .filter(Boolean),
                  });
                }}
              />
            </label>
          </div>
        )}
      </div>
    </div>
  );
}

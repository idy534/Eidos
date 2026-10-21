import { useEffect, useState } from "react";
import type { DeepSeekSearchOptions, LangSearchOptions, Provider } from "../../../../shared/web-search.generated.js";

export const searchOptionDefaults = {
  defaultCount: 5,
  timeoutSeconds: 30,
  includePageContent: false,
  maxExcerptCharacters: 3000,
};

export const FRESHNESS_PRESETS = [
  { value: "noLimit", label: "不限时间" },
  { value: "oneDay", label: "过去 24 小时（一天内）" },
  { value: "oneWeek", label: "过去 1 周" },
  { value: "oneMonth", label: "过去 1 个月" },
  { value: "oneYear", label: "过去 1 年" },
  { value: "custom", label: "指定日期范围..." },
] as const;

type FreshnessPreset = (typeof FRESHNESS_PRESETS)[number]["value"];

function getLocalDateString(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseFreshness(freshness: string | undefined): {
  preset: FreshnessPreset;
  start: string;
  end: string;
} {
  const value = freshness?.trim() || "noLimit";
  const standardPresets = new Set(["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"]);
  if (standardPresets.has(value)) {
    return { preset: value as FreshnessPreset, start: "", end: "" };
  }
  const parts = value.split("..");
  return {
    preset: "custom",
    start: parts[0]?.trim() || "",
    end: parts[1]?.trim() || "",
  };
}

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

  const parsedFreshness = parseFreshness(lang.freshness);
  const [freshnessPreset, setFreshnessPreset] = useState<FreshnessPreset>(parsedFreshness.preset);
  const [customStart, setCustomStart] = useState(parsedFreshness.start);
  const [customEnd, setCustomEnd] = useState(parsedFreshness.end);

  useEffect(() => {
    const next = parseFreshness(lang.freshness);
    setFreshnessPreset(next.preset);
    if (next.preset === "custom") {
      setCustomStart((prev) => (prev !== next.start ? next.start : prev));
      setCustomEnd((prev) => (prev !== next.end ? next.end : prev));
    }
  }, [lang.freshness]);

  const update = (change: Partial<DeepSeekSearchOptions & LangSearchOptions>) =>
    onChange({ ...options, ...change });

  const handlePresetChange = (newPreset: FreshnessPreset) => {
    setFreshnessPreset(newPreset);
    if (newPreset !== "custom") {
      update({ freshness: newPreset });
    } else {
      const today = getLocalDateString();
      const nextStart = customStart || today;
      setCustomStart(nextStart);
      const nextFreshness = customEnd ? `${nextStart}..${customEnd}` : nextStart;
      update({ freshness: nextFreshness });
    }
  };

  const handleStartChange = (val: string) => {
    setCustomStart(val);
    if (!val) return;
    let nextEnd = customEnd;
    if (nextEnd && nextEnd < val) {
      nextEnd = val;
      setCustomEnd(nextEnd);
    }
    const nextFreshness = nextEnd ? `${val}..${nextEnd}` : val;
    update({ freshness: nextFreshness });
  };

  const handleEndChange = (val: string) => {
    setCustomEnd(val);
    const start = customStart || getLocalDateString();
    if (!customStart) {
      setCustomStart(start);
    }
    if (!val) {
      update({ freshness: start });
    } else if (val >= start) {
      update({ freshness: `${start}..${val}` });
    }
  };

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

      {provider === "langsearch" && (
        <div className="web-search-options-section">
          <div className="web-search-options-title">高级控制</div>
          <div className="web-search-langsearch-fields">
            <div className="web-search-field-item">
              <span className="web-search-field-label">时间范围</span>
              <select
                aria-label="时间范围"
                value={freshnessPreset}
                onChange={(event) => handlePresetChange(event.target.value as FreshnessPreset)}
              >
                {FRESHNESS_PRESETS.map((preset) => (
                  <option key={preset.value} value={preset.value}>
                    {preset.label}
                  </option>
                ))}
              </select>
              {freshnessPreset === "custom" && (
                <div className="web-search-custom-date-container">
                  <div className="web-search-custom-date-row">
                    <label className="web-search-custom-date-col">
                      <span className="web-search-field-label">起始日期</span>
                      <input
                        type="date"
                        aria-label="起始日期"
                        required
                        value={customStart}
                        onChange={(event) => handleStartChange(event.target.value)}
                      />
                    </label>
                    <span className="web-search-date-sep">至</span>
                    <label className="web-search-custom-date-col">
                      <span className="web-search-field-label">截止日期（可选）</span>
                      <input
                        type="date"
                        aria-label="截止日期（可选）"
                        value={customEnd}
                        min={customStart || undefined}
                        onChange={(event) => handleEndChange(event.target.value)}
                      />
                    </label>
                  </div>
                  <span className="web-search-hint">
                    支持指定单个起始日期或起止区间（例如 2026-01-01 至 2026-10-21）。
                  </span>
                </div>
              )}
            </div>
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
        </div>
      )}
    </div>
  );
}

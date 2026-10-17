import type { CustomModelSettings } from "../../../../shared/custom-models.js";
import type { ModelReasoningSelection } from "../../contracts.js";

const efforts = ["low", "medium", "high", "xhigh", "max"] as const;
const labels: Record<ModelReasoningSelection, string> = { none: "无", thinking: "开启", low: "低", medium: "中", high: "高", xhigh: "超高", max: "极致" };
interface Props { custom: CustomModelSettings; busy: boolean; onChange: (value: CustomModelSettings) => void; }

export function CustomReasoningFields({ custom, busy, onChange }: Props) {
  const reasoning = custom.reasoning;
  const selected = reasoning?.selections.filter(value => value !== "none") ?? [];
  const defaultSelection = reasoning?.defaultSelection ?? (custom.reasoningControl === "unsupported" ? "none" : "auto");
  const change = (selections: ModelReasoningSelection[], nextDefault: ModelReasoningSelection) => onChange({
    ...custom, reasoningFormat: "openai", reasoningControl: "configured",
    reasoning: { selections: ["none", ...selections.filter(value => value !== "none" && value !== "thinking")], defaultSelection: nextDefault },
  });
  return <div className="custom-model-reasoning">
    <div className="custom-model-section-heading"><strong>思考设置</strong></div>
    <label><span>默认思考强度</span><select aria-label="默认思考强度" value={defaultSelection} disabled={busy}
      onChange={event => {
        const value = event.target.value;
        if (value === "auto") onChange({ ...custom, reasoning: null, reasoningControl: "provider_default", reasoningFormat: "openai" });
        else { const selection = value as ModelReasoningSelection;
          change(selection === "none" ? selected : selected.includes(selection) ? selected : [...selected, selection], selection);
        }
      }}>
      <option value="auto">自动（使用接口默认值）</option>
      <option value="none">无</option>
      {efforts.map(value => <option key={value} value={value}>{labels[value]}</option>)}
      {defaultSelection === "thinking" && <option value="thinking">开启</option>}
    </select></label>
    <fieldset disabled={busy}><legend>支持的思考强度</legend><div className="custom-model-efforts">
      {efforts.map(selection => <label className="custom-model-check" key={selection}><input type="checkbox"
        checked={selected.includes(selection)} onChange={event => {
          const selections = event.target.checked ? [...selected, selection] : selected.filter(value => value !== selection);
          const nextDefault = defaultSelection === "auto" ? selection
            : defaultSelection === "thinking" || (defaultSelection !== "none" && !selections.includes(defaultSelection)) ? "none" : defaultSelection;
          change(selections, nextDefault);
        }} />{labels[selection]}</label>)}
    </div></fieldset>
  </div>;
}

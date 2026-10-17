import { useEffect, useId, useRef, useState } from "react";
import type { DiscoveredModel } from "../../../../shared/custom-models.js";

interface Props {
  value: string;
  models: DiscoveredModel[];
  disabled: boolean;
  onChange: (value: string) => void;
  onSelect: (model: DiscoveredModel) => void;
}

export function ModelIdCombobox({ value, models, disabled, onChange, onSelect }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [query, setQuery] = useState("");
  const filtered = models.filter(model => `${model.id} ${model.name ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  useEffect(() => {
    const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  const select = (model: DiscoveredModel) => { onSelect(model); setOpen(false); setQuery(""); };
  return <div className="custom-model-combobox" ref={root}>
    <label htmlFor={`${listId}-input`}>模型 ID <span className="custom-model-required" aria-hidden="true">*</span></label>
    <input id={`${listId}-input`} role="combobox" aria-label="模型 ID" aria-autocomplete="list"
      aria-expanded={open && models.length > 0} aria-controls={listId}
      aria-activedescendant={open && filtered[active] ? `${listId}-${active}` : undefined}
      aria-required="true" autoComplete="off" placeholder="选择模型，或手动输入 model-id" maxLength={256} value={value} disabled={disabled}
      onFocus={() => { setQuery(""); setOpen(true); setActive(0); }}
      onClick={() => { setQuery(""); setOpen(true); setActive(0); }}
      onChange={event => { onChange(event.target.value); setQuery(event.target.value); setOpen(true); setActive(0); }}
      onKeyDown={event => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault(); setOpen(true);
          const next = Math.min(Math.max(0, filtered.length - 1), Math.max(0, active + (event.key === "ArrowDown" ? 1 : -1)));
          setActive(next); document.getElementById(`${listId}-${next}`)?.scrollIntoView?.({ block: "nearest" });
        } else if (event.key === "Enter" && open && filtered[active]) {
          event.preventDefault(); select(filtered[active]);
        } else if (event.key === "Escape" && open) {
          event.stopPropagation(); setOpen(false);
        } else if (event.key === "Tab") setOpen(false);
      }} />
    {open && models.length > 0 && <div id={listId} role="listbox" aria-label="可用模型" className="custom-model-options">
      {filtered.map((model, index) => <button type="button" role="option" tabIndex={-1} id={`${listId}-${index}`}
        aria-selected={model.id === value} className={index === active ? "is-active" : ""} key={model.id}
        onMouseDown={event => event.preventDefault()} onMouseEnter={() => setActive(index)} onClick={() => select(model)}>
        <span title={model.id}>{model.id}</span><small title={model.name ?? undefined}>{model.name?.trim() || "未提供名称"}</small>
      </button>)}
      {!filtered.length && <p>没有匹配项，你可以直接使用输入的模型 ID。</p>}
    </div>}
  </div>;
}

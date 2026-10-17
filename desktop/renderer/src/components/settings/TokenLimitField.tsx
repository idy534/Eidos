import { useEffect, useId, useRef, useState } from "react";

interface Props {
  label: string;
  value: number;
  presets: readonly number[];
  disabled: boolean;
  onChange: (value: number) => void;
}

const format = (value: number) => value ? `${value / 1024}K` : "";
const parse = (raw: string) => {
  const match = /^(\d+(?:\.\d+)?)\s*([km]?)$/i.exec(raw.trim());
  return match ? Number(match[1]) * (match[2]?.toLowerCase() === "m" ? 1_048_576 : 1024) : 0;
};

export function TokenLimitField({ label, value, presets, disabled, onChange }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();
  const [text, setText] = useState(format(value));
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  useEffect(() => { setText(previous => parse(previous) === value ? previous : format(value)); }, [value]);
  useEffect(() => {
    const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  const openMenu = () => { setOpen(true); setActive(presets.indexOf(value)); };
  const select = (next: number) => { setText(format(next)); onChange(next); setOpen(false); };
  return <div className="custom-model-combobox custom-model-token-field" ref={root}>
    <label htmlFor={`${listId}-input`}>{label} <span className="custom-model-required" aria-hidden="true">*</span></label>
    <div className="custom-model-token-control">
      <input id={`${listId}-input`} role="combobox" aria-label={label} aria-autocomplete="list" aria-expanded={open}
        aria-controls={listId} aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        aria-required="true" autoComplete="off" inputMode="decimal" maxLength={24} placeholder="选择或输入" value={text} disabled={disabled}
        onFocus={openMenu} onClick={() => { if (!open) openMenu(); }}
        onChange={event => { const next = parse(event.target.value); setText(event.target.value); onChange(next); setActive(presets.indexOf(next)); }}
        onBlur={event => { if (!root.current?.contains(event.relatedTarget as Node)) { setOpen(false); if (value) setText(format(value)); } }}
        onKeyDown={event => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) openMenu();
            else setActive(index => index < 0 ? event.key === "ArrowDown" ? 0 : presets.length - 1
              : Math.min(presets.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1))));
          } else if (event.key === "Enter" && open) {
            event.preventDefault();
            if (active >= 0) select(presets[active]!);
            else { if (value) setText(format(value)); setOpen(false); }
          }
          else if (event.key === "Escape" && open) { event.stopPropagation(); setOpen(false); }
          else if (event.key === "Tab") setOpen(false);
        }} />
      <button type="button" tabIndex={-1} aria-label={`展开${label}预设`} disabled={disabled}
        onMouseDown={event => event.preventDefault()} onClick={() => { if (open) setOpen(false); else openMenu(); }}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg>
      </button>
    </div>
    {open && <div role="listbox" id={listId} aria-label={`${label}预设`} className="custom-model-options custom-model-token-options">
      {presets.map((preset, index) => <button type="button" role="option" tabIndex={-1} id={`${listId}-${index}`} key={preset}
        className={active === index ? "is-active" : ""} aria-selected={preset === value}
        onMouseDown={event => event.preventDefault()} onMouseEnter={() => setActive(index)} onClick={() => select(preset)}>
        <span>{format(preset)}</span>
        {preset === value && <svg className="custom-model-token-check" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m3 8 3 3 7-7" /></svg>}
      </button>)}
    </div>}
  </div>;
}

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type {
  ModelId,
  ModelOption,
  ModelReasoningSelection,
} from "../contracts.js";

const SELECTION_ORDER: readonly ModelReasoningSelection[] = [
  "none",
  "low",
  "medium",
  "high",
  "max",
  "thinking",
];

const SELECTION_LABELS: Record<ModelReasoningSelection, string> = {
  none: "关闭思考",
  thinking: "启用思考",
  low: "低",
  medium: "中",
  high: "高",
  max: "最高",
};

type PickerView = "reasoning" | "models";

export interface ReasoningSelectorProps {
  models: ModelOption[];
  selectedModelId: ModelId | undefined;
  selection: ModelReasoningSelection | undefined;
  disabled: boolean;
  onModelChange: (id: ModelId) => void;
  onChange: (selection: ModelReasoningSelection) => void;
}

export function ReasoningSelector({
  models,
  selectedModelId,
  selection,
  disabled,
  onModelChange,
  onChange,
}: ReasoningSelectorProps) {
  const panelId = useId();
  const modelRadioName = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const sliderRef = useRef<HTMLInputElement>(null);
  const modelHeadingRef = useRef<HTMLHeadingElement>(null);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<PickerView>("reasoning");

  const selectedModel = models.find((model) => model.id === selectedModelId);
  const selections = getSelections(selectedModel?.reasoning);
  const canAdjustReasoning = selections.length > 1;
  const selectedSelection = selection && selections.includes(selection)
    ? selection
    : selectedModel?.reasoning?.defaultSelection ?? selections[0];
  const selectedIndex = Math.max(0, selections.indexOf(selectedSelection ?? "none"));
  const sliderRatio = selections.length > 1 ? selectedIndex / (selections.length - 1) : 0;
  const sliderInset = (Math.abs(0.5 - sliderRatio) * 1.5).toFixed(3);
  const sliderProgress = (sliderRatio * 100).toFixed(3);
  const sliderFillWidth = `calc(${sliderProgress}% ${sliderRatio <= 0.5 ? "+" : "-"} ${sliderInset}rem)`;
  const currentLabel = SELECTION_LABELS[selectedSelection ?? "none"];
  const firstSelection = selections[0] ?? selectedSelection ?? "none";
  const lastSelection = selections.at(-1) ?? selectedSelection ?? "none";
  const modelName = selectedModel?.name ?? "选择模型";

  useEffect(() => {
    if (!open) return;

    const closeOnOutsideInteraction = (event: Event) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) {
        setOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    };

    document.addEventListener("pointerdown", closeOnOutsideInteraction, true);
    document.addEventListener("focusin", closeOnOutsideInteraction, true);
    document.addEventListener("keydown", closeOnEscape, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideInteraction, true);
      document.removeEventListener("focusin", closeOnOutsideInteraction, true);
      document.removeEventListener("keydown", closeOnEscape, true);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    if (view === "reasoning") {
      sliderRef.current?.focus();
      return;
    }
    modelHeadingRef.current?.focus();
  }, [open, selectedModelId, view]);

  const selectModel = (modelId: ModelId) => {
    const nextModel = models.find((model) => model.id === modelId);
    const nextCanAdjustReasoning = getSelections(nextModel?.reasoning).length > 1;
    onModelChange(modelId);

    if (nextCanAdjustReasoning) {
      setView("reasoning");
    } else {
      setOpen(false);
      triggerRef.current?.focus();
    }
  };

  return (
    <div ref={rootRef} className="reasoning-selector">
      <button
        ref={triggerRef}
        type="button"
        className="reasoning-selector__trigger"
        aria-label={canAdjustReasoning
          ? `${modelName}，思考强度 ${currentLabel}，打开模型和思考强度菜单`
          : `${modelName}，选择模型`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        disabled={disabled || models.length === 0}
        onClick={() => {
          if (open) {
            setOpen(false);
            return;
          }
          setView(canAdjustReasoning ? "reasoning" : "models");
          setOpen(true);
        }}
      >
        <span className="reasoning-selector__trigger-model">{modelName}</span>
        {canAdjustReasoning && (
          <span className="reasoning-selector__trigger-selection">
            {currentLabel}
          </span>
        )}
        <ChevronIcon />
      </button>

      {open && (
        <div
          id={panelId}
          className="reasoning-selector__panel"
          role="dialog"
          aria-label={view === "models" ? "选择模型" : `${modelName} 思考强度`}
        >
          {view === "models" ? (
            <>
              <header className="reasoning-selector__header reasoning-selector__header--models">
                <h2 ref={modelHeadingRef} tabIndex={-1}>选择模型</h2>
              </header>
              <fieldset className="reasoning-selector__model-list" aria-label="模型">
                {models.map((model) => (
                  <label
                    key={model.id}
                    className={`reasoning-selector__model-choice${model.id === selectedModelId ? " is-selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name={modelRadioName}
                      value={model.id}
                      checked={model.id === selectedModelId}
                      disabled={disabled}
                      onChange={() => selectModel(model.id)}
                    />
                    <span>{model.name}</span>
                    {model.id === selectedModelId && (
                      <span className="reasoning-selector__check" aria-hidden="true">✓</span>
                    )}
                  </label>
                ))}
              </fieldset>
            </>
          ) : (
            <>
              <header className="reasoning-selector__header">
                <button
                  type="button"
                  className="reasoning-selector__model-button"
                  disabled={disabled}
                  aria-label={`切换模型，当前为 ${modelName}`}
                  onClick={() => setView("models")}
                >
                  <span>{modelName}</span>
                  <ChevronRightIcon />
                </button>
                <span>{currentLabel}</span>
              </header>
              <label className="sr-only" htmlFor={`${panelId}-slider`}>思考强度</label>
              <div className="reasoning-selector__slider-wrap">
                <div className="reasoning-selector__rail" aria-hidden="true">
                  <span className="reasoning-selector__fill" style={{ width: sliderFillWidth }} />
                  <span className="reasoning-selector__dots">
                    {selections.map((value) => (
                      <svg key={value} viewBox="0 0 8 8" aria-hidden="true">
                        <circle cx="4" cy="4" r="4" fill="currentColor" />
                      </svg>
                    ))}
                  </span>
                </div>
                <input
                  ref={sliderRef}
                  id={`${panelId}-slider`}
                  className="reasoning-selector__range"
                  type="range"
                  min={0}
                  max={selections.length - 1}
                  step={1}
                  value={selectedIndex}
                  disabled={disabled || !canAdjustReasoning}
                  aria-valuetext={currentLabel}
                  aria-label={`${modelName} 思考强度`}
                  onChange={(event) => {
                    const next = selections[Number(event.currentTarget.value)];
                    if (next) onChange(next);
                  }}
                />
              </div>
              <div className="reasoning-selector__scale-labels" aria-hidden="true">
                <span>{SELECTION_LABELS[firstSelection]}</span>
                <span>{SELECTION_LABELS[lastSelection]}</span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function getSelections(reasoning: ModelOption["reasoning"]): ModelReasoningSelection[] {
  const available = new Set(reasoning?.selections ?? []);
  return SELECTION_ORDER.filter((value) => available.has(value));
}

function ChevronIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="m6 4 4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

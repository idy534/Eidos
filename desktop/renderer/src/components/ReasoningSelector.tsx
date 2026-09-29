import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type {
  ModelId,
  ModelOption,
  ModelReasoningSelection,
} from "../contracts.js";
import { getProviderName, ProviderLogo } from "./ProviderLogo.js";

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

const FULL_MULTIMODAL_MODELS = new Set([
  "glm-5.3-flash",
  "minimax-m3",
  "MiniMax-M3",
  "kimi-k3",
  "kimi-k2.7-code-highspeed",
]);

const MODEL_CONTEXT_LIMITS: Record<string, string> = {
  "deepseek-v4.1-flash": "1M",
  "glm-5.3-flash": "1M",
  "glm-5.3": "1M",
  "deepseek-v4-flash-ga-260731": "1M",
  "deepseek-v4-pro-ga-260813": "1M",
  "deepseek-flash": "800K",
  "minimax-m3": "1M",
  "MiniMax-M3": "1M",
  "kimi-k3": "1M",
  "kimi-k2.7-code-highspeed": "256K",
};

function getModelProviderDisplay(model: ModelOption): string {
  return getProviderName(model.provider) || model.vendor;
}

function getModelInputDisplay(model: ModelOption): string {
  if (FULL_MULTIMODAL_MODELS.has(model.id)) {
    return "文本, 图像, 视频, PDF";
  }
  if (model.supportsImages) {
    return "文本, 图像";
  }
  return "文本";
}

function getModelReasoningDisplay(model: ModelOption): string {
  return model.supportsReasoning ? "支持推理" : "不支持推理";
}

function getModelContextDisplay(model: ModelOption): string {
  return MODEL_CONTEXT_LIMITS[model.id] ?? "128K";
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
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const sliderRef = useRef<HTMLInputElement>(null);
  const modelHeadingRef = useRef<HTMLHeadingElement>(null);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<PickerView>("reasoning");
  const [hoveredModel, setHoveredModel] = useState<ModelOption | null>(null);
  const [cardTop, setCardTop] = useState(0);
  const [cardPlacement, setCardPlacement] = useState<"right" | "left">("right");

  const selectedModel = models.find((model) => model.id === selectedModelId);
  const selections = getSelections(selectedModel?.reasoning);
  const canAdjustReasoning = selections.length > 1;
  const selectedSelection = selection && selections.includes(selection)
    ? selection
    : selectedModel?.reasoning?.defaultSelection ?? selections[0];
  const selectedIndex = Math.max(0, selections.indexOf(selectedSelection ?? "none"));
  const maxSliderIndex = Math.max(0, selections.length - 1);
  const [sliderPosition, setSliderPosition] = useState(selectedIndex);
  const [isDragging, setIsDragging] = useState(false);
  const sliderPositionRef = useRef(selectedIndex);
  const isDraggingRef = useRef(false);
  const sliderPositionValue = Math.min(maxSliderIndex, Math.max(0, sliderPosition));
  const sliderRatio = maxSliderIndex > 0 ? sliderPositionValue / maxSliderIndex : 0;
  const sliderInset = (Math.abs(0.5 - sliderRatio) * 1.5).toFixed(3);
  const sliderProgress = (sliderRatio * 100).toFixed(3);
  const sliderFillWidth = `calc(${sliderProgress}% ${sliderRatio <= 0.5 ? "+" : "-"} ${sliderInset}rem)`;
  const sliderPreviewIndex = Math.min(maxSliderIndex, Math.max(0, Math.round(sliderPositionValue)));
  const currentLabel = SELECTION_LABELS[selectedSelection ?? "none"];
  const sliderLabel = SELECTION_LABELS[selections[sliderPreviewIndex] ?? selectedSelection ?? "none"];
  const firstSelection = selections[0] ?? selectedSelection ?? "none";
  const lastSelection = selections.at(-1) ?? selectedSelection ?? "none";
  const modelName = selectedModel?.name ?? "选择模型";
  const providerName = selectedModel ? getProviderName(selectedModel.provider) : "";
  const modelLabel = providerName ? `${modelName}，${providerName}` : modelName;

  useEffect(() => {
    if (isDraggingRef.current) return;
    sliderPositionRef.current = selectedIndex;
    setSliderPosition(selectedIndex);
  }, [selectedIndex, selectedModelId]);

  useEffect(() => {
    if (!open) {
      setHoveredModel(null);
      return;
    }

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
    setHoveredModel(null);
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

  const handleModelHover = (model: ModelOption, element: HTMLElement | null) => {
    setHoveredModel(model);
    if (element && panelRef.current) {
      const panelRect = panelRef.current.getBoundingClientRect();
      const spaceOnRight = window.innerWidth - panelRect.right;
      setCardPlacement(spaceOnRight >= 230 ? "right" : "left");

      const cardHeight = 148;
      const viewportMargin = 8;
      const itemTop = element.offsetTop;
      let top = itemTop - 4;

      const cardBottomInViewport = panelRect.top + top + cardHeight;
      if (cardBottomInViewport > window.innerHeight - viewportMargin) {
        top = window.innerHeight - viewportMargin - panelRect.top - cardHeight;
      }

      setCardTop(Math.max(0, top));
    }
  };

  const handleModelLeave = (modelId: ModelId) => {
    setHoveredModel((current) => (current?.id === modelId ? null : current));
  };

  const commitSliderPosition = (position: number) => {
    const index = Math.min(maxSliderIndex, Math.max(0, Math.round(position)));
    const nextSelection = selections[index];
    sliderPositionRef.current = index;
    setSliderPosition(index);
    setIsDragging(false);
    isDraggingRef.current = false;
    if (nextSelection && index !== selectedIndex) onChange(nextSelection);
  };

  const updateSliderPosition = (value: number) => {
    const position = Math.min(maxSliderIndex, Math.max(0, value));
    sliderPositionRef.current = position;
    setSliderPosition(position);
    if (!isDraggingRef.current) commitSliderPosition(position);
  };

  const finishSliderDrag = () => {
    if (isDraggingRef.current) commitSliderPosition(sliderPositionRef.current);
  };

  return (
    <div ref={rootRef} className="reasoning-selector">
      <button
        ref={triggerRef}
        type="button"
        className="reasoning-selector__trigger"
        aria-label={canAdjustReasoning
          ? `${modelLabel}，思考强度 ${currentLabel}，打开模型和思考强度菜单`
          : `${modelLabel}，选择模型`}
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
        <span className="reasoning-selector__trigger-model">
          {selectedModel && <ProviderLogo provider={selectedModel.provider} className="reasoning-selector__trigger-logo" />}
          <span className="reasoning-selector__trigger-model-name">{modelName}</span>
        </span>
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
          ref={panelRef}
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
                    onMouseEnter={(event) => handleModelHover(model, event.currentTarget)}
                    onMouseLeave={() => handleModelLeave(model.id)}
                    onFocus={(event) => handleModelHover(model, event.currentTarget)}
                    onBlur={() => handleModelLeave(model.id)}
                  >
                    <input
                      type="radio"
                      name={modelRadioName}
                      value={model.id}
                      aria-label={`${model.name}，${getProviderName(model.provider)}`}
                      checked={model.id === selectedModelId}
                      disabled={disabled}
                      onChange={() => selectModel(model.id)}
                    />
                    <ProviderLogo provider={model.provider} className="reasoning-selector__model-logo" />
                    <span className="reasoning-selector__model-name">{model.name}</span>
                    {model.id === selectedModelId && (
                      <span className="reasoning-selector__check" aria-hidden="true">✓</span>
                    )}
                  </label>
                ))}
              </fieldset>
              {hoveredModel && (
                <div
                  className={`reasoning-selector__info-card reasoning-selector__info-card--${cardPlacement}`}
                  style={{ top: `${cardTop}px` }}
                  role="tooltip"
                  aria-live="polite"
                >
                  <div className="reasoning-selector__info-row">
                    <span className="reasoning-selector__info-label">模型</span>
                    <span className="reasoning-selector__info-value" title={hoveredModel.name}>
                      {hoveredModel.name}
                    </span>
                  </div>
                  <div className="reasoning-selector__info-row">
                    <span className="reasoning-selector__info-label">提供商</span>
                    <span className="reasoning-selector__info-value" title={getModelProviderDisplay(hoveredModel)}>
                      {getModelProviderDisplay(hoveredModel)}
                    </span>
                  </div>
                  <div className="reasoning-selector__info-row">
                    <span className="reasoning-selector__info-label">输入</span>
                    <span className="reasoning-selector__info-value">
                      {getModelInputDisplay(hoveredModel)}
                    </span>
                  </div>
                  <div className="reasoning-selector__info-row">
                    <span className="reasoning-selector__info-label">推理</span>
                    <span className="reasoning-selector__info-value">
                      {getModelReasoningDisplay(hoveredModel)}
                    </span>
                  </div>
                  <div className="reasoning-selector__info-row">
                    <span className="reasoning-selector__info-label">上下文</span>
                    <span className="reasoning-selector__info-value">
                      {getModelContextDisplay(hoveredModel)}
                    </span>
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              <header className="reasoning-selector__header">
                <button
                  type="button"
                  className="reasoning-selector__model-button"
                  disabled={disabled}
                  aria-label={`切换模型，当前为 ${modelName}，提供商 ${providerName}`}
                  onClick={() => setView("models")}
                >
                  {selectedModel && <ProviderLogo provider={selectedModel.provider} className="reasoning-selector__model-button-logo" />}
                  <span className="reasoning-selector__model-button-name">{modelName}</span>
                  <ChevronRightIcon />
                </button>
                <span>{isDragging ? sliderLabel : currentLabel}</span>
              </header>
              <label className="sr-only" htmlFor={`${panelId}-slider`}>思考强度</label>
              <div className={`reasoning-selector__slider-wrap${isDragging ? " is-dragging" : ""}`}>
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
                  max={maxSliderIndex}
                  step={isDragging ? "any" : 1}
                  value={sliderPositionValue}
                  disabled={disabled || !canAdjustReasoning}
                  aria-valuetext={isDragging ? sliderLabel : currentLabel}
                  aria-label={`${modelName} 思考强度`}
                  onPointerDown={(event) => {
                    isDraggingRef.current = true;
                    setIsDragging(true);
                    event.currentTarget.setPointerCapture?.(event.pointerId);
                  }}
                  onPointerUp={finishSliderDrag}
                  onPointerCancel={finishSliderDrag}
                  onLostPointerCapture={finishSliderDrag}
                  onBlur={finishSliderDrag}
                  onChange={(event) => updateSliderPosition(Number(event.currentTarget.value))}
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

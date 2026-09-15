import { useCallback, useEffect, useRef, useState } from "react";
import type { ModelId, ModelListResult, ModelReasoningSelection } from "../contracts.js";
import { userFacingError } from "../session-state.js";

const REASONING_OVERRIDES_KEY = "eidos.modelReasoningOverrides.v1";
const REASONING_SELECTIONS = new Set<ModelReasoningSelection>([
  "none",
  "thinking",
  "low",
  "medium",
  "high",
  "max",
]);

function isReasoningSelection(value: unknown): value is ModelReasoningSelection {
  return typeof value === "string" && REASONING_SELECTIONS.has(value as ModelReasoningSelection);
}

function loadReasoningOverrides(): Record<string, ModelReasoningSelection> {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(REASONING_OVERRIDES_KEY) ?? "{}");
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
    return Object.fromEntries(
      Object.entries(stored).filter((entry): entry is [string, ModelReasoningSelection] =>
        isReasoningSelection(entry[1]),
      ),
    );
  } catch {
    return {};
  }
}

function saveReasoningOverrides(overrides: Record<string, ModelReasoningSelection>): void {
  try {
    window.localStorage.setItem(REASONING_OVERRIDES_KEY, JSON.stringify(overrides));
  } catch {
    // Model preference persistence is non-critical.
  }
}

export interface ModelControllerState {
  list: ModelListResult | undefined;
  selectedModelId: ModelId | undefined;
  reasoningSelection: ModelReasoningSelection | undefined;
  loading: boolean;
  error: string | undefined;
}

export interface ModelControllerActions {
  initialize(list: ModelListResult, currentSessionModelId?: ModelId): void;
  load(currentSessionModelId?: ModelId): Promise<void>;
  selectModel(modelId: ModelId): void;
  setReasoningSelection(modelId: ModelId, selection: ModelReasoningSelection): void;
  clearError(): void;
}

export function resolveSelectedModel(
  list: ModelListResult | undefined,
  currentSessionModelId?: ModelId,
  currentSelectedModelId?: ModelId,
): { selectedModelId: ModelId | undefined } {
  const ids = new Set(list?.models.map((model) => model.id) ?? []);
  if (currentSessionModelId && ids.has(currentSessionModelId)) {
    return { selectedModelId: currentSessionModelId };
  }
  if (currentSelectedModelId && ids.has(currentSelectedModelId)) {
    return { selectedModelId: currentSelectedModelId };
  }
  return { selectedModelId: list?.models[0]?.id };
}

export function useModelController(): [ModelControllerState, ModelControllerActions] {
  const [list, setList] = useState<ModelListResult | undefined>(undefined);
  const [selectedModelId, setSelectedModelId] = useState<ModelId | undefined>(undefined);
  const [reasoningOverrides, setReasoningOverrides] = useState(loadReasoningOverrides);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const selectedRef = useRef<ModelId | undefined>(undefined);
  const listRef = useRef<ModelListResult | undefined>(undefined);
  selectedRef.current = selectedModelId;
  listRef.current = list;

  useEffect(() => {
    saveReasoningOverrides(reasoningOverrides);
  }, [reasoningOverrides]);

  const applyList = useCallback((next: ModelListResult, sessionModelId?: ModelId) => {
    setList(next);
    const resolved = resolveSelectedModel(next, sessionModelId, selectedRef.current);
    setSelectedModelId(resolved.selectedModelId);
    selectedRef.current = resolved.selectedModelId;
    setError(undefined);
  }, []);

  const initialize = useCallback((next: ModelListResult, sessionModelId?: ModelId) => {
    setList(next);
    const resolved = resolveSelectedModel(next, sessionModelId, selectedRef.current);
    setSelectedModelId(resolved.selectedModelId);
    selectedRef.current = resolved.selectedModelId;
    setError(undefined);
  }, []);

  const load = useCallback(async (sessionModelId?: ModelId) => {
    setLoading(true);
    setError(undefined);
    try {
      applyList(await window.eidosRuntime.listModels(), sessionModelId);
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setLoading(false);
    }
  }, [applyList]);

  const selectModel = useCallback((modelId: ModelId) => {
    if (!listRef.current?.models.some((model) => model.id === modelId)) {
      setError(`Model ${modelId} is not configured`);
      return;
    }
    setSelectedModelId(modelId);
    selectedRef.current = modelId;
    setError(undefined);
  }, []);

  const setReasoningSelection = useCallback((modelId: ModelId, selection: ModelReasoningSelection) => {
    const model = listRef.current?.models.find((candidate) => candidate.id === modelId);
    const reasoning = model?.reasoning;
    if (!reasoning?.selections.includes(selection)) return;
    setReasoningOverrides((previous) => {
      if (selection === reasoning.defaultSelection) {
        if (previous[modelId] === undefined) return previous;
        const next = { ...previous };
        delete next[modelId];
        return next;
      }
      return { ...previous, [modelId]: selection };
    });
  }, []);

  const selectedModel = list?.models.find((model) => model.id === selectedModelId);
  const savedSelection = selectedModelId ? reasoningOverrides[selectedModelId] : undefined;
  const reasoningSelection = selectedModel?.reasoning
    ? (savedSelection && selectedModel.reasoning.selections.includes(savedSelection)
      ? savedSelection
      : selectedModel.reasoning.defaultSelection)
    : undefined;

  return [
    { list, selectedModelId, reasoningSelection, loading, error },
    {
      initialize,
      load,
      selectModel,
      setReasoningSelection,
      clearError: useCallback(() => setError(undefined), []),
    },
  ];
}

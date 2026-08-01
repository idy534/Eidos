import { useCallback, useRef, useState } from "react";
import type { ModelId, ModelListResult } from "../contracts.js";
import { userFacingError } from "../session-state.js";

export interface ModelControllerState {
  list: ModelListResult | undefined;
  selectedModelId: ModelId | undefined;
  loading: boolean;
  error: string | undefined;
}

export interface ModelControllerActions {
  initialize(list: ModelListResult, currentSessionModelId?: ModelId): void;
  load(currentSessionModelId?: ModelId): Promise<void>;
  selectModel(modelId: ModelId): void;
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const selectedRef = useRef<ModelId | undefined>(undefined);
  const listRef = useRef<ModelListResult | undefined>(undefined);
  selectedRef.current = selectedModelId;
  listRef.current = list;

  const applyList = useCallback((next: ModelListResult, sessionModelId?: ModelId) => {
    setList(next);
    const resolved = resolveSelectedModel(next, sessionModelId, selectedRef.current);
    setSelectedModelId(resolved.selectedModelId);
    selectedRef.current = resolved.selectedModelId;
    setError(undefined);
  }, []);

  const initialize = useCallback((next: ModelListResult, sessionModelId?: ModelId) => {
    setList(next);
    const resolved = resolveSelectedModel(next, sessionModelId);
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

  return [
    { list, selectedModelId, loading, error },
    {
      initialize,
      load,
      selectModel,
      clearError: useCallback(() => setError(undefined), []),
    },
  ];
}

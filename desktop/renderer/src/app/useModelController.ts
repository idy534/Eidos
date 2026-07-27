import { useCallback, useRef, useState } from "react";
import type { ModelId, ModelListResult, ModelOption, ModelStatus } from "../contracts.js";
import { userFacingError } from "../session-state.js";

export interface ModelControllerState {
  status: ModelStatus | undefined;
  list: ModelListResult | undefined;
  selectedModelId: ModelId | undefined;
  loading: boolean;
  configuring: boolean;
  error: string | undefined;
}

export interface ModelControllerActions {
  initialize(
    status: ModelStatus,
    list: ModelListResult,
    currentSessionModelId?: ModelId,
  ): void;
  load(): Promise<void>;
  configure(apiKey: string): Promise<boolean>;
  selectModel(modelId: ModelId): void;
  clearError(): void;
}

export function resolveSelectedModel(
  list: ModelListResult | undefined,
  currentSessionModelId?: ModelId,
  currentSelectedModelId?: ModelId,
): { selectedModelId?: ModelId | undefined; error?: string | undefined } {
  if (!list || !Array.isArray(list.models) || list.models.length === 0) {
    return {
      selectedModelId: undefined,
      error: "Runtime returned invalid or empty model list",
    };
  }

  const selectableMap = new Map<ModelId, ModelOption>();
  for (const model of list.models) {
    if (model.selectable) {
      selectableMap.set(model.id, model);
    }
  }

  // 1. Current session Run model
  if (currentSessionModelId && selectableMap.has(currentSessionModelId)) {
    return { selectedModelId: currentSessionModelId };
  }

  // 2. Current selected model if still selectable
  if (currentSelectedModelId && selectableMap.has(currentSelectedModelId)) {
    return { selectedModelId: currentSelectedModelId };
  }

  // 3. defaultModelId if selectable
  if (list.defaultModelId && selectableMap.has(list.defaultModelId)) {
    return { selectedModelId: list.defaultModelId };
  }

  // 4. First selectable model
  const firstSelectable = list.models.find((m) => m.selectable);
  if (firstSelectable) {
    return { selectedModelId: firstSelectable.id };
  }

  // 5. No selectable model available from Runtime
  return {
    selectedModelId: undefined,
    error: "No selectable model available from Runtime",
  };
}

export function useModelController(): [ModelControllerState, ModelControllerActions] {
  const [status, setStatus] = useState<ModelStatus | undefined>(undefined);
  const [list, setList] = useState<ModelListResult | undefined>(undefined);
  const [selectedModelId, setSelectedModelId] = useState<ModelId | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(false);
  const [configuring, setConfiguring] = useState<boolean>(false);
  const [error, setError] = useState<string | undefined>(undefined);

  const configuringRef = useRef<boolean>(false);
  const selectedModelIdRef = useRef<ModelId | undefined>(selectedModelId);
  selectedModelIdRef.current = selectedModelId;
  const listRef = useRef<ModelListResult | undefined>(list);
  listRef.current = list;

  const initialize = useCallback((
    newStatus: ModelStatus,
    newList: ModelListResult,
    currentSessionModelId?: ModelId,
  ): void => {
    setStatus(newStatus);
    setList(newList);
    const { selectedModelId: computed, error: selectionError } = resolveSelectedModel(
      newList,
      currentSessionModelId,
      selectedModelIdRef.current,
    );
    setSelectedModelId(computed);
    if (selectionError) {
      setError(selectionError);
    } else {
      setError(undefined);
    }
  }, []);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(undefined);
    try {
      const [fetchedStatus, fetchedList] = await Promise.all([
        window.eidosRuntime.getModelStatus(),
        window.eidosRuntime.listModels(),
      ]);
      setStatus(fetchedStatus);
      setList(fetchedList);
      const { selectedModelId: computed, error: selectionError } = resolveSelectedModel(
        fetchedList,
        undefined,
        selectedModelIdRef.current,
      );
      setSelectedModelId(computed);
      if (selectionError) {
        setError(selectionError);
      }
    } catch (cause) {
      // Preserve previously valid model state on failure, set local error
      setError(userFacingError(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  const configure = useCallback(async (apiKey: string): Promise<boolean> => {
    if (configuringRef.current) {
      return false;
    }
    configuringRef.current = true;
    setConfiguring(true);
    setError(undefined);
    try {
      const updatedStatus = await window.eidosRuntime.configureModel(apiKey);
      const updatedList = await window.eidosRuntime.listModels();
      setStatus(updatedStatus);
      setList(updatedList);
      const { selectedModelId: computed, error: selectionError } = resolveSelectedModel(
        updatedList,
        undefined,
        selectedModelIdRef.current,
      );
      setSelectedModelId(computed);
      if (selectionError) {
        setError(selectionError);
      }
      return true;
    } catch (cause) {
      // Preserve previously valid model state on failure, set local error
      setError(userFacingError(cause));
      throw cause;
    } finally {
      configuringRef.current = false;
      setConfiguring(false);
    }
  }, []);

  const selectModel = useCallback((modelId: ModelId): void => {
    const currentList = listRef.current;
    if (!currentList || !Array.isArray(currentList.models)) return;
    const target = currentList.models.find((m) => m.id === modelId);
    if (!target || !target.selectable) {
      setError(`Model ${modelId} is not selectable`);
      return;
    }
    setError(undefined);
    setSelectedModelId(modelId);
    selectedModelIdRef.current = modelId;
  }, []);

  const clearError = useCallback((): void => {
    setError(undefined);
  }, []);

  const state: ModelControllerState = {
    status,
    list,
    selectedModelId,
    loading,
    configuring,
    error,
  };

  const actions: ModelControllerActions = {
    initialize,
    load,
    configure,
    selectModel,
    clearError,
  };

  return [state, actions];
}

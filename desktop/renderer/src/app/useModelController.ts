import { useCallback, useState } from "react";
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
  configure(apiKey: string): Promise<void>;
  selectModel(modelId: ModelId): void;
  clearError(): void;
}

export function resolveSelectedModel(
  list: ModelListResult | undefined,
  currentSessionModelId?: ModelId,
  currentSelectedModelId?: ModelId,
): { selectedModelId: ModelId; error?: string } {
  if (!list || !Array.isArray(list.models) || list.models.length === 0) {
    return {
      selectedModelId: "deepseek-v4-flash",
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

  // 5. Hardcoded fallback only when Runtime response is invalid / no selectable models
  return {
    selectedModelId: "deepseek-v4-flash",
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
      selectedModelId,
    );
    setSelectedModelId(computed);
    if (selectionError) {
      setError(selectionError);
    } else {
      setError(undefined);
    }
  }, [selectedModelId]);

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
        selectedModelId,
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
  }, [selectedModelId]);

  const configure = useCallback(async (apiKey: string): Promise<void> => {
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
        selectedModelId,
      );
      setSelectedModelId(computed);
      if (selectionError) {
        setError(selectionError);
      }
    } catch (cause) {
      // Preserve previously valid model state on failure, set local error
      setError(userFacingError(cause));
      throw cause;
    } finally {
      setConfiguring(false);
    }
  }, [selectedModelId]);

  const selectModel = useCallback((modelId: ModelId): void => {
    setSelectedModelId(modelId);
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

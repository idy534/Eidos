/**
 * Generated from Eidos Runtime Pydantic models.
 * Do not edit manually.
 */

/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "ReasoningEffort".
 */
export type ReasoningEffort = 'low' | 'medium' | 'high';
export type ReasoningMode = 'none' | 'native' | 'compatible';
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "WireAPI".
 */
export type WireAPI = 'openai_responses' | 'openai_chat_completions';
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "ReasoningMode".
 */
export type ReasoningMode1 = 'none' | 'native' | 'compatible';

/**
 * Schema-only root that retains the Desktop Model Profile contract.
 */
export interface ModelProfileContractBundle {
  profile: ModelProfile;
}
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "ModelProfile".
 */
export interface ModelProfile {
  authReference: string;
  baseUrl?: string | null;
  contextWindow?: number | null;
  createdAt: string;
  id: string;
  maxOutputTokens?: number | null;
  modelId: string;
  name: string;
  provider: string;
  reasoningEffort?: ReasoningEffort | null;
  reasoningMode?: ReasoningMode;
  requestTimeout?: number;
  retryPolicy?: RetryPolicy;
  schemaVersion?: number;
  supportsImages?: boolean | null;
  supportsParallelTools?: boolean | null;
  supportsPromptCache?: boolean | null;
  supportsStructuredOutput?: boolean | null;
  supportsTools?: boolean | null;
  updatedAt: string;
  wireApi: WireAPI;
}
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "RetryPolicy".
 */
export interface RetryPolicy {
  initialBackoffSeconds?: number;
  maxAttempts?: number;
  maxBackoffSeconds?: number;
}

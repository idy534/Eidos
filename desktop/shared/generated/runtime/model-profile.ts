/**
 * Generated from Eidos Runtime Pydantic models.
 * Do not edit manually.
 */

/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "CapabilityProbeSource".
 */
export type CapabilityProbeSource =
  'provider_metadata' | 'active_probe' | 'built_in_preset' | 'user_declaration' | 'conservative_default';
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "ReasoningMode".
 */
export type ReasoningMode = 'none' | 'native' | 'compatible';
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "ReasoningEffort".
 */
export type ReasoningEffort = 'low' | 'medium' | 'high';
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "WireAPI".
 */
export type WireAPI = 'openai_responses' | 'openai_chat_completions';
export type ReasoningMode1 = 'none' | 'native' | 'compatible';

/**
 * Schema-only root that retains the Model Profile contract's definitions.
 */
export interface ModelProfileContractBundle {
  capabilitySnapshot: CapabilitySnapshot;
  profile: ModelProfile;
}
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "CapabilitySnapshot".
 */
export interface CapabilitySnapshot {
  authenticated: boolean;
  contextWindow?: number | null;
  id: string;
  maxOutputTokens?: number | null;
  modelId: string;
  probeSource: CapabilityProbeSource;
  probeVersion: string;
  probedAt: string;
  profileId: string;
  provider: string;
  reachable: boolean;
  reasoningMode: ReasoningMode;
  schemaVersion?: number;
  sources?: {
    [k: string]: CapabilityProbeSource;
  };
  supportedReasoningEfforts?: ReasoningEffort[];
  supportsImages: boolean;
  supportsParallelTools: boolean;
  supportsPromptCache: boolean;
  supportsStructuredOutput: boolean;
  supportsTools: boolean;
  warnings?: CapabilityWarning[];
  wireApi: WireAPI;
}
/**
 * This interface was referenced by `ModelProfileContractBundle`'s JSON-Schema
 * via the `definition` "CapabilityWarning".
 */
export interface CapabilityWarning {
  capability?: string | null;
  code: string;
  message: string;
  source: CapabilityProbeSource;
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
  reasoningMode?: ReasoningMode1;
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

import { RuntimeRequestError } from "./runtime-client.js";

/** A removed preview file is an expected empty state at the Desktop IPC boundary. */
export async function workspacePreviewUrl(prepare: () => Promise<string>): Promise<string | null> {
  try {
    return await prepare();
  } catch (error) {
    if (error instanceof RuntimeRequestError && error.businessCode === "WORKSPACE_FILE_NOT_FOUND") return null;
    throw error;
  }
}

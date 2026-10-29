import type { ProjectRootInput } from "../../../shared/project-workspaces.generated.js";
import { ProjectEditorDialog } from "./ProjectEditorDialog.js";

interface CreateProjectDialogProps {
  open: boolean;
  sourceFolder?: string | undefined;
  busy?: boolean;
  error?: string | undefined;
  onCreate: (name: string | undefined, sourceFolder: string) => void;
  onCreateFolders?: ((name: string | undefined, roots: ProjectRootInput[], primaryRootId: string) => void) | undefined;
  onSelectFolder: () => void;
  onCancel: () => void;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
}

export function CreateProjectDialog({ onCreate, onCreateFolders, onSelectFolder, ...props }: CreateProjectDialogProps) {
  return <ProjectEditorDialog {...props} onSelectFolder={onCreateFolders ? undefined : onSelectFolder} onSave={(name, roots, primary) => {
    if (onCreateFolders) onCreateFolders(name, roots, primary);
    else onCreate(name, roots.find((root) => root.id === primary)!.path);
  }} />;
}

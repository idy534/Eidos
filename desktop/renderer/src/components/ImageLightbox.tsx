import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { ChevronRightIcon, CloseIcon } from "./MenuIcons.js";

export interface LightboxImage {
  /** Alt text, caption and the default dialog name. */
  name: string;
  /** Accessible dialog name; defaults to "<name> 图片预览". */
  dialogLabel?: string | undefined;
  url?: string | undefined;
  /** Shown in place of the image while it has no URL. */
  status?: string | undefined;
  /** The status reports a failure rather than progress. */
  failed?: boolean | undefined;
}

/**
 * Full-window image preview shared by every enlargeable image. One image is a
 * plain enlargement. Several images can be stepped through with the side
 * buttons or the arrow keys, stopping at both ends.
 */
export function ImageLightbox({ images, index = 0, onIndexChange, onClose }: {
  images: readonly LightboxImage[];
  index?: number | undefined;
  /** Required for stepping; without it several images still show only the current one. */
  onIndexChange?: ((index: number) => void) | undefined;
  onClose(): void;
}) {
  const last = images.length - 1;
  const current = Math.min(Math.max(index, 0), last);
  const image = images[current];
  const stepTo = images.length > 1 ? onIndexChange : undefined;

  const latest = useRef({ current, last, stepTo, onClose });
  latest.current = { current, last, stepTo, onClose };
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const state = latest.current;
      if (event.key === "Escape") {
        state.onClose();
        return;
      }
      if (!state.stepTo || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.key === "ArrowLeft" ? state.current - 1 : event.key === "ArrowRight" ? state.current + 1 : undefined;
      if (target === undefined) return;
      event.preventDefault();
      if (target < 0 || target > state.last) return;
      // Key repeat can outrun the next render, so the position advances here first.
      state.current = target;
      state.stepTo(target);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (!image || typeof document === "undefined") return null;

  return createPortal(
    <div
      className={`artifact-image-preview${stepTo ? " artifact-image-preview--gallery" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-label={image.dialogLabel ?? `${image.name} 图片预览`}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <button type="button" className="artifact-image-preview__close" aria-label="关闭图片预览" autoFocus onClick={onClose}>
        <CloseIcon />
      </button>
      {image.url ? (
        // Keyed by URL so the previous image never lingers under the next one's caption.
        <img key={image.url} src={image.url} alt={image.name} />
      ) : (
        <p role={image.failed ? "alert" : "status"}>{image.status ?? "正在读取图片预览…"}</p>
      )}
      {stepTo && (
        <>
          {/* aria-disabled keeps keyboard focus on the button at either end. */}
          <button
            type="button"
            className="artifact-image-preview__step artifact-image-preview__step--previous"
            aria-label="上一张"
            aria-disabled={current === 0}
            onClick={() => { if (current > 0) stepTo(current - 1); }}
          >
            <ChevronRightIcon />
          </button>
          <button
            type="button"
            className="artifact-image-preview__step artifact-image-preview__step--next"
            aria-label="下一张"
            aria-disabled={current === last}
            onClick={() => { if (current < last) stepTo(current + 1); }}
          >
            <ChevronRightIcon />
          </button>
          <div className="artifact-image-preview__caption" aria-live="polite">
            <span className="artifact-image-preview__name">{image.name}</span>
            <span className="artifact-image-preview__position">{current + 1} / {images.length}</span>
          </div>
        </>
      )}
    </div>,
    document.body,
  );
}

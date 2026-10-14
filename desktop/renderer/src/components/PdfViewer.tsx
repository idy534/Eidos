import { useCallback, useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export interface PdfViewerProps {
  url: string;
  title: string;
  onOpenExternal?: (() => void) | undefined;
  onError?: ((error: string) => void) | undefined;
}

interface PageDimensions {
  width: number;
  height: number;
  scale: number;
}

function PdfPageItem({
  pageNumber,
  pdfDoc,
  containerWidth,
  onVisible,
}: {
  pageNumber: number;
  pdfDoc: pdfjsLib.PDFDocumentProxy;
  containerWidth: number;
  onVisible: (num: number) => void;
}) {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [dimensions, setDimensions] = useState<PageDimensions | null>(null);
  const [inViewport, setInViewport] = useState(false);

  // 1. 获取页面原始视口尺寸并计算自适应缩放
  useEffect(() => {
    let active = true;
    pdfDoc.getPage(pageNumber).then((page) => {
      if (!active) return;
      const unscaled = page.getViewport({ scale: 1 });
      const maxAvailableWidth = Math.max(320, containerWidth - 48);
      // 适度缩放：宽度不超过 920px，也不超过容器可用宽
      const targetWidth = Math.min(unscaled.width, maxAvailableWidth, 920);
      const scale = targetWidth / unscaled.width;
      const vp = page.getViewport({ scale });
      setDimensions({
        width: Math.round(vp.width),
        height: Math.round(vp.height),
        scale,
      });
    }).catch(() => {});
    return () => {
      active = false;
    };
  }, [pageNumber, pdfDoc, containerWidth]);

  // 2. 懒加载观察器：接近视口时再激活绘制，并上报当前可见页码
  useEffect(() => {
    const card = cardRef.current;
    if (!card) return;
    if (typeof IntersectionObserver === "undefined") {
      setInViewport(true);
      onVisible(pageNumber);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry?.isIntersecting) {
          setInViewport(true);
          onVisible(pageNumber);
        }
      },
      { rootMargin: "450px" },
    );
    observer.observe(card);
    return () => observer.disconnect();
  }, [pageNumber, onVisible]);

  // 3. 在 Canvas 上渲染
  useEffect(() => {
    if (!inViewport || !dimensions || !canvasRef.current) return;
    let cancelled = false;
    let renderTask: pdfjsLib.RenderTask | null = null;

    pdfDoc.getPage(pageNumber).then((page) => {
      if (cancelled || !canvasRef.current) return;
      const canvas = canvasRef.current;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const dpr = window.devicePixelRatio || 1;
      const scaledViewport = page.getViewport({ scale: dimensions.scale * dpr });

      canvas.width = scaledViewport.width;
      canvas.height = scaledViewport.height;
      canvas.style.width = `${dimensions.width}px`;
      canvas.style.height = `${dimensions.height}px`;

      renderTask = page.render({
        canvas,
        canvasContext: ctx,
        viewport: scaledViewport,
      });

      renderTask.promise.catch(() => {});
    }).catch(() => {});

    return () => {
      cancelled = true;
      if (renderTask) {
        renderTask.cancel();
      }
    };
  }, [inViewport, dimensions, pageNumber, pdfDoc]);

  return (
    <div
      ref={cardRef}
      className="pdf-page-card"
      style={{
        width: dimensions ? `${dimensions.width}px` : "100%",
        minHeight: dimensions ? `${dimensions.height}px` : "420px",
      }}
    >
      <canvas ref={canvasRef} className="pdf-page-canvas" />
    </div>
  );
}

export function PdfViewer({
  url,
  title,
  onOpenExternal,
  onError,
}: PdfViewerProps) {
  const [dimToolbar, setDimToolbar] = useState(false);
  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [loadFailed, setLoadFailed] = useState(false);
  const [containerWidth, setContainerWidth] = useState<number>(860);

  const scrollAreaRef = useRef<HTMLDivElement | null>(null);
  const dimTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  // 测量容器宽度
  useEffect(() => {
    const el = scrollAreaRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry?.contentRect?.width) {
        setContainerWidth(Math.round(entry.contentRect.width));
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // 胶囊工具栏淡化计时
  const handlePointerMove = useCallback(() => {
    setDimToolbar(false);
    if (dimTimeoutRef.current) clearTimeout(dimTimeoutRef.current);
    dimTimeoutRef.current = setTimeout(() => {
      setDimToolbar(true);
    }, 2800);
  }, []);

  // 加载 PDF 数据
  useEffect(() => {
    let cancelled = false;
    let loadingTask: pdfjsLib.PDFDocumentLoadingTask | null = null;
    setPdfDoc(null);
    setLoadFailed(false);
    setCurrentPage(1);

    async function load() {
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const buffer = await response.arrayBuffer();
        if (cancelled) return;

        const task = pdfjsLib.getDocument({
          data: new Uint8Array(buffer),
        });
        loadingTask = task;
        const doc = await task.promise;
        if (cancelled) return;

        setPdfDoc(doc);
      } catch {
        if (cancelled) return;
        setLoadFailed(true);
        onErrorRef.current?.("PDF 无法加载，请检查文件格式。");
      }
    }

    void load();

    return () => {
      cancelled = true;
      if (loadingTask) {
        loadingTask.destroy().catch(() => {});
      }
    };
  }, [url]);

  const totalPages = pdfDoc?.numPages ?? 0;
  const pageNumbers = Array.from({ length: totalPages }, (_, i) => i + 1);
  const showPageCount = pdfDoc !== null && totalPages > 1;

  return (
    <div
      className="pdf-viewer"
      title={title}
      onMouseMove={handlePointerMove}
      onPointerDown={handlePointerMove}
    >
      <div ref={scrollAreaRef} className="pdf-viewer__scroll-area">
        {!pdfDoc ? (
          <p className="pdf-viewer__status" role="status">
            {loadFailed ? "PDF 无法加载，请检查文件格式。" : "正在加载 PDF…"}
          </p>
        ) : (
          pageNumbers.map((num) => (
            <PdfPageItem
              key={num}
              pageNumber={num}
              pdfDoc={pdfDoc}
              containerWidth={containerWidth}
              onVisible={setCurrentPage}
            />
          ))
        )}
      </div>

      {(showPageCount || onOpenExternal) && <div className={`pdf-pill-toolbar${dimToolbar ? " pdf-pill-toolbar--dimmed" : ""}`}>
        {showPageCount && <span className="pdf-pill-text">
          第 {currentPage} / {totalPages} 页
        </span>}
        {onOpenExternal && (
          <>
            {showPageCount && <div className="pdf-pill-divider" />}
            <button
              type="button"
              className="pdf-pill-action-btn"
              title="在系统「预览」中打开"
              aria-label="在系统「预览」中打开"
              onClick={onOpenExternal}
            >
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path d="M11 4h5v5M10 10l6-6M15 11.5V15a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h3.5" />
              </svg>
              <span>在系统「预览」中打开</span>
            </button>
          </>
        )}
      </div>}
    </div>
  );
}

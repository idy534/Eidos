import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PdfViewer } from "./PdfViewer.js";

const { getDocument, renderPage, destroy } = vi.hoisted(() => ({
  getDocument: vi.fn(),
  renderPage: vi.fn(),
  destroy: vi.fn(),
}));

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument,
}));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    arrayBuffer: async () => new Uint8Array([37, 80, 68, 70]).buffer,
  }));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
  destroy.mockResolvedValue(undefined);
  renderPage.mockReturnValue({ promise: Promise.resolve(), cancel: vi.fn() });
  getDocument.mockReturnValue({
    promise: Promise.resolve({
      numPages: 1,
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
        render: renderPage,
      }),
    }),
    destroy,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("PdfViewer behavior", () => {
  it("renders the PDF on canvas without the Chromium embed frame", async () => {
    const onOpenExternal = vi.fn();

    const { getByTitle } = render(
      <PdfViewer
        url="eidos-preview://test/sample.pdf"
        title="sample.pdf"
        onOpenExternal={onOpenExternal}
      />,
    );

    const container = getByTitle("sample.pdf");
    expect(container).toBeInTheDocument();
    await waitFor(() => expect(renderPage).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("PDF 预览")).not.toBeInTheDocument();
    expect(container.querySelector(".pdf-pill-divider")).toBeNull();
    expect(container.querySelector("canvas")).toBeInTheDocument();
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("shows only the page count before the system open button for multipage PDFs", async () => {
    const task = getDocument();
    const doc = await task.promise;
    getDocument.mockClear();
    getDocument.mockReturnValue({ ...task, promise: Promise.resolve({ ...doc, numPages: 5 }) });
    const { container } = render(<PdfViewer url="eidos-preview://test/pages.pdf" title="pages.pdf" onOpenExternal={vi.fn()} />);
    expect(await screen.findByText(/第 \d+ \/ 5 页/)).toBeInTheDocument();
    expect(screen.queryByText(/PDF 预览/)).not.toBeInTheDocument();
    expect(container.querySelector(".pdf-pill-divider")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "在系统「预览」中打开" })).toBeInTheDocument();
  });

  it("keeps loading and failed previews outside the Chromium embed frame", async () => {
    const onError = vi.fn();
    vi.mocked(fetch).mockRejectedValue(new Error("Unable to read PDF"));
    const { container } = render(<PdfViewer url="eidos-preview://test/broken.pdf" title="broken.pdf" onError={onError} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载 PDF…");
    expect(await screen.findByText("PDF 无法加载，请检查文件格式。")).toBeInTheDocument();
    expect(onError).toHaveBeenCalledWith("PDF 无法加载，请检查文件格式。");
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("retains the loaded PDF when the error callback changes and releases it on close", async () => {
    const { rerender, unmount } = render(<PdfViewer url="eidos-preview://test/sample.pdf" title="sample.pdf" onError={vi.fn()} />);
    await waitFor(() => expect(renderPage).toHaveBeenCalledTimes(1));
    rerender(<PdfViewer url="eidos-preview://test/sample.pdf" title="sample.pdf" onError={vi.fn()} />);
    expect(getDocument).toHaveBeenCalledTimes(1);
    unmount();
    expect(destroy).toHaveBeenCalledTimes(1);
  });

  it("triggers onOpenExternal callback when clicking open in system button", async () => {
    const onOpenExternal = vi.fn();
    render(
      <PdfViewer
        url="eidos-preview://test/sample.pdf"
        title="sample.pdf"
        onOpenExternal={onOpenExternal}
      />,
    );

    const openBtn = await screen.findByTitle("在系统「预览」中打开");
    expect(openBtn).toBeInTheDocument();
    fireEvent.click(openBtn);
    expect(onOpenExternal).toHaveBeenCalledTimes(1);
  });
});

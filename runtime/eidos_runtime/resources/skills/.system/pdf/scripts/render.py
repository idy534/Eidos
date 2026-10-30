"""Render a bounded page range with bundled PDFium, one page at a time."""
import argparse
from pathlib import Path

import pypdfium2 as pdfium


def render(pdf_path, output_dir, *, dpi=100, prefix="page", image_format="png", start=1, end=None, max_dim=None):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    with pdfium.PdfDocument(str(pdf_path)) as document:
        last = len(document) if end is None else min(end, len(document))
        if start < 1 or last < start or last - start >= 100 or not 1 <= dpi <= 600:
            raise ValueError("Render 1–100 pages per call, with DPI between 1 and 600")
        width = len(str(len(document)))
        for index in range(start - 1, last):
            page = document[index]
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil()
            try:
                if max_dim:
                    image.thumbnail((max_dim, max_dim))
                path = output / f"{prefix}-{index + 1:0{width}d}.{image_format}"
                if image_format == "jpeg":
                    rgb = image.convert("RGB")
                    image.close()
                    image = rgb
                image.save(path)
                paths.append(path)
            finally:
                image.close()
                bitmap.close()
                page.close()
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("output_directory")
    parser.add_argument("--dpi", type=int, default=100)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument("--format", choices=("png", "jpeg"), default="png")
    args = parser.parse_args()
    for result in render(args.pdf, args.output_directory, dpi=args.dpi, start=args.start, end=args.end, image_format=args.format):
        print(result)

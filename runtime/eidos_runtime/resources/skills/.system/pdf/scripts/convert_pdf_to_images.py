"""Render PDF form reference images using bundled PDFium."""
import sys
from render import render


def convert(pdf_path, output_dir, max_dim=1000):
    for path in render(pdf_path, output_dir, dpi=200, max_dim=max_dim):
        number = int(path.stem.rsplit("-", 1)[1])
        destination = path.with_name(f"page_{number}.png")
        path.rename(destination)
        print(f"Saved {destination}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: convert_pdf_to_images.py INPUT.pdf OUTPUT_DIRECTORY")
    convert(sys.argv[1], sys.argv[2])
